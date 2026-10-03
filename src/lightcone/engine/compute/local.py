"""Manage local allocations through validated OS process identities."""

from __future__ import annotations

import fcntl
import os
import re
import shlex
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

from lightcone.engine.compute.catalog import cuda_device_count, local_disabled_reason
from lightcone.engine.compute.model import (
    ComputeError,
    Identity,
    LaunchPlan,
    Offer,
    Request,
    Resources,
    Snapshot,
    UnavailableOfferError,
    config_text,
    positive_int,
    validate_name,
)
from lightcone.engine.compute.runtime import (
    NOT_STARTED,
    configured_directory,
    open_client,
    private_directory,
    read_private_json,
    write_private_json,
)

# The owner's command after its interpreter; launch and both identity checks share it.
_OWNER_ARGS = ("-P", "-m", "lightcone.engine.compute.local_runtime")
_STOP_GRACE = 3.0
_RETIRED = "ended.json"


def _above_stdio(descriptor: int) -> int:
    """Keep inherited control descriptors clear of Popen's stdio redirections."""
    if descriptor < 3:
        duplicate = fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)
        os.close(descriptor)
        return duplicate
    return descriptor


def _running_owners() -> list[tuple[int, Path]]:
    """Find this user's live allocation owners in this host's process table.

    An owner leads its own session and runs ``python -P -m <owner module>
    <directory>``. The process table spans every catalog and connection root,
    and asking it needs no file lock, which shared home filesystems such as
    NERSC's refuse.

    Returns:
        Each owner's PID and allocation directory.

    Raises:
        ComputeError: If the process table cannot be read at all.
    """
    try:
        processes = list(psutil.process_iter())
    except (psutil.Error, OSError) as exc:
        raise ComputeError(f"cannot read this host's process table: {exc}") from exc
    owners = []
    for process in processes:
        try:
            if process.uids().real != os.getuid():
                continue
            argv = process.cmdline()
            # An exiting owner has no command line left, and a forked worker
            # keeps the owner's command but does not lead its session.
            if (
                len(argv) == len(_OWNER_ARGS) + 2
                and tuple(argv[1:-1]) == _OWNER_ARGS
                and os.getsid(process.pid) == process.pid
            ):
                owners.append((process.pid, Path(argv[-1])))
        except (psutil.Error, OSError):
            continue
    return owners


def _refuse_a_second_allocation() -> None:
    """Refuse a launch while this user already runs a local allocation here.

    Launches that overlap can both pass; that race is accepted rather than
    closed with a lock.

    Raises:
        ComputeError: If an owner is running, naming how to stop it.
    """
    owners = _running_owners()
    if not owners:
        return
    pid, directory = owners[0]
    message = "a local cluster is already running or starting for this user on this machine"
    path = directory / "identity.json"
    if directory.is_dir() and not path.exists():
        raise ComputeError(
            f"{message}; its launcher (owner process {pid}) is publishing the allocation "
            "identity; retry shortly"
        )
    try:
        record = read_private_json(path)
        cluster_id = record.get("identity")
        if not isinstance(cluster_id, str) or not cluster_id:
            raise ComputeError(f"compute connection file has no identity: {path}")
    except ComputeError as exc:
        # A lost or damaged record never recovers by waiting; the process can still be stopped.
        raise ComputeError(
            f"{message}; owner process {pid} has no usable allocation record ({exc}); "
            f"stop it with `kill {pid}`"
        ) from exc
    catalog = record.get("catalog")
    command = (
        f"LC_COMPUTE_CONFIG={shlex.quote(catalog)}" if isinstance(catalog, str)
        else "env -u LC_COMPUTE_CONFIG"
    ) + " lc compute down " + shlex.quote(cluster_id)
    raise ComputeError(
        f"{message}; allocation {cluster_id} uses connection_root "
        f"{str(directory.parent.parent)!r}; using that launch catalog, stop it with "
        f"`{command}`"
    )


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

    def __init__(self, root: Path) -> None:
        # The catalog resolves the connection root; allocations live under local/.
        self.root = root
        self.allocations = root / "local"

    def plan(self, offer: Offer, request: Request) -> LaunchPlan:
        """Validate a one-node local offer without creating allocation files."""
        if reason := local_disabled_reason():
            raise UnavailableOfferError(reason)
        if os.name != "posix":
            raise ComputeError("local allocations require POSIX process sessions and signals")
        if request.num_nodes != 1:
            raise UnavailableOfferError("a local allocation provides exactly one execution node")
        config = offer.config
        if config.keys() - {"scratch_root", "python", "task_slots_per_node"}:
            raise ComputeError(
                "local offer config supports only scratch_root, python, and task_slots_per_node"
            )
        for name in ("python", "scratch_root"):
            if name in config:
                config_text(config[name], f"local {name}")
        slots = positive_int(
            config.get("task_slots_per_node", offer.resources.cpus), "task_slots_per_node",
        )
        if slots > offer.resources.cpus:
            raise ComputeError("task_slots_per_node exceeds the offered CPU envelope")
        from dask.system import CPU_COUNT
        from distributed.system import MEMORY_LIMIT

        if offer.resources.cpus > CPU_COUNT or offer.resources.memory_bytes > MEMORY_LIMIT:
            raise UnavailableOfferError("the local offer exceeds this host's CPU or RAM capacity")
        mask = ""
        if offer.resources.gpus:
            if sys.platform != "linux":
                raise UnavailableOfferError("local GPU allocations require Linux")
            if (visible := cuda_device_count()) < offer.resources.gpus:
                raise UnavailableOfferError(
                    f"the local offer has {offer.resources.gpus} GPUs, but "
                    f"CUDA_VISIBLE_DEVICES exposes {visible} on this host"
                )
            mask = os.environ["CUDA_VISIBLE_DEVICES"]
        seconds = request.seconds if request.seconds is not None else offer.time.default_seconds
        limit = offer.time.max_seconds
        if seconds is not None and limit is not None and seconds > limit:
            raise ComputeError("the requested time exceeds the local offer's maximum")
        try:
            python = Path(config.get("python", sys.executable)).expanduser()
        except RuntimeError as exc:
            raise ComputeError(f"cannot expand the configured local Python: {exc}") from exc
        scratch = configured_directory(Path(config.get("scratch_root", tempfile.gettempdir())))
        if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
            raise ComputeError("the configured local Python must be an executable absolute path")
        return LaunchPlan(
            offer=offer,
            request=request,
            seconds=seconds,
            details={
                "python": str(python),
                "connection_root": str(self.root),
                "scratch_root": str(scratch),
                "task_slots_per_node": slots,
                "cuda_visible_devices": mask,
                "cuda_device_order": os.environ.get("CUDA_DEVICE_ORDER"),
                "resource_enforcement": "cooperative; no exclusive CPU, RAM, or GPU reservation",
                "termination_grace_seconds": _STOP_GRACE,
            },
        )

    def launch(self, plan: LaunchPlan) -> Identity:
        """Start a detached allocation owner and retain its immutable OS identity."""
        if reason := local_disabled_reason():
            raise ComputeError(reason)
        if (
            plan.offer.provider != "local"
            or plan.details["connection_root"] != str(self.root)
            or plan.num_nodes != 1
        ):
            raise ComputeError(
                "local launch plan belongs to a different provider, connection root or node count"
            )
        if plan.name is not None:
            validate_name(plan.name)
        _refuse_a_second_allocation()
        boot = _boot_identity()
        token = uuid4().hex
        directory: Path | None = None
        scratch: Path | None = None
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        identity: Identity | None = None
        published = False
        startup_read: int | None = None
        startup_write: int | None = None
        environment = {**os.environ, "CUDA_VISIBLE_DEVICES": plan.details["cuda_visible_devices"]}
        if plan.details["cuda_device_order"] is None:
            environment.pop("CUDA_DEVICE_ORDER", None)
        else:
            environment["CUDA_DEVICE_ORDER"] = plan.details["cuda_device_order"]
        try:
            directory = private_directory(self.allocations / token, create=True)
            scratch = private_directory(
                Path(plan.details["scratch_root"]) / f"lc-{token}", create=True,
            )
            startup_read, startup_write = os.pipe()
            startup_read = _above_stdio(startup_read)
            startup_write = _above_stdio(startup_write)
            launch = {
                "deadline": None if plan.seconds is None else started + plan.seconds,
                "idle_timeout": plan.idle_seconds,
                "task_slots": plan.details["task_slots_per_node"],
                "scratch": str(scratch),
                "identity": "",
                "startup_fd": startup_read,
            }
            write_private_json(directory / "launch.json", launch)
            # This allocation outlives a command; the ordinary run-to-completion
            # subprocess seam cannot own it. Logs are discarded rather than grow.
            process = subprocess.Popen(
                [plan.details["python"], *_OWNER_ARGS, str(directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                pass_fds=(startup_read,),
                env=environment,
            )
            os.close(startup_read)
            startup_read = None
            identity = Identity(
                provider="local", native_id=str(process.pid), token=token,
                host=socket.gethostname(),
                name=plan.name or "",
            )
            catalog = os.environ.get("LC_COMPUTE_CONFIG")
            record = {
                "identity": identity.encode(),
                "pid": process.pid,
                "uid": os.getuid(),
                "boot": boot,
                "host": identity.host,
                "cpus": plan.resources.cpus,
                "memory": plan.resources.memory_bytes,
                "gpus": plan.resources.gpus,
                "accelerator_name": plan.resources.accelerator_name or "GPU",
                # A refused launch names the catalog that reaches this allocation.
                "catalog": None if catalog is None else str(Path(catalog).expanduser().absolute()),
            }
            write_private_json(directory / "identity.json", record)
            published = True
            launch["identity"] = identity.encode()
            write_private_json(directory / "launch.json", launch)
            # EOF without this byte means the launcher died before publication,
            # including SIGKILL, which no Python exception handler can clean up.
            os.write(startup_write, b"1")
            return identity
        except BaseException as exc:
            if process is not None:
                # The unreturned child is still our direct Popen child; no file
                # lookup or stale PID is needed to identify this failed launch.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=_STOP_GRACE)
            if published and directory is not None:
                self._retire(directory)
            else:
                # No published identity exists to retain or inspect.
                if directory is not None:
                    shutil.rmtree(directory, ignore_errors=True)
                if scratch is not None:
                    shutil.rmtree(scratch, ignore_errors=True)
            if not isinstance(exc, Exception):
                raise
            raise ComputeError(
                f"cannot start the local allocation: {exc}",
                cluster_id=identity.encode() if published and identity is not None else None,
            ) from exc
        finally:
            for descriptor in (startup_read, startup_write):
                if descriptor is not None:
                    os.close(descriptor)

    def _directory(self, identity: Identity) -> Path:
        if (
            identity.provider != "local"
            or not re.fullmatch(r"[0-9a-f]{32}", identity.token)
            or not re.fullmatch(r"[1-9][0-9]*", identity.native_id)
        ):
            raise ComputeError("this cluster ID does not identify a local allocation")
        return private_directory(self.allocations / identity.token)

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
        record.setdefault("gpus", 0)
        record.setdefault("accelerator_name", "GPU")
        if type(record.get("gpus")) is not int or record["gpus"] < 0:
            raise ComputeError("recorded local gpus must be a nonnegative integer")
        if not isinstance(record.get("accelerator_name"), str) or not record["accelerator_name"]:
            raise ComputeError("recorded local accelerator_name must be a nonempty string")
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
                argv[1:] != [*_OWNER_ARGS, str(directory)]
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
        if not self.allocations.exists():
            return []
        private_directory(self.allocations)
        boot = _boot_identity()
        snapshots = []
        for directory in sorted(self.allocations.iterdir()):
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
        reason = "CPU, RAM, and GPU budgets are cooperative, not exclusive OS reservations"
        if process is None and (directory / "error.json").exists():
            reason = str(read_private_json(directory / "error.json").get("error", ""))
        return Snapshot(
            identity=identity,
            phase="active" if process is not None else "ended",
            resources=Resources.from_bytes(
                cpus=int(record["cpus"]), memory_bytes=int(record["memory"]),
                gpus=record["gpus"],
                accelerator_name=record["accelerator_name"],
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
        """Terminate the validated allocation process group even if Dask is wedged."""
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
