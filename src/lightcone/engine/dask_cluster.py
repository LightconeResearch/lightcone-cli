# mypy: disable-error-code="no-untyped-call"
"""Cluster lifecycle for ``lc materialize``.

One run-scoped ``LocalCluster``, owned by the driver: the scheduler is
in-process, so its lifetime equals the run's lifetime — no service to
manage, no orphaned schedulers if the driver crashes. The child
snakemake's executor plugin reaches it through the
``DASK_SCHEDULER_ADDRESS`` env overlay yielded here.

Additional venues (SLURM allocations, hub deployments) return later as
new branches behind this same context-manager seam.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

# Resource keys advertised by workers and requested per-task. These strings
# form a contract between the worker bootstrap (here) and the executor plugin
# (snakemake_executor_plugin_dask.executor). Dask matches by string equality.
RESOURCE_CPUS = "cpus"
RESOURCE_MEMORY = "memory"
RESOURCE_GPUS = "gpus"


@dataclass
class _NodeShape:
    """Machine resources advertised by the dask worker."""

    cpus: int
    mem_bytes: int
    gpus: int


def _detect_node_shape() -> _NodeShape:
    """Read machine capacity (with sensible fallbacks)."""
    cpus = int(os.cpu_count() or 1)
    try:
        import psutil  # type: ignore[import-untyped]

        mem_bytes = psutil.virtual_memory().total
    except ImportError:
        mem_bytes = 0  # advisory: workers won't enforce memory caps
    return _NodeShape(cpus=cpus, mem_bytes=mem_bytes, gpus=0)


def _resource_dict(shape: _NodeShape) -> dict[str, float]:
    """Resource keys advertised by the worker for this machine.

    Workers must advertise every key the executor may request — Dask
    matches by exact key presence — or rules with ``mem_mb`` /
    ``gpus_per_task`` would never schedule.
    """
    res: dict[str, float] = {RESOURCE_CPUS: float(shape.cpus)}
    if shape.mem_bytes:
        res[RESOURCE_MEMORY] = float(shape.mem_bytes)
    if shape.gpus:
        res[RESOURCE_GPUS] = float(shape.gpus)
    return res


@contextmanager
def cluster_for_run(
    *,
    verbose: bool = False,
    local_directory: str | None = None,
    max_workers: int | None = None,
) -> Iterator[dict[str, str]]:
    """Yield the env overlay the child snakemake needs to reach the cluster.

    The parent (``lc materialize``) and the executor plugin live in
    different processes, so connection info travels via the environment:
    ``{"DASK_SCHEDULER_ADDRESS": addr}``.

    *local_directory*, when given, is where dask workers stage their
    spilled task data and internal state files. ``lc materialize``
    resolves it to a path under :mod:`lightcone.engine.scratch`.

    *max_workers* is currently unused on the local branch (one worker
    with all cores); kept in the signature as the seam future venue
    branches share.
    """
    from dask.distributed import LocalCluster

    shape = _detect_node_shape()
    cluster = LocalCluster(
        n_workers=1,
        threads_per_worker=shape.cpus,
        resources=_resource_dict(shape),
        dashboard_address=":0",
        local_directory=local_directory,
        silence_logs=logging.INFO if verbose else logging.WARNING,
    )
    if verbose:
        print(
            f"→ Local Dask cluster ({shape.cpus} threads); "
            f"scheduler at {cluster.scheduler_address}"
        )
    try:
        yield {"DASK_SCHEDULER_ADDRESS": cluster.scheduler_address}
    finally:
        cluster.close()
