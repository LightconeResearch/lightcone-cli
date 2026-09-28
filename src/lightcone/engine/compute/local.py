"""Manage local allocations through validated OS process identities."""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psutil

from lightcone.engine.compute.model import (
    ComputeError,
    Connection,
    Identity,
    LaunchPlan,
    Offer,
    Request,
    Resources,
    Snapshot,
    UnavailableOfferError,
    positive_int,
    validate_name,
)
from lightcone.engine.compute.runtime import (
    DEFAULT_CONNECTION_ROOT,
    NOT_STARTED,
    configured_directory,
    open_client,
    private_directory,
    read_private_json,
    write_private_json,
)

_OWNER_MODULE = "lightcone.engine.compute.local_runtime"
_STOP_GRACE = 3.0
_RETIRED = "ended.json"


def _boot_identity() -> str:
    try:
        if sys.platform == "linux":
            value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        elif sys.platform == "darwin":
            value = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=5,
            ).stdout.strip()
        else:
            raise ComputeError("local allocation identity requires Linux or macOS")
        return str(UUID(value))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ComputeError("cannot verify this host's boot identity") from exc


class LocalProvider:
    """Allocate one cooperative Dask execution node on the current host."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        root = connection.launch.get("connection_root", DEFAULT_CONNECTION_ROOT)
        if not isinstance(root, str) or not root or any(ord(c) < 32 for c in root):
            raise ComputeError("local connection_root must be a nonempty path string")
        self.root = configured_directory(Path(root)) / connection.namespace

    def plan(self, offer: Offer, request: Request) -> LaunchPlan:
        """Validate a one-node local offer without creating allocation files."""
        if os.name != "posix":
            raise ComputeError("local allocations require POSIX process sessions and signals")
        if request.num_nodes != 1:
            raise UnavailableOfferError("a local allocation provides exactly one execution node")
        if self.connection.context not in ("", socket.gethostname()):
            raise UnavailableOfferError("this local connection belongs to a different host")
        allowed = {"connection_root", "scratch_root", "python", "task_slots_per_node"}
        unknown = self.connection.launch.keys() - allowed
        if unknown or offer.config:
            raise ComputeError(
                "local offers support only connection_root, scratch_root, "
                "python, and task_slots_per_node"
            )
        for name in ("python", "scratch_root"):
            if name in self.connection.launch:
                value = self.connection.launch[name]
                if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
                    raise ComputeError(f"local {name} must be a nonempty path string")
        slots = positive_int(
            self.connection.launch.get("task_slots_per_node", offer.resources.cpus),
            "task_slots_per_node",
        )
        if slots > offer.resources.cpus:
            raise ComputeError("task_slots_per_node exceeds the offered CPU envelope")
        from dask.system import CPU_COUNT
        from distributed.system import MEMORY_LIMIT

        if offer.resources.cpus > CPU_COUNT or offer.resources.memory_bytes > MEMORY_LIMIT:
            raise UnavailableOfferError("the local offer exceeds this host's CPU or RAM capacity")
        seconds = request.seconds if request.seconds is not None else offer.time.default_seconds
        if seconds <= 0 or seconds > offer.time.max_seconds:
            raise ComputeError("local allocations require a finite time within the offer's limit")
        python = Path(self.connection.launch.get("python", sys.executable)).expanduser()
        scratch = configured_directory(
            Path(self.connection.launch.get("scratch_root", tempfile.gettempdir()))
        )
        if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
            raise ComputeError("the configured local Python must be an executable absolute path")
        return LaunchPlan(
            connection=self.connection,
            offer=offer,
            request=request,
            seconds=seconds,
            details={
                "python": str(python),
                "connection_root": str(self.root),
                "scratch_root": str(scratch),
                "task_slots_per_node": slots,
                "resource_enforcement": "cooperative; no exclusive CPU or RAM reservation",
                "termination_grace_seconds": _STOP_GRACE,
            },
        )

    def launch(self, plan: LaunchPlan) -> Identity:
        """Start a detached allocation owner and retain its immutable OS identity."""
        if plan.connection != self.connection or plan.num_nodes != 1:
            raise ComputeError("local launch plan belongs to a different connection or node count")
        if plan.name is not None:
            validate_name(plan.name)
        boot = _boot_identity()
        token = uuid4().hex
        directory = private_directory(self.root / token, create=True)
        scratch = private_directory(Path(plan.details["scratch_root"]) / f"lc-{token}", create=True)
        started = time.monotonic()
        write_private_json(
            directory / "launch.json",
            {
                "deadline": started + plan.seconds,
                "task_slots": plan.details["task_slots_per_node"],
                "scratch": str(scratch),
                "identity": "",
            },
        )
        process: subprocess.Popen[bytes] | None = None
        identity: Identity | None = None
        try:
            # This allocation outlives a command; the ordinary run-to-completion
            # subprocess seam cannot own it. Logs are discarded rather than grow.
            process = subprocess.Popen(
                [plan.details["python"], "-P", "-m", _OWNER_MODULE, str(directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            identity = Identity(
                namespace=self.connection.namespace, native_id=str(process.pid), token=token,
                host=socket.gethostname(),
                name=plan.name or "",
            )
            record = {
                "identity": identity.encode(),
                "pid": process.pid,
                "uid": os.getuid(),
                "boot": boot,
                "host": identity.host,
                "cpus": plan.resources.cpus,
                "memory": plan.resources.memory_bytes,
            }
            write_private_json(directory / "identity.json", record)
            # The child waits for this file before publishing its TLS connection.
            write_private_json(
                directory / "launch.json",
                {
                    "deadline": started + plan.seconds,
                    "task_slots": plan.details["task_slots_per_node"],
                    "scratch": str(scratch),
                    "identity": identity.encode(),
                },
            )
            return identity
        except Exception as exc:
            if process is not None:
                # The unreturned child is still our direct Popen child; no file
                # lookup or stale PID is needed to identify this failed launch.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=_STOP_GRACE)
            if identity is None:
                # Publication is the discovery boundary. A failed spawn has no
                # allocation to retain, and these are the only files we wrote.
                try:
                    (directory / "launch.json").unlink(missing_ok=True)
                    directory.rmdir()
                    scratch.rmdir()
                except OSError:
                    # Discovery ignores unpublished directories even if cleanup
                    # is interrupted or the filesystem becomes unavailable.
                    pass
            raise ComputeError(
                f"cannot start the local allocation: {exc}",
                cluster_id=identity.encode() if identity is not None else None,
            ) from exc

    def _directory(self, identity: Identity) -> Path:
        if (
            identity.namespace != self.connection.namespace
            or not re.fullmatch(r"[0-9a-f]{32}", identity.token)
            or not re.fullmatch(r"[1-9][0-9]*", identity.native_id)
        ):
            raise ComputeError("this local allocation does not belong to this connection")
        return private_directory(self.root / identity.token)

    def _record(self, identity: Identity) -> tuple[Path, dict[str, Any]]:
        directory = self._directory(identity)
        record = read_private_json(directory / "identity.json")
        if (
            record.get("identity") != identity.encode()
            or record.get("host") != identity.host
            or record.get("pid") != int(identity.native_id)
            or record.get("uid") != os.getuid()
        ):
            raise ComputeError("the private locator does not match this local allocation identity")
        positive_int(record.get("cpus"), "recorded local cpus")
        positive_int(record.get("memory"), "recorded local memory")
        return directory, record

    def _process(
        self, identity: Identity, directory: Path, record: dict[str, Any]
    ) -> psutil.Process | None:
        if record.get("boot") != _boot_identity():
            raise ComputeError(
                "the local allocation belongs to a different host or boot; "
                "inspect it from its launch host",
                cluster_id=identity.encode(),
            )
        try:
            process = psutil.Process(int(identity.native_id))
            if process.status() == psutil.STATUS_ZOMBIE:
                return None
            if process.uids().real != os.getuid():
                return None
            argv = process.cmdline()
            if not argv:
                # Linux clears argv during exit before publishing zombie state.
                # Never signal this transiently unverifiable process.
                try:
                    process.wait(timeout=0.1)
                    return None
                except psutil.TimeoutExpired:
                    raise ComputeError("the local process identity is unavailable") from None
            if (
                len(argv) != 5
                or argv[1:] != ["-P", "-m", _OWNER_MODULE, str(directory)]
                or os.getpgid(process.pid) != process.pid
                or os.getsid(process.pid) != process.pid
            ):
                # This PID now identifies another process, not this allocation.
                return None
            # The exact command includes this launch's random token directory.
            # PID reuse cannot match another allocation, and unlike wall-clock
            # create_time this identity survives clock corrections and renames.
            return process
        except (psutil.NoSuchProcess, ProcessLookupError):
            return None
        except psutil.AccessDenied as exc:
            # On macOS, command-line inspection can fail during exit before the
            # process table reports death. Confirm exit; never infer it merely
            # from a permission error on an otherwise live process.
            try:
                psutil.Process(int(identity.native_id)).wait(timeout=0.1)
                return None
            except psutil.NoSuchProcess:
                return None
            except (psutil.TimeoutExpired, psutil.AccessDenied):
                pass
            raise ComputeError("cannot verify the local allocation process identity") from exc

    def discover(self) -> Sequence[Snapshot]:
        """Find active local owners by checking their private locators against the OS."""
        if not self.root.exists():
            return []
        private_directory(self.root)
        boot = _boot_identity()
        snapshots = []
        for directory in sorted(self.root.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{32}", directory.name):
                continue
            if (directory / _RETIRED).exists():
                # Verified ended and retired: nothing is left to observe.
                continue
            if not (directory / "identity.json").exists():
                # Launch publishes the identity atomically after Popen. A
                # launcher can still be starting, or have failed before that.
                continue
            record = read_private_json(directory / "identity.json")
            if record.get("boot") != boot:
                # A shared home can contain locators from another machine.
                # This host cannot observe whether those allocations have ended.
                continue
            identity = Identity.decode(str(record.get("identity", "")))
            snapshot = self.inspect(identity)
            if snapshot.phase == "ended":
                self._retire(directory)
            else:
                snapshots.append(snapshot)
        return snapshots

    def _retire(self, directory: Path) -> None:
        """Remove an ended allocation's credentials and scratch, keeping its record.

        The identity record (and any startup error) still answers an
        inspection by full ID, while the marker lets discovery skip the
        allocation without reading it. Best effort: an allocation left
        half-retired is still ended, and the next discovery finishes the job.
        """
        try:
            if (launch := directory / "launch.json").exists():
                scratch = Path(str(read_private_json(launch).get("scratch", "")))
                # Only this allocation's own scratch, never any path a record names.
                if scratch.name == f"lc-{directory.name}":
                    shutil.rmtree(scratch, ignore_errors=True)
            for name in (
                "tls-key.pem", "tls-cert.pem", "scheduler.json", "connection.json", "launch.json",
            ):
                (directory / name).unlink(missing_ok=True)
            write_private_json(directory / _RETIRED, {})
        except (OSError, ComputeError):
            pass

    def inspect(self, identity: Identity) -> Snapshot:
        """Report native process existence without requiring a reachable scheduler."""
        directory, record = self._record(identity)
        process = self._process(identity, directory, record)
        try:
            native_state = process.status() if process is not None else "not-running"
        except psutil.NoSuchProcess:
            process = None
            native_state = "not-running"
        reason = "CPU and RAM budgets are cooperative, not exclusive OS reservations"
        if process is None and (directory / "error.json").exists():
            reason = str(read_private_json(directory / "error.json").get("error", ""))
        return Snapshot(
            identity=identity,
            phase="active" if process is not None else "ended",
            resources=Resources.from_bytes(
                cpus=int(record["cpus"]), memory_bytes=int(record["memory"]),
            ),
            num_nodes=1,
            evidence="configured",
            reason=reason,
            native_state=native_state,
        )

    @contextmanager
    def connect(self, identity: Identity, *, timeout: float = 10) -> Iterator[Any]:
        """Borrow an authenticated client without taking ownership of the cluster."""
        directory, record = self._record(identity)
        if self._process(identity, directory, record) is None:
            raise ComputeError("the local allocation has ended", cluster_id=identity.encode())
        if not (directory / "connection.json").exists():
            raise ComputeError(NOT_STARTED, cluster_id=identity.encode())
        connection = read_private_json(directory / "connection.json")
        if connection.get("identity") != identity.encode():
            raise ComputeError("the local scheduler connection belongs to a different allocation")
        client = open_client(directory, str(connection.get("scheduler_id", "")), timeout=timeout)
        try:
            if self._process(identity, directory, record) is None:
                raise ComputeError("the local allocation ended during connection")
            yield client
        finally:
            client.close(timeout=min(timeout, 5))

    def terminate(self, identity: Identity) -> None:
        """Terminate the validated allocation session even if Dask is wedged."""
        directory, record = self._record(identity)
        self._stop(identity, directory, record)
        self._retire(directory)

    def _stop(self, identity: Identity, directory: Path, record: dict[str, Any]) -> None:
        process = self._process(identity, directory, record)
        if process is None:
            return
        members = []
        for member in psutil.process_iter():
            try:
                if (
                    member.uids().real == os.getuid()
                    and os.getsid(member.pid) == process.pid
                ):
                    # Capture each birth identity while the owner still proves
                    # this session is ours. psutil's signal methods check reuse.
                    member.create_time()
                    members.append(member)
            except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
                continue
        for member in members:
            try:
                member.terminate()
            except psutil.NoSuchProcess:
                pass
        for escalation in (False, True):
            from lightcone.engine.sandbox.processes import has_custodian

            grace = max(_STOP_GRACE, 16) if has_custodian(members) else _STOP_GRACE
            deadline = time.monotonic() + grace
            while members and time.monotonic() < deadline:
                living = []
                for member in members:
                    try:
                        if member.is_running() and member.status() != psutil.STATUS_ZOMBIE:
                            living.append(member)
                    except psutil.NoSuchProcess:
                        pass
                members = living
                if members:
                    time.sleep(0.05)
            if not members:
                return
            if escalation:
                raise ComputeError(
                    "local allocation processes have not exited after SIGKILL",
                    cluster_id=identity.encode(),
                )
            process = self._process(identity, directory, record)
            if process is not None:
                # Commands have separate groups inside this session. The
                # live owner establishes custody of newly created members.
                from lightcone.engine.sandbox.processes import members as session_members

                for member in session_members(session=process.pid):
                    try:
                        member.kill()
                    except psutil.NoSuchProcess:
                        pass
            for member in members:
                try:
                    # The owner may have exited first. Never signal its old PGID
                    # without an owner; use the captured process identities.
                    member.kill()
                except psutil.NoSuchProcess:
                    pass
