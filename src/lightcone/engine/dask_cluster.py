# mypy: disable-error-code="no-untyped-call"
"""Cluster lifecycle for ``lc run``.

One context manager, four branches:

- ``DASK_SCHEDULER_ADDRESS`` is already set → yield it as-is. We don't own
  the cluster, so we don't tear it down.
- A Dask Gateway environment is detected (``LIGHTCONE_GATEWAY_CLUSTER`` or
  ``DASK_GATEWAY__ADDRESS`` is set, e.g. on a JupyterHub deployment) →
  **attach** to the user's running Gateway cluster. ``lc run`` never
  creates Gateway clusters: the user starts one from JupyterLab (Dask
  sidebar or a notebook, where the options widget offers image/cores/
  memory) and lc discovers it through the user-scoped Gateway API —
  exactly one running cluster attaches unambiguously; zero or several
  is an error naming the fix. Gateway scheduler addresses use a custom
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
schedulers if the driver crashes. The Gateway branch mirrors the
``DASK_SCHEDULER_ADDRESS`` convention: we attach to a cluster someone
else owns, so we leave it running (and its scaling untouched) on exit.
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

#: Env var carrying a Dask Gateway cluster name. Set by the user to pick
#: one of several running Gateway clusters (discovery attaches
#: automatically when exactly one is up); set by :func:`cluster_for_run`
#: for the child snakemake process so the executor plugin can rejoin the
#: cluster through the Gateway API (a bare ``Client`` cannot dial
#: ``gateway://``).
GATEWAY_CLUSTER_ENV = "LIGHTCONE_GATEWAY_CLUSTER"

#: Env var carrying the image a Gateway worker pod was started with. A
#: lightcone-hub deployment's cluster-options handler injects it into
#: every scheduler/worker pod; :func:`cluster_for_run` reads it back to
#: verify the attached cluster actually runs the project's image (and
#: the manifest layer records it as ground truth — see
#: ``lightcone.engine.manifest.write_manifest``).
WORKER_IMAGE_ENV = "LIGHTCONE_WORKER_IMAGE"


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

    Exposed so ``lc run`` can shape the parent snakemake invocation
    (``--shared-fs-usage``) before entering the cluster context — the
    decision is a pure function of the environment, in the same
    priority order as the branches below.
    """
    if os.environ.get("DASK_SCHEDULER_ADDRESS"):
        return False
    return (
        GATEWAY_CLUSTER_ENV in os.environ
        or "DASK_GATEWAY__ADDRESS" in os.environ
    )


@contextmanager
def cluster_for_run(
    *,
    verbose: bool = False,
    local_directory: str | None = None,
    expected_worker_image: str | None = None,
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

    *expected_worker_image*, when given, is the image the project's
    declared container resolves to on this deployment; the Gateway
    branch compares it against what the attached cluster actually runs
    and warns on mismatch. Ignored by the other branches (they realize
    containers by wrapping recipes, not via pod images).
    """
    if addr := os.environ.get("DASK_SCHEDULER_ADDRESS"):
        if verbose:
            print(f"→ Using existing Dask scheduler at {addr}")
        yield {"DASK_SCHEDULER_ADDRESS": addr}
        return

    if GATEWAY_CLUSTER_ENV in os.environ or "DASK_GATEWAY__ADDRESS" in os.environ:
        with _gateway_cluster(
            verbose=verbose, expected_worker_image=expected_worker_image
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
    *, verbose: bool, expected_worker_image: str | None
) -> Iterator[str]:
    """Attach to the user's running Dask Gateway cluster; yield its name.

    Attach-only by design: cluster lifecycle belongs to the user (create
    one from the JupyterLab Dask sidebar or a notebook — that's where
    the image/cores/memory options widget lives, and where the dashboard
    is already docked). The Gateway API is user-scoped under JupyterHub
    auth, so ``list_clusters()`` sees only the caller's clusters —
    exactly one running cluster attaches with zero configuration; zero
    or several raises with the fix spelled out. We never touch the
    cluster's scaling and leave it running on exit, mirroring the
    ``DASK_SCHEDULER_ADDRESS`` convention.

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

    try:
        gateway = Gateway()
    except Exception as exc:
        # Gateway() raises (ValueError) when no gateway address is
        # configured — e.g. LIGHTCONE_GATEWAY_CLUSTER lingering in a
        # shell off-hub, where no DASK_GATEWAY__* env exists.
        raise RuntimeError(
            "A Dask Gateway environment was detected but the gateway "
            f"client could not be configured ({exc}). If you exported "
            f"{GATEWAY_CLUSTER_ENV} on a machine without a Dask Gateway, "
            "unset it."
        ) from exc

    named = os.environ.get(GATEWAY_CLUSTER_ENV)
    name = named or _discover_cluster_name(
        gateway, expected_worker_image=expected_worker_image
    )
    try:
        cluster = gateway.connect(name)
    except Exception as exc:
        # dask-gateway raises its own error types (ValueError,
        # GatewayClusterError) for a missing/stopped cluster; translate
        # into guidance, because a *stale* LIGHTCONE_GATEWAY_CLUSTER is
        # the likely cause — we told users to export it.
        hint = (
            f"unset {GATEWAY_CLUSTER_ENV} to let lc discover your "
            "running cluster, or point it at one that is running"
            if named
            else "it may have just stopped — check the JupyterLab Dask panel"
        )
        raise RuntimeError(
            f"Could not connect to Dask Gateway cluster {name!r} "
            f"({exc}); {hint}."
        ) from exc

    if verbose:
        print(
            f"→ Attached to Dask Gateway cluster {cluster.name} "
            f"(dashboard: {cluster.dashboard_link})"
        )

    try:
        client = cluster.get_client()
        try:
            # Verify the deployment contract only against what's live:
            # an adaptive cluster sitting at zero workers will scale
            # once the executor submits tasks, so an empty worker set
            # is not an error here.
            _assert_worker_resources(client)
            _check_worker_image(client, expected=expected_worker_image)
        finally:
            client.close()
        yield cluster.name
    finally:
        # Releases this process's connections only — the cluster (and
        # its scaling) belongs to the user.
        cluster.close()


def _discover_cluster_name(
    gateway: object, expected_worker_image: str | None
) -> str:
    """Name of the caller's single running Gateway cluster.

    Raises with actionable instructions when there isn't exactly one:
    creation is deliberately not offered here (the user creates clusters
    from JupyterLab), so the error message *is* the UX — it must say
    precisely what to do next.
    """
    reports = gateway.list_clusters()  # type: ignore[attr-defined]
    if len(reports) == 1:
        return str(reports[0].name)

    if not reports:
        image_arg = (
            f'image="{expected_worker_image}", ' if expected_worker_image else ""
        )
        # shutdown_on_close=False matters: without it the cluster dies
        # with the creating kernel/process, killing any lc run attached
        # to it. The deployment's idle_timeout reaps forgotten clusters.
        raise RuntimeError(
            "No Dask Gateway cluster is running for your user. Create one "
            "first — from the JupyterLab Dask sidebar (+ NEW), or in a "
            "notebook:\n\n"
            "    from dask_gateway import Gateway\n"
            f"    cluster = Gateway().new_cluster({image_arg}"
            "shutdown_on_close=False)\n"
            "    cluster.adapt(minimum=1, maximum=8)\n\n"
            "then re-run `lc run` (it attaches to your running cluster "
            "and leaves it up)."
        )

    names = ", ".join(str(r.name) for r in reports)
    raise RuntimeError(
        f"Multiple Dask Gateway clusters are running for your user "
        f"({names}). Pick one by setting {GATEWAY_CLUSTER_ENV}, e.g.:\n\n"
        f"    export {GATEWAY_CLUSTER_ENV}={reports[0].name}\n\n"
        "or shut the extras down from the JupyterLab Dask sidebar."
    )


def _check_worker_image(client: object, *, expected: str | None) -> None:
    """Warn when the attached cluster doesn't run the project's image.

    The scheduler pod carries the same ``LIGHTCONE_WORKER_IMAGE`` env
    the deployment injects into workers (Gateway applies cluster-config
    ``environment`` to both), so this works even while an adaptive
    cluster sits at zero workers. Read via a lambda — cloudpickled by
    value — so the scheduler pod does not need lightcone importable.

    A warning, not an error: the user may be knowingly iterating on a
    stale cluster, and the manifest layer records the *actual* image
    (ground truth) either way. Silently skipped when the deployment
    doesn't inject the marker or no expectation was computed.
    """
    if expected is None:
        return
    try:
        env_key = WORKER_IMAGE_ENV  # captured by value into the lambda
        actual = client.run_on_scheduler(  # type: ignore[attr-defined]
            lambda: __import__("os").environ.get(env_key)
        )
    except Exception:
        return  # older deployment / restricted scheduler — not fatal
    if actual and actual != expected:
        print(
            f"⚠ The attached Dask Gateway cluster runs image\n"
            f"    {actual}\n"
            f"  but this project's container resolves to\n"
            f"    {expected}\n"
            f"  Recipes will execute in the cluster's image; manifests "
            f"record what actually ran.\n"
            f"  To use the project image, create a new cluster with "
            f'image="{expected}".'
        )


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
    # Require cpus AND memory together on at least one worker: the
    # executor requests cpus for every rule (threads >= 1) and memory
    # for any rule with mem_mb, so a partial injection (cpus without
    # memory) still hangs mem_mb rules forever — the exact silent
    # failure this check exists to catch. gpus is not required: a
    # CPU-only deployment may legitimately omit it, and rules that
    # request GPUs on such a cluster are a real scheduling constraint,
    # not a forgotten contract.
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
        "would never schedule. Fix the gateway deployment to inject "
        "DASK_DISTRIBUTED__WORKER__RESOURCES__* into worker pods (see "
        "the lightcone-hub dask-gateway values)."
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
