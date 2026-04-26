from __future__ import annotations

import os
import shlex
from collections.abc import AsyncGenerator
from typing import Any

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

try:
    import flux  # type: ignore[import-not-found]
    import flux.job  # type: ignore[import-not-found]
    from flux.job import JobspecV1
except ImportError:
    flux = None


def _inject_memory(resources: list[dict[str, Any]], mem_mb: int) -> None:
    """Add a `memory` resource under every slot in a jobspec resource tree.

    `from_command` builds a tree with `slot` nodes that hold `core` (and
    optionally `gpu`) children. Adding `memory` as a sibling expresses the
    per-slot memory requirement in RFC 14 form.
    """
    for r in resources:
        if r.get("type") == "slot":
            r.setdefault("with", []).append(
                {"type": "memory", "count": mem_mb, "unit": "MB"}
            )
        if "with" in r:
            _inject_memory(r["with"], mem_mb)


class LightconeFluxExecutor(RemoteExecutor):  # type: ignore[misc]
    def __init__(self, workflow, logger):  # type: ignore[no-untyped-def]
        super().__init__(workflow, logger)
        self.workdir = os.path.realpath(os.path.dirname(self.workflow.persistence.path))
        if flux is None:
            raise WorkflowError(
                "Cannot import flux. Install flux-core with Python bindings, "
                "or `module load flux` on HPC sites."
            )
        self._fexecutor = flux.job.FluxExecutor()
        self._flux_handle = flux.Flux()  # for cancellation

    def get_envvar_declarations(self) -> str:
        return " ".join(
            f"{var}={os.environ[var]!r}"
            for var in self.workflow.remote_execution_settings.envvars or {}
        )

    def run_job(self, job: JobExecutorInterface) -> None:
        flux_logfile = job.logfile_suggestion(os.path.join(".snakemake", "flux_logs"))
        os.makedirs(os.path.dirname(flux_logfile), exist_ok=True)

        command = self.format_job_exec(job)
        self.logger.debug(command)

        fluxjob = JobspecV1.from_command(command=shlex.split(command))
        fluxjob.duration = job.resources.get("runtime", 0)
        fluxjob.stderr = flux_logfile
        fluxjob.cwd = self.workdir
        fluxjob.environment = dict(os.environ)

        cpus_per_task = max(1, int(job.resources.get("cpus_per_task") or job.threads))
        fluxjob.cpus_per_task = cpus_per_task

        gpus = job.resources.get("gpus_per_task") or job.resources.get("gpus")
        if gpus:
            fluxjob.gpus_per_task = int(gpus)

        nodes = job.resources.get("nodes")
        if nodes:
            fluxjob.num_nodes = int(nodes)

        mem_mb = job.resources.get("mem_mb")
        if mem_mb:
            _inject_memory(fluxjob.resources, int(mem_mb))

        flux_future = self._fexecutor.submit(fluxjob)
        aux = {"flux_future": flux_future, "flux_logfile": flux_logfile}
        self.report_job_submission(
            SubmittedJobInfo(job, external_jobid=str(flux_future.jobid()), aux=aux)
        )

    async def check_active_jobs(
        self, active_jobs: list[SubmittedJobInfo]
    ) -> AsyncGenerator[SubmittedJobInfo, None]:
        for j in active_jobs:
            flux_future = j.aux["flux_future"]
            aux_logs = [j.aux["flux_logfile"]]

            if not flux_future.done():
                yield j
                continue

            try:
                exit_code = flux_future.result(0)
            except RuntimeError:
                self.report_job_error(
                    j, msg=f"Flux job '{j.external_jobid}' failed.", aux_logs=aux_logs
                )
                continue

            if exit_code != 0:
                self.report_job_error(
                    j,
                    msg=f"Flux job '{j.external_jobid}' exited {exit_code}.",
                    aux_logs=aux_logs,
                )
            else:
                self.report_job_success(j)

    def cancel_jobs(self, active_jobs: list[SubmittedJobInfo]) -> None:
        for j in active_jobs:
            flux_future = j.aux["flux_future"]
            if flux_future.done():
                continue
            try:
                flux.job.cancel(self._flux_handle, int(j.external_jobid))
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"Failed to cancel flux job {j.external_jobid}: {exc}")
        self.shutdown()
