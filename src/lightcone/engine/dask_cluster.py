# mypy: disable-error-code="no-untyped-call"
"""Cluster lifecycle for ``lc run``.

One context manager, three branches:

- ``DASK_SCHEDULER_ADDRESS`` is already set → yield it as-is. We don't own
  the cluster, so we don't tear it down.
- ``SLURM_JOB_ID`` is set → start an in-process scheduler via
  ``LocalCluster(n_workers=0)``, then ``srun`` one ``dask worker`` per node
  across the allocation. Workers advertise the node's full resources;
  per-rule ``threads`` / ``mem_mb`` / ``gpus`` map to per-task constraints.
- Neither → ``LocalCluster()`` sized to the local machine.

The scheduler is always in-process (driven by ``lc run`` itself) so its
lifetime equals the run's lifetime — no service to manage, no orphaned
schedulers if the driver crashes.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
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
    in-process LocalCluster and the srun-launched ``dask worker``s
    advertise the same set so the executor's per-task requests resolve
    on either path.
    """
    res: dict[str, float] = {RESOURCE_CPUS: float(shape.cpus)}
    if shape.mem_bytes:
        res[RESOURCE_MEMORY] = float(shape.mem_bytes)
    if shape.gpus:
        res[RESOURCE_GPUS] = float(shape.gpus)
    return res


def _resources_arg(shape: _NodeShape) -> str:
    """Format `--resources` for `dask worker`."""
    return " ".join(f"{k}={int(v)}" for k, v in _resource_dict(shape).items())


@contextmanager
def cluster_for_run(
    *,
    verbose: bool = False,
    local_directory: str | None = None,
) -> Iterator[str]:
    """Yield a Dask scheduler address valid for the duration of `lc run`.

    *local_directory*, when given, is where dask workers stage their
    spilled task data and internal state files. ``lc run`` resolves it
    to a path under :mod:`lightcone.engine.scratch` so on NERSC the
    spill lands on Lustre instead of DVS-mounted home/CFS (where small-
    file I/O is slow and can pressure the gateway nodes).
    """
    if addr := os.environ.get("DASK_SCHEDULER_ADDRESS"):
        if verbose:
            print(f"→ Using existing Dask scheduler at {addr}")
        yield addr
        return

    if "SLURM_JOB_ID" in os.environ:
        with _slurm_backed_cluster(
            verbose=verbose, local_directory=local_directory
        ) as addr:
            yield addr
        return

    with _local_cluster(
        verbose=verbose, local_directory=local_directory
    ) as addr:
        yield addr


@contextmanager
def _local_cluster(
    *, verbose: bool, local_directory: str | None
) -> Iterator[str]:
    from dask.distributed import LocalCluster

    shape = _detect_node_shape()
    # Workers must advertise every key the executor may request — Dask
    # matches by exact key presence — or rules with ``mem_mb`` /
    # ``gpus_per_task`` would never schedule on a workstation.
    cluster = LocalCluster(
        n_workers=1,
        threads_per_worker=shape.cpus,
        resources=_resource_dict(shape),
        dashboard_address=":0",
        local_directory=local_directory,
    )
    if verbose:
        print(
            f"→ Local Dask cluster ({shape.cpus} threads); "
            f"scheduler at {cluster.scheduler_address}"
        )
    try:
        yield cluster.scheduler_address
    finally:
        cluster.close()


@contextmanager
def _slurm_backed_cluster(
    *, verbose: bool, local_directory: str | None
) -> Iterator[str]:
    from dask.distributed import LocalCluster

    if shutil.which("dask") is None:
        raise RuntimeError(
            "`dask` CLI is not on PATH inside the SLURM allocation. "
            "Install lightcone-cli (and its `distributed` dep) into the "
            "environment activated by your sbatch/salloc."
        )

    shape = _detect_node_shape()
    nnodes = int(os.environ.get("SLURM_NNODES") or 1)

    # Default LocalCluster binds the scheduler to 127.0.0.1, which workers
    # on remote nodes cannot reach. Bind to the driver's hostname so srun-
    # launched workers across the allocation can connect. SLURMD_NODENAME
    # is the SLURM-canonical name; gethostname() is a sane fallback.
    scheduler_host = os.environ.get("SLURMD_NODENAME") or socket.gethostname()
    cluster = LocalCluster(
        n_workers=0,
        host=scheduler_host,
        dashboard_address=":0",
        local_directory=local_directory,
    )
    addr = cluster.scheduler_address

    if verbose:
        print(
            f"→ SLURM allocation detected ({nnodes} node(s), "
            f"{shape.cpus} cpu/node, {shape.gpus} gpu/node); "
            f"launching workers via srun. Scheduler: {addr}"
        )

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
    ]
    if local_directory:
        worker_cmd.extend(["--local-directory", local_directory])
    workers = subprocess.Popen(worker_cmd)

    try:
        from dask.distributed import Client

        client = Client(addr)
        try:
            client.wait_for_workers(n_workers=nnodes, timeout=120)
            if verbose:
                print(f"→ {nnodes} dask worker(s) registered.")
        finally:
            client.close()
        yield addr
    finally:
        workers.terminate()
        try:
            workers.wait(timeout=10)
        except subprocess.TimeoutExpired:
            workers.kill()
            workers.wait()
        cluster.close()
