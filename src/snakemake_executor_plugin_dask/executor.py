# mypy: disable-error-code="no-untyped-call"
from __future__ import annotations

import os
import subprocess
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


def _run_shell(cmd: str) -> int:
    """Worker-side: run the shell command and return its exit code.

    Stdout/stderr stream to whatever the worker is configured to log.
    """
    return subprocess.run(cmd, shell=True, check=False).returncode


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
        for j in active_jobs:
            future = j.aux.get("future")
            if future is not None and not future.done():
                try:
                    future.cancel()
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(
                        f"Failed to cancel dask task {j.external_jobid}: {exc}"
                    )
        self.shutdown()

    def shutdown(self) -> None:
        try:
            self._client.close()
        finally:
            super().shutdown()
