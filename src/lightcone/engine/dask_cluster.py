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
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


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


def _resources_arg(shape: _NodeShape) -> str:
    """Format `--resources` for `dask worker`."""
    parts = [f"cpus={shape.cpus}"]
    if shape.mem_bytes:
        parts.append(f"memory={shape.mem_bytes}")
    if shape.gpus:
        parts.append(f"gpus={shape.gpus}")
    return " ".join(parts)


@contextmanager
def cluster_for_run(*, verbose: bool = False) -> Iterator[str]:
    """Yield a Dask scheduler address valid for the duration of `lc run`."""
    if addr := os.environ.get("DASK_SCHEDULER_ADDRESS"):
        if verbose:
            print(f"→ Using existing Dask scheduler at {addr}")
        yield addr
        return

    if "SLURM_JOB_ID" in os.environ:
        with _slurm_backed_cluster(verbose=verbose) as addr:
            yield addr
        return

    with _local_cluster(verbose=verbose) as addr:
        yield addr


@contextmanager
def _local_cluster(*, verbose: bool) -> Iterator[str]:
    from dask.distributed import LocalCluster

    shape = _detect_node_shape()
    cluster = LocalCluster(
        n_workers=1,
        threads_per_worker=shape.cpus,
        resources={"cpus": shape.cpus},
        dashboard_address=":0",
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
def _slurm_backed_cluster(*, verbose: bool) -> Iterator[str]:
    from dask.distributed import LocalCluster

    if shutil.which("dask") is None:
        raise RuntimeError(
            "`dask` CLI is not on PATH inside the SLURM allocation. "
            "Install lightcone-cli (and its `distributed` dep) into the "
            "environment activated by your sbatch/salloc."
        )

    shape = _detect_node_shape()
    nnodes = int(os.environ.get("SLURM_NNODES") or 1)

    cluster = LocalCluster(n_workers=0, dashboard_address=":0")
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


def _wait_for_workers(addr: str, n_workers: int, timeout: int) -> None:
    """Reusable for tests/integration callers; thin shim around Client."""
    from dask.distributed import Client

    client = Client(addr)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(client.scheduler_info()["workers"]) >= n_workers:
                return
            time.sleep(0.5)
        registered = len(client.scheduler_info()["workers"])
        raise TimeoutError(f"Only {registered} of {n_workers} workers registered")
    finally:
        client.close()
