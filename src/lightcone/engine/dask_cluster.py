# mypy: disable-error-code="no-untyped-call"
"""Cluster connection point for ``lc run``.

Two branches:

- ``DASK_SCHEDULER_ADDRESS`` set → use it as-is. We don't own the
  cluster, so we don't tear it down. Kept as the escape hatch / CI
  override / "user has their own scheduler" path.
- Otherwise → :func:`lightcone.engine.dask_daemon.ensure_scheduler`
  returns the address of a session-scoped scheduler (spawning the
  daemon if needed). The daemon outlives any single ``lc run`` so
  successive runs in the same Claude session reuse it.

The node-shape helpers and resource-key constants live here because
both the daemon (when it spins up a cluster) and the executor plugin
(when it requests per-task resources) consume them.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# Resource keys advertised by workers and requested per-task. These strings
# form a contract between the worker bootstrap (in :mod:`dask_daemon`) and
# the executor plugin (snakemake_executor_plugin_dask.executor). Dask
# matches by string equality.
RESOURCE_CPUS = "cpus"
RESOURCE_MEMORY = "memory"
RESOURCE_GPUS = "gpus"


@dataclass
class _NodeShape:
    """Per-node resources advertised by the dask worker."""

    cpus: int
    mem_bytes: int
    gpus: int


def _detect_node_shape() -> _NodeShape:
    """Read node capacity from SLURM env vars (with sensible fallbacks)."""
    cpus = int(os.environ.get("SLURM_CPUS_ON_NODE") or os.cpu_count() or 1)

    mem_mb = os.environ.get("SLURM_MEM_PER_NODE")
    if mem_mb:
        mem_bytes = int(mem_mb) * 1_000_000
    else:
        try:
            import psutil  # type: ignore[import-untyped]

            mem_bytes = psutil.virtual_memory().total
        except ImportError:
            mem_bytes = 0  # advisory: workers won't enforce memory caps

    gpus = int(os.environ.get("SLURM_GPUS_ON_NODE") or 0)
    return _NodeShape(cpus=cpus, mem_bytes=mem_bytes, gpus=gpus)


def _resource_dict(shape: _NodeShape) -> dict[str, float]:
    """Resource keys advertised by a worker for this node shape.

    Single source of truth for which keys workers expose — both the
    laptop ``LocalCluster`` and the srun-launched ``dask worker``s
    advertise the same set so the executor's per-task requests
    resolve on either path.
    """
    res: dict[str, float] = {RESOURCE_CPUS: float(shape.cpus)}
    if shape.mem_bytes:
        res[RESOURCE_MEMORY] = float(shape.mem_bytes)
    if shape.gpus:
        res[RESOURCE_GPUS] = float(shape.gpus)
    return res


def _resources_arg(shape: _NodeShape) -> str:
    """Format ``--resources`` for ``dask worker``."""
    return " ".join(f"{k}={int(v)}" for k, v in _resource_dict(shape).items())


@contextmanager
def cluster_for_run(
    *,
    project_path: Path,
    verbose: bool = False,
) -> Iterator[str]:
    """Yield a Dask scheduler address for the duration of one ``lc run``.

    The scheduler outlives the run — see :mod:`lightcone.engine.dask_daemon`.
    No teardown happens here; the daemon self-shuts on idle-timeout, or
    on SIGTERM from the SessionEnd hook.
    """
    if addr := os.environ.get("DASK_SCHEDULER_ADDRESS"):
        if verbose:
            print(f"→ Using existing Dask scheduler at {addr}")
        yield addr
        return

    from lightcone.engine.dask_daemon import ensure_scheduler

    addr = ensure_scheduler(project_path)
    if verbose:
        print(f"→ Reusing session scheduler at {addr}")
    yield addr
