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

from lightcone.engine.dask_cluster import (
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


class DaskExecutor(RemoteExecutor):  # type: ignore[misc]
    def __init__(self, workflow, logger):  # type: ignore[no-untyped-def]
        super().__init__(workflow, logger)
        try:
            from dask.distributed import Client
        except ImportError as exc:
            raise WorkflowError(
                "dask.distributed is required for the dask executor "
                "(`pip install distributed`)."
            ) from exc

        addr = os.environ.get("DASK_SCHEDULER_ADDRESS")
        if not addr:
            raise WorkflowError(
                "DASK_SCHEDULER_ADDRESS is not set. `lc run` should set this "
                "before invoking snakemake; if you're calling snakemake "
                "directly, point it at a running dask scheduler."
            )
        self._client = Client(addr)

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
        finally:
            super().shutdown()
