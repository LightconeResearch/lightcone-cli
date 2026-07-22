# mypy: disable-error-code="no-untyped-call"
"""Cluster lifecycle for ``lc run``.

One context manager, four branches:

- ``DASK_SCHEDULER_ADDRESS`` is already set → yield it as-is. We don't own
  the cluster, so we don't tear it down.
- ``DASK_GATEWAY__ADDRESS`` is set (a JupyterHub/Dask Gateway
  deployment) → **create** a run-scoped Gateway cluster with the
  project's image and shut it down when the run finishes. Create/cull
  per run is what makes image updates seamless: a Gateway cluster's
  image is fixed at creation, so picking up a freshly built project
  image *requires* a fresh cluster. Gateway scheduler addresses use a
  custom ``gateway://`` comm scheme a bare ``distributed.Client``
  cannot dial, so this branch hands the executor the *cluster name*
  (via :data:`GATEWAY_CLUSTER_ENV`) and the executor rejoins through
  the authenticated Gateway API.
- ``SLURM_JOB_ID`` is set → start an in-process scheduler via
  ``LocalCluster(n_workers=0)``, then ``srun`` one ``dask worker`` per node
  across the allocation. Workers advertise the node's full resources;
  per-rule ``threads`` / ``mem_mb`` / ``gpus`` map to per-task constraints.
- None of the above → ``LocalCluster()`` sized to the local machine.

Outside the Gateway branch the scheduler is always in-process (driven
by ``lc run`` itself) so its lifetime equals the run's lifetime — no
service to manage, no orphaned schedulers if the driver crashes. On the
Gateway branch the Gateway server owns scheduling, and the same
lifetime contract is enforced there: the cluster is shut down on exit,
with the deployment's idle timeout as the backstop if lc dies uncleanly.
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

#: Parent→child rendezvous for the Gateway branch: ``cluster_for_run``
#: sets this to the name of the cluster it created so the executor
#: plugin (running in the child snakemake process) can rejoin it via
#: ``Gateway().connect(name)``. Internal contract, not a user knob.
GATEWAY_CLUSTER_ENV = "LIGHTCONE_GATEWAY_CLUSTER"

#: Bounds how long the Gateway branch waits for the first worker of a
#: cluster it created (seconds; default 600 — a first-time image pull
#: on a fresh node is minutes, not seconds). Without this bound an
#: unpullable image leaves the run sitting at zero workers forever.
GATEWAY_WORKER_TIMEOUT_ENV = "LIGHTCONE_GATEWAY_WORKER_TIMEOUT"


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


def gateway_branch_active() -> bool:
    """Would :func:`cluster_for_run` take the Gateway branch right now?

    Exposed so ``lc run`` can shape the snakemake invocation (e.g. NFS
    latency tolerance) before entering the cluster context. Pure
    function of the environment, in the same priority order as the
    branches in :func:`cluster_for_run`.
    """
    if os.environ.get("DASK_SCHEDULER_ADDRESS"):
        return False
    return bool(os.environ.get("DASK_GATEWAY__ADDRESS"))


@contextmanager
def cluster_for_run(
    *,
    verbose: bool = False,
    local_directory: str | None = None,
    worker_image: str | None = None,
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

    *worker_image* is the registry ref the project's declared container
    resolves to; the Gateway branch creates its cluster with exactly
    this image (``None`` → the deployment's default). Ignored by the
    other branches — they realize containers by wrapping recipes, not
    via pod images.

    *max_workers* bounds the adaptive scaling of a Gateway cluster
    (``lc run`` passes its job bound — there is never a reason to hold
    more workers than dispatchable rules). Ignored everywhere else.
    """
    if addr := os.environ.get("DASK_SCHEDULER_ADDRESS"):
        if verbose:
            print(f"→ Using existing Dask scheduler at {addr}")
        yield {"DASK_SCHEDULER_ADDRESS": addr}
        return

    if os.environ.get("DASK_GATEWAY__ADDRESS"):
        with _gateway_cluster(
            verbose=verbose, worker_image=worker_image, max_workers=max_workers
        ) as name:
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
    *,
    verbose: bool,
    worker_image: str | None,
    max_workers: int | None,
) -> Iterator[str]:
    """Create a run-scoped Dask Gateway cluster; yield its name.

    The Gateway client is configured entirely by ambient dask config —
    on a lightcone JupyterHub deployment the ``DASK_GATEWAY__*`` env
    vars carry the API address, the JupyterHub auth mode, and the proxy
    address, so ``Gateway()`` needs no arguments here.
    """
    try:
        from dask_gateway import Gateway
    except ImportError as exc:
        raise RuntimeError(
            "A Dask Gateway environment was detected "
            "(DASK_GATEWAY__ADDRESS is set) but the dask-gateway client "
            "is not installed. Install it with "
            "`pip install lightcone-cli[gateway]`."
        ) from exc

    gateway = Gateway()
    options: dict[str, object] = {}
    if worker_image:
        # ``image`` is a server-side cluster option declared by the
        # deployment's options handler; a deployment that doesn't
        # expose it rejects the request — surfaced below with guidance.
        options["image"] = worker_image
    try:
        cluster = gateway.new_cluster(shutdown_on_close=True, **options)
    except Exception as exc:
        detail = (
            f" (requested image={worker_image!r} — if the deployment does "
            "not expose an `image` cluster option, ask the hub admin to "
            "add it to the gateway's cluster-options handler)"
            if worker_image
            else ""
        )
        raise RuntimeError(
            f"Could not create a Dask Gateway cluster ({exc}){detail}."
        ) from exc

    bound = max(1, max_workers or 1)
    if verbose:
        image_note = f" with image {worker_image}" if worker_image else ""
        print(
            f"→ Created Dask Gateway cluster {cluster.name}{image_note}; "
            f"scaling adaptively up to {bound} worker(s) "
            f"(dashboard: {cluster.dashboard_link})"
        )
    try:
        cluster.adapt(minimum=1, maximum=bound)
        client = cluster.get_client()
        try:
            _wait_first_worker(client, image=worker_image)
            _assert_worker_resources(client)
        finally:
            client.close()
        yield str(cluster.name)
    finally:
        # We created it, we cull it. shutdown() stops the cluster
        # server-side; if lc dies before reaching this, shutdown_on_close
        # and the deployment's idle timeout are the backstops.
        try:
            cluster.shutdown()
        except Exception:
            cluster.close()
        if verbose:
            print(f"→ Shut down Dask Gateway cluster {cluster.name}")


def _wait_first_worker(client: object, *, image: str | None) -> None:
    """Block until the created cluster has one live worker.

    An unpullable image or an unschedulable pool otherwise leaves the
    run sitting at zero workers with no error at all — the classic
    silent-hang failure mode.
    """
    try:
        timeout = int(os.environ.get(GATEWAY_WORKER_TIMEOUT_ENV) or 600)
    except ValueError:
        timeout = 600
    try:
        client.wait_for_workers(n_workers=1, timeout=timeout)  # type: ignore[attr-defined]
    except Exception as exc:
        image_hint = f"the worker image ({image or 'deployment default'}) cannot be pulled"
        raise RuntimeError(
            f"No Dask Gateway worker became ready within {timeout}s "
            f"({exc}). Likely causes: {image_hint}, or the node pool "
            "cannot schedule a worker (capacity/quota). Check the "
            "JupyterLab Dask panel for the cluster's state, or raise "
            f"{GATEWAY_WORKER_TIMEOUT_ENV}."
        ) from exc


def _assert_worker_resources(client: object) -> None:
    """Fail fast when Gateway workers don't advertise the resource contract.

    Dask schedules a task only on workers advertising *every* requested
    resource key. The executor requests ``cpus`` for every rule and
    ``memory`` for any rule with ``mem_mb``, so a deployment that forgot
    to inject ``DASK_DISTRIBUTED__WORKER__RESOURCES__*`` into worker
    pods makes every rule hang with no error — refuse loudly instead.
    ``gpus`` is deliberately not required: a CPU-only deployment
    legitimately omits it.
    """
    workers = client.scheduler_info().get("workers", {})  # type: ignore[attr-defined]
    if not workers:
        return
    if any(
        RESOURCE_CPUS in res and RESOURCE_MEMORY in res
        for w in workers.values()
        if (res := w.get("resources") or {}) is not None
    ):
        return
    raise RuntimeError(
        "Dask Gateway workers do not advertise the lightcone resource "
        f"contract ({RESOURCE_CPUS}+{RESOURCE_MEMORY}, plus "
        f"{RESOURCE_GPUS} on GPU pools); per-rule resource requests "
        "would never schedule. Fix the deployment to inject "
        "DASK_DISTRIBUTED__WORKER__RESOURCES__* into worker pods (see "
        "the hub-deploy dask-gateway cluster-options handler)."
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
