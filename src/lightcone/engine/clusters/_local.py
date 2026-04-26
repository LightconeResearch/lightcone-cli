"""Laptop / dev-machine cluster backend.

When you're not on SLURM (no ``$SLURM_JOB_ID`` set, no ``--target X``),
``lc cluster start`` falls through to this backend.  It runs:

* a ``distributed.LocalCluster`` (scheduler + N worker subprocesses on
  this machine), spawned in a detached daemon so it outlives ``lc
  cluster start``, with the scheduler address written to a project-local
  scheduler-file;
* a Postgres daemon (via :mod:`lightcone.engine.clusters._pg`) backing
  Dagster's persistent storage.

The state file is the same project-local
``<project>/.lightcone/cluster.state.json`` that the SLURM modes use,
so ``lc cluster status`` / ``lc run`` / ``lc dev`` look the same here
as anywhere.

Lifecycle:

* :func:`start_local_cluster` — spawn LocalCluster + PG, write state.
* :func:`stop_local_cluster` — terminate LocalCluster's daemon (kills
  the scheduler + workers as a process group) and call ``stop_pg``.
* :func:`local_cluster_info` — read state + probe liveness for ``status``.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

from lightcone.engine.clusters._common import (
    ClusterInfo,
    ClusterRecord,
    ClusterSpec,
    WorkerPool,
    clear_project_state,
    read_project_state,
    read_scheduler_address,
    write_project_state,
)
from lightcone.engine.clusters._pg import start_pg, stop_pg

logger = logging.getLogger(__name__)


def _is_pid_alive(pid: int) -> bool:
    """Cheap aliveness check — signal 0 doesn't deliver but probes existence."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _wait_for_scheduler_file(scheduler_file: Path, timeout_s: float = 30.0) -> str:
    """Poll *scheduler_file* until it has a parseable address; return it."""
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if scheduler_file.exists():
            address = read_scheduler_address(str(scheduler_file))
            if address:
                return address
        time.sleep(0.25)
    raise TimeoutError(
        f"LocalCluster scheduler-file {scheduler_file} did not appear within "
        f"{timeout_s:.0f}s — check the daemon log alongside it."
    )


def start_local_cluster(project_root: Path) -> ClusterInfo:
    """Spawn a LocalCluster + Postgres for this project.

    Idempotent: if a local cluster is already active for the project,
    returns its info instead of starting a new one.
    """
    project_root = project_root.resolve()
    existing = read_project_state(project_root)
    if existing is not None:
        info = local_cluster_info(project_root)
        if info and info.state == "RUNNING":
            logger.info(
                "Local cluster already active for this project (pid %s).",
                existing.process_pids[0] if existing.process_pids else "?",
            )
            return info
        # Stale; sweep before starting fresh.
        clear_project_state(project_root)

    lightcone_dir = project_root / ".lightcone"
    lightcone_dir.mkdir(parents=True, exist_ok=True)
    scheduler_file = lightcone_dir / "scheduler-local.json"
    if scheduler_file.exists():
        scheduler_file.unlink()

    log_dir = project_root / "results" / ".slurm"
    log_dir.mkdir(parents=True, exist_ok=True)
    daemon_log = log_dir / "lc-cluster-local.log"

    # Inline daemon script: bring up LocalCluster, write scheduler-file,
    # block until killed.  Detached via start_new_session so it survives
    # the launching `lc cluster start` exiting.
    daemon_src = textwrap.dedent(
        f"""\
        import json, signal, sys, time
        from distributed import LocalCluster

        cluster = LocalCluster()
        with open({str(scheduler_file)!r}, "w") as f:
            json.dump({{"address": cluster.scheduler_address}}, f)
        print("LocalCluster up:", cluster.scheduler_address, flush=True)

        # Block forever until SIGTERM.  LocalCluster's atexit handler
        # tears down workers and the scheduler cleanly.
        def _stop(signum, frame):
            sys.exit(0)
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        while True:
            time.sleep(3600)
        """
    )
    daemon = subprocess.Popen(
        [sys.executable, "-c", daemon_src],
        stdout=open(daemon_log, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=str(project_root),
    )

    try:
        scheduler_address = _wait_for_scheduler_file(scheduler_file)
    except Exception:
        # Daemon failed; reap and re-raise with log tail for diagnostics.
        try:
            os.kill(daemon.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        log_tail = daemon_log.read_text()[-1500:] if daemon_log.exists() else ""
        raise RuntimeError(
            f"LocalCluster daemon failed to come up.\n"
            f"--- {daemon_log} ---\n{log_tail}"
        )

    postgres_url = start_pg(project_root)

    record = ClusterRecord(
        name="_local",
        type="slurm",  # substrate-neutral RuntimeState fields work fine here
        job_id=str(daemon.pid),
        site="_local",
        submitted_at=datetime.now(UTC).isoformat(),
        walltime_seconds=0,
        scheduler_file=str(scheduler_file),
        mode="local",
        process_pids=[daemon.pid],
        postgres_url=postgres_url,
        scheduler_address=scheduler_address,
    )
    write_project_state(project_root, record)
    logger.info(
        "Local cluster up (pid %d, scheduler %s).", daemon.pid, scheduler_address,
    )
    spec = _spec_for_local_cluster(record)
    return ClusterInfo(
        spec=spec, record=record, state="RUNNING",
        scheduler_address=scheduler_address,
    )


def stop_local_cluster(*, project_root: Path | None = None) -> None:
    """Tear down a local cluster: SIGTERM the daemon, stop PG, clear state."""
    project_root = (project_root or Path.cwd()).resolve()
    record = read_project_state(project_root)
    if record is None:
        logger.info("No active cluster for project — nothing to stop.")
        return
    if record.mode != "local":
        # Caller dispatched wrong; safe to no-op rather than corrupt other state.
        logger.warning(
            "stop_local_cluster called for non-local cluster '%s' (mode=%s); "
            "no-op.", record.name, record.mode,
        )
        return
    for pid in record.process_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if record.postgres_url:
        stop_pg(project_root)
    sf = Path(record.scheduler_file)
    if sf.exists():
        sf.unlink()
    clear_project_state(project_root)
    logger.info("Local cluster stopped.")


def local_cluster_info(project_root: Path) -> ClusterInfo | None:
    """Return spec + record + live state for the project's local cluster."""
    record = read_project_state(project_root)
    if record is None or record.mode != "local":
        return None
    daemon_alive = bool(record.process_pids) and _is_pid_alive(record.process_pids[0])
    state = "RUNNING" if daemon_alive else "DEAD"
    address = record.scheduler_address if daemon_alive else None
    spec = _spec_for_local_cluster(record)
    return ClusterInfo(spec=spec, record=record, state=state, scheduler_address=address)


def _spec_for_local_cluster(record: ClusterRecord) -> ClusterSpec:
    """Display-only spec for a local cluster."""
    return ClusterSpec(
        name=record.name,
        type="slurm",
        site="_local",
        account="(local)",
        qos="(local)",
        walltime="∞",
        workers=[WorkerPool(nodes=1, threads_per_node=1, memory="auto")],
        container_runtime="docker",
        scratch_root="(local)",
    )


__all__ = [
    "start_local_cluster",
    "stop_local_cluster",
    "local_cluster_info",
]
