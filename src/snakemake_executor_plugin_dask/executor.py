# mypy: disable-error-code="no-untyped-call"
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from collections.abc import AsyncGenerator

from snakemake_interface_common.exceptions import WorkflowError
from snakemake_interface_executor_plugins.executors.base import (  # type: ignore[import-untyped]
    SubmittedJobInfo,
)
from snakemake_interface_executor_plugins.executors.remote import (  # type: ignore[import-untyped]
    RemoteExecutor,
)
from snakemake_interface_executor_plugins.jobs import (  # type: ignore[import-untyped]
    JobExecutorInterface,
)
from snakemake_interface_executor_plugins.utils import (  # type: ignore[import-untyped]
    format_cli_arg,
    join_cli_args,
)

from lightcone.engine.dask_cluster import (
    GATEWAY_CLUSTER_ENV,
    RESOURCE_CPUS,
    RESOURCE_GPUS,
    RESOURCE_MEMORY,
)
from lightcone.engine.runner import SENTINEL


def _run_shell(cmd: str) -> int:
    """Worker-side: run the child snakemake command, forward its lightcone
    output, and return its exit code.

    The command is a child snakemake invocation that loads the generated
    Snakefile and executes one rule's ``run:`` block. That block calls
    :func:`lightcone.engine.runner.run_rule`, which streams structured
    output prefixed with :data:`lightcone.engine.runner.SENTINEL`.

    We capture both stdout and stderr from the child snakemake; anything
    not prefixed (snakemake's bootstrap, dask noise, stray prints) is
    dropped. Lightcone-prefixed lines are forwarded to *our* stdout —
    inherited from ``lc run``'s terminal across local LocalCluster
    workers and srun-launched remote workers alike — as one atomic block
    per rule, serialised across workers and nodes by an ``flock`` on the
    path pointed to by ``LIGHTCONE_OUT_LOCK``.

    The lockfile must live on a filesystem that supports advisory locks.
    On NERSC, ``$HOME`` and ``/global/cfs`` are mounted on compute nodes
    via DVS, which silently swallows ``flock``; lc run resolves the path
    onto Lustre via :mod:`lightcone.engine.scratch`.
    """
    p = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, check=False
    )

    forwarded: list[str] = []
    for stream in (p.stdout, p.stderr):
        for line in stream.splitlines():
            if line.startswith(SENTINEL):
                forwarded.append(line[len(SENTINEL):])
    if p.returncode != 0:
        # The child failed: its own diagnostics are the only clue, and
        # they exist solely in this worker process. Forward a bounded
        # tail so the failure is debuggable from the driver terminal
        # (exit 127 with no output is a debugging dead end).
        tail = (p.stderr.strip() or p.stdout.strip()).splitlines()[-15:]
        forwarded.append(
            f"✗ worker-side snakemake exited {p.returncode}; last output:"
        )
        forwarded.extend(f"    {line}" for line in tail)
    if forwarded:
        block = "\n".join(forwarded) + "\n"
        lock_path = os.environ.get("LIGHTCONE_OUT_LOCK")
        if lock_path:
            with open(lock_path, "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    sys.stdout.write(block)
                    sys.stdout.flush()
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
        else:
            sys.stdout.write(block)
            sys.stdout.flush()

    return p.returncode


def _build_resources(job: JobExecutorInterface) -> dict[str, float]:
    """Translate Snakemake resources to Dask abstract resource units."""
    res: dict[str, float] = {}
    cpus = job.resources.get("cpus_per_task") or job.threads
    if cpus:
        res[RESOURCE_CPUS] = float(cpus)
    mem_mb = job.resources.get("mem_mb")
    if mem_mb:
        res[RESOURCE_MEMORY] = float(mem_mb) * 1e6
    gpus = job.resources.get("gpus_per_task") or job.resources.get("gpus")
    if gpus:
        res[RESOURCE_GPUS] = float(gpus)
    return res


def _connect_client():  # type: ignore[no-untyped-def]
    """Resolve the Dask client from the environment.

    Mirrors the branches of ``lightcone.engine.dask_cluster.cluster_for_run``
    from the child process side, **in the same priority order**: a plain
    address in ``DASK_SCHEDULER_ADDRESS`` wins first (matching the parent,
    where an explicit address outranks a Gateway environment); otherwise a
    Gateway cluster is rejoined *by name* through the authenticated Gateway
    API (its ``gateway://`` scheduler address cannot be dialled by a bare
    ``Client``). The parent additionally strips the losing variable from
    the child env (see ``lc run``), so a stale ``LIGHTCONE_GATEWAY_CLUSTER``
    lingering in a user's shell can never redirect the child to a cluster
    the parent didn't verify.

    Returns ``(client, gateway_cluster_or_None)`` — the cluster handle is
    kept so ``shutdown()`` can release its local connections. It is never
    shut down here: on the Gateway path the cluster belongs to the *user*
    (lc is attach-only); on the address paths it belongs to whoever
    started the scheduler.
    """
    if addr := os.environ.get("DASK_SCHEDULER_ADDRESS"):
        try:
            from dask.distributed import Client
        except ImportError as exc:
            raise WorkflowError(
                "dask.distributed is required for the dask executor "
                "(`pip install distributed`)."
            ) from exc
        return Client(addr), None

    if name := os.environ.get(GATEWAY_CLUSTER_ENV):
        try:
            from dask_gateway import Gateway
        except ImportError as exc:
            raise WorkflowError(
                f"{GATEWAY_CLUSTER_ENV} is set but the dask-gateway client "
                "is not installed (`pip install lightcone-cli[gateway]`)."
            ) from exc
        cluster = Gateway().connect(name)
        return cluster.get_client(), cluster

    raise WorkflowError(
        f"Neither DASK_SCHEDULER_ADDRESS nor {GATEWAY_CLUSTER_ENV} is "
        "set. `lc run` should set one before invoking snakemake; if "
        "you're calling snakemake directly, point it at a running dask "
        "scheduler."
    )


class DaskExecutor(RemoteExecutor):  # type: ignore[misc]
    def __init__(self, workflow, logger):  # type: ignore[no-untyped-def]
        super().__init__(workflow, logger)
        self._client, self._gateway_cluster = _connect_client()

    def get_python_executable(self) -> str:
        """Python for the child snakemake command.

        The default (``sys.executable``) is right when driver and
        workers share an environment — local LocalCluster workers and
        srun-launched SLURM workers inherit the driver's venv, so the
        absolute path exists there. On Dask Gateway the driver (e.g. a
        JupyterLab pod) and the workers run *different images*: the
        driver's interpreter path need not exist in the worker pod
        (conda notebook vs pip-slim worker is exactly this), which
        fails with exit 127 before snakemake even starts. The worker
        image is the software deployment, so let the worker's own PATH
        resolve the interpreter.
        """
        if self._gateway_cluster is not None:
            return "python3"
        return super().get_python_executable()  # type: ignore[no-any-return]

    def additional_general_args(self) -> str:
        """Pin the child snakemake's working directory explicitly.

        Local and SLURM workers inherit the driver's cwd (LocalCluster
        forks in place; srun preserves the submit directory), so the
        child's relative output paths land in the project by accident
        of process ancestry. Gateway worker pods start in their own
        HOME — without ``--directory``, a rule "succeeds" while writing
        results into the pod's ephemeral filesystem. The parent
        snakemake chdir'd into the project (``lc run`` passes ``-d``),
        so ``os.getcwd()`` here *is* the project directory — a path
        valid on every worker because lc requires a shared project
        filesystem (``implies_no_shared_fs=False``).
        """
        return join_cli_args(  # type: ignore[no-any-return]
            [
                super().additional_general_args(),
                format_cli_arg("--directory", os.getcwd()),
            ]
        )

    def run_job(self, job: JobExecutorInterface) -> None:
        cmd = self.format_job_exec(job)
        self.logger.debug(cmd)

        resources = _build_resources(job)
        future = self._client.submit(
            _run_shell,
            cmd,
            resources=resources or None,
            pure=False,
            key=f"snakejob-{job.name}-{job.jobid}",
        )

        self.report_job_submission(
            SubmittedJobInfo(job, external_jobid=future.key, aux={"future": future})
        )

    async def check_active_jobs(
        self, active_jobs: list[SubmittedJobInfo]
    ) -> AsyncGenerator[SubmittedJobInfo, None]:
        for j in active_jobs:
            future = j.aux["future"]
            if not future.done():
                yield j
                continue

            exc = future.exception()
            if exc is not None:
                self.report_job_error(
                    j, msg=f"Dask task '{j.external_jobid}' raised: {exc!r}"
                )
                continue

            exit_code = future.result()
            if exit_code != 0:
                self.report_job_error(
                    j, msg=f"Dask task '{j.external_jobid}' exited {exit_code}."
                )
            else:
                self.report_job_success(j)

    def cancel_jobs(self, active_jobs: list[SubmittedJobInfo]) -> None:
        # Snakemake calls cancel_jobs for partial cancellations as well as
        # at terminal shutdown, so we MUST NOT close the client here —
        # that would break any subsequent submissions in the same run.
        # The client is closed in shutdown() exclusively.
        for j in active_jobs:
            future = j.aux.get("future")
            if future is not None and not future.done():
                try:
                    future.cancel()
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(
                        f"Failed to cancel dask task {j.external_jobid}: {exc}"
                    )

    def shutdown(self) -> None:
        try:
            self._client.close()
            if self._gateway_cluster is not None:
                # Releases this process's connections only; the cluster
                # itself belongs to the user (lc is attach-only and never
                # shuts Gateway clusters down — see dask_cluster).
                self._gateway_cluster.close()
        finally:
            super().shutdown()
