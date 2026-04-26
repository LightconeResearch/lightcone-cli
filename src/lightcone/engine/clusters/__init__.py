"""Cluster abstraction — manage long-lived Dask clusters across substrates.

A *cluster* is a persistent rendezvous point for the project: a Dask
scheduler + workers, a Postgres daemon backing Dagster's storage, and a
state file that records "this is the live cluster for this project".

Each project has at most one active cluster at a time; the state file
lives at ``<project>/.lightcone/cluster.state.json``.  The substrate
(SLURM-sbatch / SLURM-attached / local LocalCluster) is dispatched from
the recorded :class:`~lightcone.engine.clusters._common.ClusterMode`.

User-facing entry points (``lc cluster start``, ``stop``, ``status``)
all take a *project_root* and operate on that one project's cluster.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from lightcone.engine.clusters._common import (
    ClusterInfo,
    ClusterMode,
    ClusterRecord,
    ClusterSpec,
    ClusterType,
    RuntimeState,
    WorkerPool,
    env_path_for_site,
    expand_path,
    get_cache_dir,
    get_clusters_dir,
    get_envs_dir,
    list_clusters,
    load_cluster_config,
    parse_walltime_seconds,
    project_state_path,
    read_project_state,
    read_scheduler_address,
    save_cluster_config,
    spec_from_config,
    walltime_to_slurm,
)

logger = logging.getLogger(__name__)


__all__ = [
    "ClusterInfo",
    "ClusterMode",
    "ClusterRecord",
    "ClusterSpec",
    "ClusterType",
    "RuntimeState",
    "WorkerPool",
    "env_path_for_site",
    "expand_path",
    "get_cache_dir",
    "get_clusters_dir",
    "get_envs_dir",
    "list_clusters",
    "load_cluster_config",
    "save_cluster_config",
    "parse_walltime_seconds",
    "project_state_path",
    "read_project_state",
    "read_scheduler_address",
    "spec_from_config",
    "walltime_to_slurm",
    "start_cluster",
    "stop_cluster",
    "cluster_info",
    "wait_for_scheduler",
    "tail_cluster_logs",
    "refresh_cluster_cache",
    "is_login_node",
]


def is_login_node() -> bool:
    """Return ``True`` on a recognised HPC login node (no SLURM allocation).

    Used by the dispatcher to refuse the ``local`` fall-through on login
    nodes — they're shared, idle-killed, and not appropriate execution
    environments for long-lived services.  Detection: a known site is
    set in the environment but ``$SLURM_JOB_ID`` is not.
    """
    if os.environ.get("SLURM_JOB_ID"):
        return False
    return bool(os.environ.get("NERSC_HOST") or os.environ.get("LMOD_SYSTEM_NAME"))


def start_cluster(
    *,
    target: str | None = None,
    project_root: Path | None = None,
    overrides: dict[str, Any] | None = None,
    strategy: str = "fit",
) -> ClusterInfo:
    """Bring up the project's cluster.

    Dispatches by context:

    * ``--target X`` (a configured cluster yaml) → submit via sbatch.
    * ``$SLURM_JOB_ID`` set → attach to the current SLURM allocation.
    * On a recognised login node with neither of the above → refuse,
      with a helpful pointer to ``--target`` or ``salloc``.
    * Otherwise → run a local Dask LocalCluster + Postgres in-process.
    """
    project_root = (project_root or Path.cwd()).resolve()

    if target is not None:
        config = load_cluster_config(target)
        if config is None:
            raise FileNotFoundError(
                f"No cluster target named '{target}'. "
                f"Configured: {list_clusters() or 'none'}"
            )
        cluster_type = config.get("type")
        if cluster_type != "slurm":
            raise ValueError(f"unknown cluster type {cluster_type!r}")
        from lightcone.engine.clusters._slurm import start_slurm_cluster
        return start_slurm_cluster(
            target, config, overrides or {},
            project_root=project_root, strategy=strategy,
        )

    if os.environ.get("SLURM_JOB_ID"):
        from lightcone.engine.clusters._slurm import attach_to_allocation
        return attach_to_allocation(project_root=project_root)

    if is_login_node():
        raise RuntimeError(
            "Refusing to start a local cluster on a login node — long-lived "
            "services aren't appropriate there. Either:\n"
            "  • specify a target:  lc cluster start --target <name>\n"
            "  • or grab an allocation first:  salloc … && lc cluster start"
        )

    from lightcone.engine.clusters._local import start_local_cluster
    return start_local_cluster(project_root)


def stop_cluster(*, project_root: Path | None = None) -> None:
    """Tear down the project's active cluster (any mode).

    Dispatches by the recorded mode in the state file.  No-op if no
    cluster is active.
    """
    project_root = (project_root or Path.cwd()).resolve()
    record = read_project_state(project_root)
    if record is None:
        return
    if record.mode == "local":
        from lightcone.engine.clusters._local import stop_local_cluster
        stop_local_cluster(project_root=project_root)
        return
    from lightcone.engine.clusters._slurm import stop_slurm_cluster
    stop_slurm_cluster(project_root=project_root)


def cluster_info(project_root: Path | None = None) -> ClusterInfo | None:
    """Return spec + record + live state for the project's active cluster."""
    project_root = (project_root or Path.cwd()).resolve()
    record = read_project_state(project_root)
    if record is None:
        return None
    if record.mode == "local":
        from lightcone.engine.clusters._local import local_cluster_info
        return local_cluster_info(project_root)
    from lightcone.engine.clusters._slurm import slurm_cluster_info
    return slurm_cluster_info(project_root)


def wait_for_scheduler(
    project_root: Path | None = None, timeout_s: int = 600,
) -> ClusterInfo:
    """Block until the project's cluster is RUNNING and reachable."""
    import time

    project_root = (project_root or Path.cwd()).resolve()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = cluster_info(project_root)
        if info is None:
            raise RuntimeError("No active cluster for this project.")
        if info.state in {"FAILED", "CANCELLED", "COMPLETED", "DEAD"}:
            raise RuntimeError(
                f"Cluster '{info.record.name if info.record else '?'}' "
                f"ended in state {info.state}"
            )
        if info.state == "RUNNING" and info.scheduler_address:
            return info
        time.sleep(3)
    raise TimeoutError(f"Cluster scheduler not ready after {timeout_s}s")


def tail_cluster_logs(
    project_root: Path | None = None,
    follow: bool = False,
    lines: int = 200,
) -> None:
    """Stream the project cluster's stdout log."""
    project_root = (project_root or Path.cwd()).resolve()
    record = read_project_state(project_root)
    if record is None:
        raise RuntimeError("No active cluster for this project.")
    if record.mode == "local":
        log = project_root / "results" / ".slurm" / "lc-cluster-local.log"
        if not log.exists():
            raise FileNotFoundError(f"Log file not found: {log}")
        cmd = ["tail", f"-n{lines}"] + (["-f"] if follow else []) + [str(log)]
        import subprocess
        subprocess.run(cmd, check=False)
        return
    from lightcone.engine.clusters._slurm import tail_slurm_logs
    tail_slurm_logs(project_root=project_root, follow=follow, lines=lines)


def refresh_cluster_cache(site: str, *, cluster_type: ClusterType = "slurm") -> Any:
    """Re-query the substrate's discovery layer for *site* and rewrite the cache."""
    if cluster_type == "slurm":
        from lightcone.engine.clusters._slurm import refresh_slurm_cache
        return refresh_slurm_cache(site)
    raise ValueError(f"unknown cluster type {cluster_type!r}")
