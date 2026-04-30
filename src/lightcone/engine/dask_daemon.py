# mypy: disable-error-code="no-untyped-call"
"""Session-scoped Dask scheduler.

One scheduler per *execution context*. On a workstation that's the
project; inside a SLURM allocation it's the allocation (so workers
spawned via ``srun`` are reused across every ``lc run`` in the
allocation, not respawned each time). The key:

    slurm-<JID>            if SLURM_JOB_ID is set
    <project-path-hash>    otherwise

Storage is per-key under the resolved scratch root::

    <scratch>/.lightcone/dask-scheduler/<key>/
    ├── owner.lock        # flock'd by the daemon for its lifetime
    ├── spawn.lock        # serializes concurrent ensure() racers
    ├── scheduler.json    # Dask's native scheduler-file (address, …)
    ├── meta.json         # {pid, host, started_at, mode, …}
    ├── scheduler.log     # daemon stdout+stderr (detached)
    └── spill/            # worker spill / local-directory

Crash safety rests on a single primitive: ``flock`` is released by the
kernel when the holding process dies (clean exit, crash, or SIGKILL).
Liveness is therefore probed by trying to acquire the lock — never by
PID file or heartbeat.

If everything else fails, the scheduler self-shuts after ``IDLE_TIMEOUT``
of inactivity (Dask's built-in ``Scheduler.idle_timeout``); a stale
``scheduler.json`` from a SIGKILL'd daemon is detected by ``ensure``'s
TCP probe and replaced. The SessionEnd hook calls :func:`stop` for
prompt cleanup; idle-timeout is the safety net.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lightcone.engine.scratch import project_hash, resolve_scratch_root

#: How long the scheduler tolerates being idle before self-shutting.
#: Tuned to span "user steps away mid-conversation" without lingering
#: forever on an abandoned project.
IDLE_TIMEOUT = "30 minutes"

#: Cap on how long ``ensure_scheduler`` waits for the daemon to come up
#: and write ``scheduler.json``. Local boot is sub-second; SLURM boot
#: includes ``srun`` worker registration which dominates.
SPAWN_WAIT_SECONDS = 60

#: Probe budget for an existing scheduler. Two seconds is generous for
#: TCP connect on localhost or an HPC fabric, and short enough that a
#: dead-but-still-listed scheduler doesn't stall ``lc run``.
PROBE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class SchedulerDirs:
    """Per-key paths for a session-scoped scheduler."""

    root: Path
    owner_lock: Path
    spawn_lock: Path
    scheduler_file: Path
    meta_file: Path
    log_file: Path
    spill: Path


def scheduler_key(project_path: Path) -> str:
    """Identity for the scheduler's lifecycle scope.

    Inside a SLURM allocation every ``lc run`` for any project shares
    one scheduler keyed by ``SLURM_JOB_ID`` — workers spawned via
    ``srun`` are tied to the allocation and outlive a single run. On a
    laptop the natural unit is the project.
    """
    if jid := os.environ.get("SLURM_JOB_ID"):
        return f"slurm-{jid}"
    return project_hash(project_path)


def scheduler_dirs(project_path: Path) -> SchedulerDirs:
    """Resolve and create the per-key scheduler directory."""
    root = (
        resolve_scratch_root(project_path)
        / ".lightcone"
        / "dask-scheduler"
        / scheduler_key(project_path)
    )
    spill = root / "spill"
    for d in (root, spill):
        d.mkdir(parents=True, exist_ok=True)
    return SchedulerDirs(
        root=root,
        owner_lock=root / "owner.lock",
        spawn_lock=root / "spawn.lock",
        scheduler_file=root / "scheduler.json",
        meta_file=root / "meta.json",
        log_file=root / "scheduler.log",
        spill=spill,
    )


# ---------------------------------------------------------------------------
# Public API: ensure / stop
# ---------------------------------------------------------------------------


def ensure_scheduler(project_path: Path) -> str:
    """Return the address of a live session-scoped scheduler.

    Connects to an existing one if present, otherwise spawns a detached
    daemon and waits for it to come up. Idempotent and concurrent-safe:
    multiple callers race through ``spawn.lock`` and converge on the
    same scheduler.
    """
    dirs = scheduler_dirs(project_path)

    if (addr := _read_address(dirs.scheduler_file)) and _probe(addr):
        return addr

    # Slow path. Serialize spawn races on a separate flock so a
    # second ensure() doesn't double-spawn while the first is still
    # waiting for scheduler.json.
    spawn_fd = os.open(dirs.spawn_lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(spawn_fd, fcntl.LOCK_EX)

        # Re-probe: another caller may have spawned while we waited.
        if (addr := _read_address(dirs.scheduler_file)) and _probe(addr):
            return addr

        # Stale scheduler.json (daemon died) — clean before spawning so
        # a partial-state read can't return a dead address.
        for f in (dirs.scheduler_file, dirs.meta_file):
            f.unlink(missing_ok=True)

        _spawn_daemon(project_path, dirs)

        deadline = time.monotonic() + SPAWN_WAIT_SECONDS
        while time.monotonic() < deadline:
            if (addr := _read_address(dirs.scheduler_file)) and _probe(addr):
                return addr
            time.sleep(0.2)

        raise RuntimeError(
            f"Dask scheduler did not come up within {SPAWN_WAIT_SECONDS}s. "
            f"See {dirs.log_file} for daemon output."
        )
    finally:
        os.close(spawn_fd)


def stop(project_path: Path) -> bool:
    """Best-effort SIGTERM the running scheduler. Returns True if signalled.

    Quiet on every "nothing to stop" path: no meta file, malformed
    meta, dead PID, foreign PID. The SessionEnd hook calls this without
    caring about the result.
    """
    dirs = scheduler_dirs(project_path)
    try:
        meta = json.loads(dirs.meta_file.read_text())
        pid = int(meta["pid"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    return True


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _read_address(scheduler_file: Path) -> str | None:
    try:
        return str(json.loads(scheduler_file.read_text())["address"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return None


def _probe(addr: str) -> bool:
    """Cheap liveness probe: TCP connect to scheduler host:port.

    Avoids ``Client(addr)`` since that brings up an event loop for what
    should be a sub-second decision. A dead scheduler whose
    ``scheduler.json`` survived a crash will fail to connect; a live
    one accepts immediately.
    """
    try:
        u = urlparse(addr)
        if not u.hostname or not u.port:
            return False
        with socket.create_connection(
            (u.hostname, u.port), timeout=PROBE_TIMEOUT_SECONDS
        ):
            return True
    except (OSError, ValueError):
        return False


def _spawn_daemon(project_path: Path, dirs: SchedulerDirs) -> None:
    """Detach a daemon process running ``python -m`` this module."""
    log = open(dirs.log_file, "ab", buffering=0)
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "lightcone.engine.dask_daemon",
                "--project",
                str(project_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log.close()


# ---------------------------------------------------------------------------
# Daemon entrypoint (``python -m lightcone.engine.dask_daemon``)
# ---------------------------------------------------------------------------


def _serve(project_path: Path) -> int:
    """Run the long-lived scheduler. Exit 0 when it's gone."""
    dirs = scheduler_dirs(project_path)

    # Dedup: if another daemon already holds the lock, exit silently.
    # This makes Popen-twice safe — only one daemon ever runs per key.
    fd = os.open(dirs.owner_lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return 0

    in_slurm = "SLURM_JOB_ID" in os.environ

    # Write meta.json *before* starting the cluster so that as soon as
    # ``ensure`` sees ``scheduler.json`` (which Dask writes during
    # cluster construction), ``stop`` can already find the PID. Without
    # this ordering there's a window where ``ensure`` returns but
    # ``stop`` is silently a no-op. The address lives in
    # ``scheduler.json`` — no need to duplicate it here.
    dirs.meta_file.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started_at": time.time(),
                "key": scheduler_key(project_path),
                "mode": "slurm" if in_slurm else "local",
            }
        )
    )

    cluster, workers = (
        _start_slurm_cluster(dirs) if in_slurm else _start_local_cluster(dirs)
    )

    try:
        _block_until_done(cluster)
    finally:
        _shutdown(cluster, workers, dirs)

    return 0


def _start_local_cluster(
    dirs: SchedulerDirs,
) -> tuple[Any, subprocess.Popen[bytes] | None]:
    """LocalCluster sized to the host machine."""
    from dask.distributed import LocalCluster

    from lightcone.engine.dask_cluster import _detect_node_shape, _resource_dict

    shape = _detect_node_shape()
    cluster = LocalCluster(
        n_workers=1,
        threads_per_worker=shape.cpus,
        resources=_resource_dict(shape),
        dashboard_address=":0",
        local_directory=str(dirs.spill),
        scheduler_kwargs={
            "idle_timeout": IDLE_TIMEOUT,
            "scheduler_file": str(dirs.scheduler_file),
        },
        silence_logs=logging.WARNING,
    )
    return cluster, None


def _start_slurm_cluster(
    dirs: SchedulerDirs,
) -> tuple[Any, subprocess.Popen[bytes] | None]:
    """In-process scheduler + one ``srun``-launched worker per node."""
    from dask.distributed import Client, LocalCluster

    from lightcone.engine.dask_cluster import (
        _detect_node_shape,
        _resources_arg,
    )

    if shutil.which("dask") is None:
        raise RuntimeError(
            "`dask` CLI is not on PATH inside the SLURM allocation. "
            "Install lightcone-cli (and its `distributed` dep) into "
            "the environment activated by your sbatch/salloc."
        )

    shape = _detect_node_shape()
    nnodes = int(os.environ.get("SLURM_NNODES") or 1)

    # Bind the scheduler to a hostname workers on remote nodes can
    # reach. Default 127.0.0.1 silently fails wait_for_workers.
    scheduler_host = os.environ.get("SLURMD_NODENAME") or socket.gethostname()
    cluster = LocalCluster(
        n_workers=0,
        host=scheduler_host,
        dashboard_address=":0",
        local_directory=str(dirs.spill),
        scheduler_kwargs={
            "idle_timeout": IDLE_TIMEOUT,
            "scheduler_file": str(dirs.scheduler_file),
        },
        silence_logs=logging.WARNING,
    )
    addr = cluster.scheduler_address

    worker_cmd = [
        "srun",
        f"--ntasks={nnodes}",
        "--ntasks-per-node=1",
        "dask",
        "worker",
        addr,
        "--nthreads",
        str(shape.cpus),
        "--nworkers",
        "1",
        "--resources",
        _resources_arg(shape),
        "--no-dashboard",
        "--no-nanny",
        "--local-directory",
        str(dirs.spill),
    ]
    worker_env = dict(os.environ)
    worker_env.setdefault("DASK_LOGGING__DISTRIBUTED", "warning")
    workers = subprocess.Popen(worker_cmd, env=worker_env)

    client = Client(addr)
    try:
        client.wait_for_workers(n_workers=nnodes, timeout=120)
    finally:
        client.close()
    return cluster, workers


def _block_until_done(cluster: Any) -> None:
    """Sleep until the cluster shuts itself down or we're SIGTERM'd."""
    stopping = {"flag": False}

    def _on_term(signum: int, frame: object) -> None:
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    # Idle-timeout closes the scheduler from inside Dask; we observe it
    # via ``cluster.status``. Polling at 2s is invisible in human-time
    # and bounded against a 30-minute idle window.
    while not stopping["flag"]:
        status = getattr(cluster, "status", None)
        if status is not None and str(status).rsplit(".", maxsplit=1)[-1] != "running":
            break
        time.sleep(2)


def _shutdown(
    cluster: Any,
    workers: subprocess.Popen[bytes] | None,
    dirs: SchedulerDirs,
) -> None:
    """Gracefully retire workers, close the cluster, clean up files."""
    from dask.distributed import Client

    address = getattr(cluster, "scheduler_address", None)
    if workers is not None and address:
        # Ask the scheduler to retire workers so each ``dask worker``
        # exits cleanly. SIGTERM-ing srun directly prints noisy
        # "forcing job termination" lines on every clean shutdown.
        try:
            client = Client(address, timeout="10s")
            try:
                client.retire_workers(close_workers=True, remove=True)
            finally:
                client.close()
        except Exception:
            pass
        try:
            workers.wait(timeout=20)
        except subprocess.TimeoutExpired:
            workers.terminate()
            try:
                workers.wait(timeout=10)
            except subprocess.TimeoutExpired:
                workers.kill()
                workers.wait()

    try:
        cluster.close()
    except Exception:
        pass

    # Best-effort cleanup. Stale files don't affect correctness — the
    # next ensure() probes liveness and rewrites — but keeping the dir
    # tidy is a UX nicety.
    for f in (dirs.scheduler_file, dirs.meta_file):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lightcone.engine.dask_daemon",
        description="Run a session-scoped Dask scheduler. Internal entrypoint; "
        "users invoke this indirectly via `lc run` and `lc dask stop`.",
    )
    parser.add_argument(
        "--project", required=True, type=Path, help="Project root path."
    )
    args = parser.parse_args(argv)
    return _serve(args.project)


if __name__ == "__main__":
    raise SystemExit(_main())
