# mypy: disable-error-code="no-untyped-call"
"""Cluster lifecycle for ``lc run``.

One context manager, four branches:

- ``DASK_SCHEDULER_ADDRESS`` is already set → yield it as-is. We don't own
  the cluster, so we don't tear it down.
- A Dask Gateway environment is detected (``LIGHTCONE_GATEWAY_CLUSTER`` or
  ``DASK_GATEWAY__ADDRESS`` is set, e.g. on a JupyterHub deployment) →
  create a Gateway cluster (or attach to a named one) via the
  ``dask-gateway`` client. Gateway scheduler addresses use a custom
  ``gateway://`` comm scheme that a bare ``distributed.Client`` cannot
  dial, so the child snakemake process is told the *cluster name* via
  ``LIGHTCONE_GATEWAY_CLUSTER`` and rejoins through the Gateway API —
  the same rendezvous-by-ambient-context pattern the SLURM branch uses.
- ``SLURM_JOB_ID`` is set → start an in-process scheduler via
  ``LocalCluster(n_workers=0)``, then ``srun`` one ``dask worker`` per node
  across the allocation. Workers advertise the node's full resources;
  per-rule ``threads`` / ``mem_mb`` / ``gpus`` map to per-task constraints.
- None of the above → ``LocalCluster()`` sized to the local machine.

Except on the Gateway branch (where the Gateway server owns scheduling),
the scheduler is always in-process (driven by ``lc run`` itself) so its
lifetime equals the run's lifetime — no service to manage, no orphaned
schedulers if the driver crashes. Owned Gateway clusters are shut down on
exit; attached ones (named via ``LIGHTCONE_GATEWAY_CLUSTER``) are left
running, mirroring the ``DASK_SCHEDULER_ADDRESS`` convention.
"""

from __future__ import annotations

import logging
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

#: Env var carrying a Dask Gateway cluster name. Set by the user to make
#: ``lc run`` attach to an existing Gateway cluster (e.g. one created from
#: the JupyterLab Dask sidebar); set by :func:`cluster_for_run` for the
#: child snakemake process so the executor plugin can rejoin the cluster
#: through the Gateway API (a bare ``Client`` cannot dial ``gateway://``).
GATEWAY_CLUSTER_ENV = "LIGHTCONE_GATEWAY_CLUSTER"

#: How long to wait for the first Gateway worker. Generous because a
#: scale-from-zero pool must provision a node and pull the worker image.
GATEWAY_WORKER_TIMEOUT = 600

#: Ceiling for adaptive scaling of an owned Gateway cluster when ``lc run``
#: has no parallelism hint.
GATEWAY_DEFAULT_MAX_WORKERS = 8


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
    max_workers: int | None = None,
) -> Iterator[dict[str, str]]:
    """Yield the env overlay the child snakemake needs to reach the cluster.

    The parent (``lc run``) and the executor plugin live in different
    processes, so connection info travels via environment variables.
    Address-based branches yield ``{"DASK_SCHEDULER_ADDRESS": addr}``;
    the Gateway branch yields ``{GATEWAY_CLUSTER_ENV: name}`` because
    Gateway clusters are rejoined by name through the authenticated
    Gateway API rather than dialled by address.

    *local_directory*, when given, is where dask workers stage their
    spilled task data and internal state files. ``lc run`` resolves it
    to a path under :mod:`lightcone.engine.scratch` so on NERSC the
    spill lands on Lustre instead of DVS-mounted home/CFS (where small-
    file I/O is slow and can pressure the gateway nodes).

    *max_workers* bounds adaptive scaling of an owned Gateway cluster;
    the in-process branches size themselves to the node/allocation.
    """
    if addr := os.environ.get("DASK_SCHEDULER_ADDRESS"):
        if verbose:
            print(f"→ Using existing Dask scheduler at {addr}")
        yield {"DASK_SCHEDULER_ADDRESS": addr}
        return

    if GATEWAY_CLUSTER_ENV in os.environ or "DASK_GATEWAY__ADDRESS" in os.environ:
        with _gateway_cluster(verbose=verbose, max_workers=max_workers) as name:
            yield {GATEWAY_CLUSTER_ENV: name}
        return

    if "SLURM_JOB_ID" in os.environ:
        with _slurm_backed_cluster(
            verbose=verbose, local_directory=local_directory
        ) as addr:
            yield {"DASK_SCHEDULER_ADDRESS": addr}
        return

    with _local_cluster(
        verbose=verbose, local_directory=local_directory
    ) as addr:
        yield {"DASK_SCHEDULER_ADDRESS": addr}


@contextmanager
def _gateway_cluster(
    *, verbose: bool, max_workers: int | None
) -> Iterator[str]:
    """Create (or attach to) a Dask Gateway cluster; yield its name.

    Ownership follows the same convention as the address branch: a
    cluster we create is ours to shut down; a cluster named by the user
    via ``LIGHTCONE_GATEWAY_CLUSTER`` (e.g. created from the JupyterLab
    Dask sidebar, dashboard already docked) is left running on exit.

    The Gateway client is configured entirely by ambient dask config —
    on a lightcone JupyterHub deployment the ``DASK_GATEWAY__*`` env
    vars carry the API address, the JupyterHub auth mode, and the
    proxy address, so ``Gateway()`` needs no arguments here.
    """
    try:
        from dask_gateway import Gateway
    except ImportError as exc:
        raise RuntimeError(
            "A Dask Gateway environment was detected "
            f"({GATEWAY_CLUSTER_ENV} or DASK_GATEWAY__ADDRESS is set) but "
            "the dask-gateway client is not installed. Install it with "
            "`pip install lightcone-cli[gateway]`."
        ) from exc

    gateway = Gateway()
    name = os.environ.get(GATEWAY_CLUSTER_ENV)
    owned = name is None
    if owned:
        cluster = gateway.new_cluster(shutdown_on_close=False)
        cluster.adapt(
            minimum=1, maximum=max_workers or GATEWAY_DEFAULT_MAX_WORKERS
        )
    else:
        cluster = gateway.connect(name)

    if verbose:
        mode = "started" if owned else "attached to"
        print(
            f"→ {mode} Dask Gateway cluster {cluster.name} "
            f"(dashboard: {cluster.dashboard_link})"
        )

    try:
        if owned:
            # A worker is guaranteed to be coming (adaptive minimum=1),
            # so wait for it and fail fast if the deployment forgot the
            # resource contract — otherwise every task hangs silently.
            client = cluster.get_client()
            try:
                client.wait_for_workers(1, timeout=GATEWAY_WORKER_TIMEOUT)
                _assert_worker_resources(client)
            finally:
                client.close()
        else:
            # Don't touch the user's scaling; verify the contract only
            # if workers are already up (an adaptive cluster at zero
            # will scale once the executor submits tasks).
            client = cluster.get_client()
            try:
                workers = client.scheduler_info().get("workers", {})
                if workers:
                    _assert_worker_resources(client)
            finally:
                client.close()
        yield cluster.name
    finally:
        if owned:
            cluster.shutdown()
        else:
            cluster.close()


def _assert_worker_resources(client: object) -> None:
    """Fail fast when Gateway workers don't advertise the resource contract.

    Dask schedules a task only on workers advertising *every* requested
    resource key, so a Gateway deployment that forgot to inject
    ``cpus``/``memory``/``gpus`` (via cluster-options environment, e.g.
    ``DASK_DISTRIBUTED__WORKER__RESOURCES__CPUS``) makes every rule hang
    with no error. Better to refuse loudly at startup.
    """
    workers = client.scheduler_info().get("workers", {})  # type: ignore[attr-defined]
    if not workers:
        return
    if any(
        RESOURCE_CPUS in (w.get("resources") or {}) for w in workers.values()
    ):
        return
    raise RuntimeError(
        "Dask Gateway workers do not advertise the lightcone resource "
        f"contract ({RESOURCE_CPUS}/{RESOURCE_MEMORY}/{RESOURCE_GPUS}); "
        "per-rule resource requests would never schedule. Fix the gateway "
        "deployment to inject DASK_DISTRIBUTED__WORKER__RESOURCES__* into "
        "worker pods (see the lightcone-hub dask-gateway values)."
    )


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
        silence_logs=logging.INFO if verbose else logging.WARNING,
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
        silence_logs=logging.INFO if verbose else logging.WARNING,
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
        # Each srun task is a single run-scoped worker; an auto-restart
        # nanny adds no value (srun won't relaunch the task either) and
        # logs "Worker process died unexpectedly" when retire_workers
        # asks the worker to exit on shutdown.
        "--no-nanny",
    ]
    if local_directory:
        worker_cmd.extend(["--local-directory", local_directory])
    # Hide the worker's INFO-level connection chatter (Nanny start,
    # scheduler registration, etc.) — useful only when debugging the
    # cluster itself. WARNING+ still surface real issues. The newer
    # `dask worker` CLI dropped `--silence-logs`, so we drive it via
    # Dask's config env var instead; srun inherits env by default.
    worker_env = dict(os.environ)
    if not verbose:
        worker_env.setdefault("DASK_LOGGING__DISTRIBUTED", "warning")
    workers = subprocess.Popen(worker_cmd, env=worker_env)

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
        # Graceful shutdown: ask the scheduler to retire workers so each
        # `dask worker` process exits on its own. srun then sees its task
        # exit with code 0 and terminates silently. SIGTERM-ing srun
        # directly (the prior path) prints "srun: forcing job
        # termination" / "task 0: Killed" to stderr on every clean run.
        try:
            client = Client(addr, timeout="10s")
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
        cluster.close()
