"""Manage local allocations through validated OS process identities."""

from __future__ import annotations

import os
import re
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
from uuid import uuid4

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
)
from lightcone.engine.compute.runtime import (
    open_client,
    private_directory,
    read_private_json,
    write_private_json,
)

_OWNER_MODULE = "lightcone.engine.compute.local_runtime"
_STOP_GRACE = 3.0


def _boot_identity() -> str:
    boot_id = Path("/proc/sys/kernel/random/boot_id")
    if boot_id.exists():
        return boot_id.read_text().strip()
    return str(psutil.boot_time())


class LocalProvider:
    """Allocate one cooperative Dask execution node on the current host."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        root = connection.launch.get("connection_root", "~/.lightcone/compute")
        if not isinstance(root, str) or not root or any(ord(c) < 32 for c in root):
            raise ComputeError("local connection_root must be a nonempty path string")
        self.root = Path(root).expanduser() / connection.namespace

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

        if offer.resources.cpus > CPU_COUNT or offer.resources.memory > MEMORY_LIMIT:
            raise UnavailableOfferError("the local offer exceeds this host's CPU or RAM capacity")
        seconds = request.seconds if request.seconds is not None else offer.default_seconds
        if seconds <= 0 or seconds > offer.max_seconds:
            raise ComputeError("local allocations require a finite time within the offer's limit")
        python = Path(self.connection.launch.get("python", sys.executable)).expanduser()
        scratch = Path(
            self.connection.launch.get("scratch_root", str(Path(tempfile.gettempdir()).resolve()))
        ).expanduser()
        if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
            raise ComputeError("the configured local Python must be an executable absolute path")
        for path in (self.root, scratch):
            if not path.is_absolute() or ".." in path.parts:
                raise ComputeError("local connection and scratch roots must be absolute paths")
        return LaunchPlan(
            self.connection,
            offer,
            request,
            seconds,
            {
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
        token = uuid4().hex
        directory = private_directory(self.root / token, create=True)
        scratch = private_directory(Path(plan.details["scratch_root"]) / f"lc-{token}", create=True)
        started = time.time()
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
                [plan.details["python"], "-m", _OWNER_MODULE, str(directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            native = psutil.Process(process.pid)
            identity = Identity(
                self.connection.namespace, str(process.pid), token, socket.gethostname()
            )
            record = {
                "identity": identity.encode(),
                "pid": process.pid,
                "created": native.create_time(),
                "uid": os.getuid(),
                "boot": _boot_identity(),
                "host": socket.gethostname(),
                "cpus": plan.resources.cpus,
                "memory": plan.resources.memory,
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
            or identity.host != socket.gethostname()
            or not re.fullmatch(r"[0-9a-f]{32}", identity.token)
            or not re.fullmatch(r"[1-9][0-9]*", identity.native_id)
        ):
            raise ComputeError("this local allocation does not belong to this host/connection")
        return private_directory(self.root / identity.token)

    def _record(self, identity: Identity) -> tuple[Path, dict[str, Any]]:
        directory = self._directory(identity)
        record = read_private_json(directory / "identity.json")
        if (
            record.get("identity") != identity.encode()
            or record.get("host") != identity.host
            or record.get("pid") != int(identity.native_id)
            or record.get("uid") != os.getuid()
            or not isinstance(record.get("created"), (float, int))
        ):
            raise ComputeError("the private locator does not match this local allocation identity")
        positive_int(record.get("cpus"), "recorded local cpus")
        positive_int(record.get("memory"), "recorded local memory")
        return directory, record

    def _process(
        self, identity: Identity, directory: Path, record: dict[str, Any]
    ) -> psutil.Process | None:
        if record.get("boot") != _boot_identity():
            return None
        try:
            process = psutil.Process(int(identity.native_id))
            if process.status() == psutil.STATUS_ZOMBIE:
                return None
            if process.create_time() != record["created"]:
                return None
            if process.uids().real != os.getuid():
                raise ComputeError("the local allocation process belongs to another user")
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
                len(argv) != 4
                or argv[1:] != ["-m", _OWNER_MODULE, str(directory)]
                or os.getpgid(process.pid) != process.pid
                or os.getsid(process.pid) != process.pid
            ):
                raise ComputeError(
                    "the local allocation owner no longer matches its recorded process session"
                )
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
        snapshots = []
        for directory in sorted(self.root.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{32}", directory.name):
                continue
            if not (directory / "identity.json").exists():
                # Launch publishes the identity atomically after Popen. A
                # launcher can still be starting, or have failed before that.
                continue
            record = read_private_json(directory / "identity.json")
            identity = Identity.decode(str(record.get("identity", "")))
            snapshot = self.inspect(identity)
            if snapshot.phase != "ended":
                snapshots.append(snapshot)
        return snapshots

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
            identity,
            "active" if process is not None else "ended",
            resources=Resources(int(record["cpus"]), int(record["memory"])),
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
        """Terminate the validated allocation process group even if Dask is wedged."""
        directory, record = self._record(identity)
        process = self._process(identity, directory, record)
        if process is None:
            return
        members = []
        for member in psutil.process_iter():
            try:
                if (
                    member.uids().real == os.getuid()
                    and os.getpgid(member.pid) == process.pid
                    and os.getsid(member.pid) == process.pid
                ):
                    # Capture each birth identity while the owner still proves
                    # this session is ours. psutil's signal methods check reuse.
                    member.create_time()
                    members.append(member)
            except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
                continue
        process = self._process(identity, directory, record)
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            for member in members:
                try:
                    member.terminate()
                except psutil.NoSuchProcess:
                    pass
        for escalation in (False, True):
            deadline = time.monotonic() + _STOP_GRACE
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
                try:
                    # A live, verified owner also covers children created while
                    # stopping. Its finalizer provides the same group-wide kill.
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for member in members:
                try:
                    # The owner may have exited first. Never signal its old PGID
                    # without an owner; use the captured process identities.
                    member.kill()
                except psutil.NoSuchProcess:
                    pass
