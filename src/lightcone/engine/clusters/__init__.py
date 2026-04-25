"""Cluster abstraction — manage long-lived Dask clusters across substrates.

A *cluster* is a persistent rendezvous to a Dask scheduler. The substrate
that provides the scheduler (today: SLURM) is a private implementation
detail dispatched from the ``type:`` field in the cluster YAML.

Adding a new substrate is one new module + one branch per dispatching
function below.
"""
from __future__ import annotations

import logging
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
    list_state_records,
    load_cluster_config,
    parse_walltime_seconds,
    read_record,
    read_scheduler_address,
    resolve_cluster,
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
    "list_state_records",
    "load_cluster_config",
    "save_cluster_config",
    "resolve_cluster",
    "parse_walltime_seconds",
    "read_record",
    "read_scheduler_address",
    "spec_from_config",
    "walltime_to_slurm",
    "start_cluster",
    "attach_cluster",
    "stop_cluster",
    "cluster_info",
    "find_running_cluster",
    "find_attached_cluster_for_job",
    "list_attached_clusters",
    "wait_for_scheduler",
    "tail_cluster_logs",
    "refresh_cluster_cache",
]


# ---------------------------------------------------------------------------
# Type-dispatched lifecycle
# ---------------------------------------------------------------------------


def _resolve_type(name: str) -> tuple[ClusterType, dict[str, Any]]:
    """Load the cluster YAML and return ``(type, config)``.

    Raises ``FileNotFoundError`` if the YAML is missing, ``ValueError``
    if it lacks a ``type:`` discriminator.
    """
    config = load_cluster_config(name)
    if config is None:
        raise FileNotFoundError(
            f"No cluster named '{name}'. Configured: {list_clusters() or 'none'}"
        )
    cluster_type = config.get("type")
    if cluster_type is None:
        raise ValueError(
            f"Cluster '{name}': missing required field 'type' (set `type: slurm`)"
        )
    return cluster_type, config


def _record_type(name: str) -> ClusterType | None:
    """Return the substrate type from a state file, or ``None`` if absent."""
    record = read_record(name)
    return record.type if record is not None else None


def start_cluster(
    name: str,
    *,
    project_root: Path | None = None,
    overrides: dict[str, Any] | None = None,
    strategy: str = "fit",
) -> ClusterInfo:
    """Submit a *named, configured* cluster via its substrate.

    Always uses the ``cluster.yaml`` for *name* and submits a fresh job
    (today: ``sbatch``).  Idempotent — if a recorded job for *name* is
    already alive, returns the existing :class:`ClusterInfo` without
    re-submitting.  For "use the SLURM allocation I'm already in", call
    :func:`attach_cluster` instead.
    """
    cluster_type, config = _resolve_type(name)
    if cluster_type == "slurm":
        from lightcone.engine.clusters._slurm import start_slurm_cluster
        return start_slurm_cluster(
            name, config, overrides or {},
            project_root=project_root, strategy=strategy,
        )
    raise ValueError(f"unknown cluster type {cluster_type!r}")


def attach_cluster(
    *,
    project_root: Path | None = None,
) -> ClusterInfo:
    """Spawn a Dask cluster inside the current SLURM allocation.

    Reads ``$SLURM_JOB_ID`` and friends from the environment.  Raises
    :class:`RuntimeError` if not inside an allocation.  No yaml is
    consulted; worker layout is derived from SLURM env.
    """
    from lightcone.engine.clusters._slurm import attach_to_allocation
    return attach_to_allocation(project_root=project_root)


def stop_cluster(name: str) -> None:
    """Tear down a cluster (mode-aware) and clean up its state.

    For ``sbatch`` mode this ``scancel``s the job; for ``attached`` mode
    it kills the dask scheduler/worker processes and leaves the user's
    salloc allocation intact.
    """
    # Prefer the substrate recorded in the state file (covers attached
    # clusters that have no yaml). Fall back to yaml when no state exists
    # so that a "stop" on a never-started yaml cluster is still a no-op.
    cluster_type = _record_type(name)
    if cluster_type is None:
        config = load_cluster_config(name)
        if config is None:
            return
        cluster_type = config.get("type")
    if cluster_type == "slurm":
        from lightcone.engine.clusters._slurm import stop_slurm_cluster
        stop_slurm_cluster(name)
        return
    raise ValueError(f"unknown cluster type {cluster_type!r}")


def cluster_info(name: str) -> ClusterInfo | None:
    """Spec + record + live substrate state for *name*, or ``None`` if absent.

    Resolves attached clusters from their state file alone (no yaml
    required); resolves yaml-backed clusters via :func:`load_cluster_config`.
    """
    cluster_type = _record_type(name)
    if cluster_type is None:
        config = load_cluster_config(name)
        if config is None:
            return None
        cluster_type = config.get("type")
    else:
        config = load_cluster_config(name)  # may be None for attached
    if cluster_type == "slurm":
        from lightcone.engine.clusters._slurm import slurm_cluster_info
        return slurm_cluster_info(name, config=config)
    raise ValueError(f"unknown cluster type {cluster_type!r}")


def find_running_cluster(name: str) -> ClusterInfo | None:
    """Return the cluster if its scheduler is reachable; ``None`` otherwise."""
    info = cluster_info(name)
    if info is None or info.state != "RUNNING" or not info.scheduler_address:
        return None
    return info


def list_attached_clusters() -> list[ClusterRecord]:
    """Return all currently-recorded ``mode=attached`` clusters."""
    return [r for r in list_state_records() if r.mode == "attached"]


def find_attached_cluster_for_job(job_id: str) -> ClusterInfo | None:
    """Return the attached cluster matching *job_id*, or ``None``."""
    for record in list_attached_clusters():
        if record.job_id == job_id:
            return cluster_info(record.name)
    return None


def wait_for_scheduler(name: str, timeout_s: int = 600) -> ClusterInfo:
    """Block until the cluster is RUNNING and the Dask scheduler is reachable.

    Works for both yaml-backed and attached clusters.
    """
    import time

    cluster_type = _record_type(name)
    if cluster_type is None:
        cluster_type, _config = _resolve_type(name)
    if cluster_type != "slurm":
        raise ValueError(f"unknown cluster type {cluster_type!r}")
    from lightcone.engine.clusters._slurm import slurm_cluster_info

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = slurm_cluster_info(name)
        if info is None or info.record is None:
            raise RuntimeError(f"No state for cluster '{name}'")
        if info.state in {"FAILED", "CANCELLED", "COMPLETED", "DEAD"}:
            raise RuntimeError(f"Cluster '{name}' ended in state {info.state}")
        if info.state == "RUNNING" and info.scheduler_address:
            return info
        time.sleep(5)
    raise TimeoutError(f"Cluster '{name}' scheduler not ready after {timeout_s}s")


def tail_cluster_logs(
    name: str,
    project_root: Path | None = None,
    follow: bool = False,
    lines: int = 200,
) -> None:
    """Stream the substrate's stdout log for a cluster."""
    cluster_type, _config = _resolve_type(name)
    if cluster_type == "slurm":
        from lightcone.engine.clusters._slurm import tail_slurm_logs
        tail_slurm_logs(name, project_root=project_root, follow=follow, lines=lines)
        return
    raise ValueError(f"unknown cluster type {cluster_type!r}")


def refresh_cluster_cache(site: str, *, cluster_type: ClusterType = "slurm") -> Any:
    """Re-query the substrate's discovery layer for *site* and rewrite the cache."""
    if cluster_type == "slurm":
        from lightcone.engine.clusters._slurm import refresh_slurm_cache
        return refresh_slurm_cache(site)
    raise ValueError(f"unknown cluster type {cluster_type!r}")
