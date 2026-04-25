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
    read_scheduler_address,
    resolve_cluster,
    save_cluster_config,
    spec_from_config,
    walltime_to_slurm,
)

logger = logging.getLogger(__name__)


__all__ = [
    "ClusterInfo",
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
    "resolve_cluster",
    "parse_walltime_seconds",
    "read_scheduler_address",
    "spec_from_config",
    "walltime_to_slurm",
    "start_cluster",
    "stop_cluster",
    "cluster_info",
    "find_running_cluster",
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


def start_cluster(
    name: str,
    *,
    project_root: Path | None = None,
    overrides: dict[str, Any] | None = None,
    strategy: str = "fit",
) -> ClusterInfo:
    """Provision the cluster's substrate and write its state file."""
    cluster_type, config = _resolve_type(name)
    if cluster_type == "slurm":
        from lightcone.engine.clusters._slurm import start_slurm_cluster
        return start_slurm_cluster(
            name, config, overrides or {},
            project_root=project_root, strategy=strategy,
        )
    raise ValueError(f"unknown cluster type {cluster_type!r}")


def stop_cluster(name: str) -> None:
    """Tear down the cluster's substrate and remove its state file."""
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
    """Spec + record + live substrate state for *name*, or ``None`` if no config."""
    config = load_cluster_config(name)
    if config is None:
        return None
    cluster_type = config.get("type")
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


def wait_for_scheduler(name: str, timeout_s: int = 600) -> ClusterInfo:
    """Block until the cluster is RUNNING and the Dask scheduler is reachable.

    Loads the cluster config once, then polls only the substrate's live
    state on each iteration.
    """
    import time

    cluster_type, config = _resolve_type(name)
    if cluster_type != "slurm":
        raise ValueError(f"unknown cluster type {cluster_type!r}")
    from lightcone.engine.clusters._slurm import slurm_cluster_info

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = slurm_cluster_info(name, config=config)
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
