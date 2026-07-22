# mypy: disable-error-code="no-untyped-call"
from __future__ import annotations

import os
import shlex
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
    GATEWAY_CLUSTER_ENV,
    RESOURCE_CPUS,
    RESOURCE_GPUS,
    RESOURCE_MEMORY,
)
from lightcone.engine.runner import SENTINEL

#: On a failure with no sentinel-framed output at all — the child
#: snakemake died before reaching the rule body (import error, missing
#: package in the worker image, broken snakefile) — forward this many
#: raw trailing lines so the failure is debuggable from the driver.
_RAW_TAIL_LINES = 60


def _run_shell(cmd: str) -> tuple[int, str]:
    """Worker-side: run the child snakemake command; return
    ``(exit_code, output_block)``.

    The command is a child snakemake invocation that loads the generated
    Snakefile and executes one rule's ``run:`` block. That block calls
    :func:`lightcone.engine.runner.run_rule`, which streams structured
    output prefixed with :data:`lightcone.engine.runner.SENTINEL`.

    The block travels back to the driver as part of the task result —
    the only channel that works uniformly across LocalCluster threads,
    srun-launched SLURM workers, and Dask Gateway worker pods (whose
    stdout goes to pod logs, not the user's terminal). Sentinel-prefixed
    lines are kept verbatim (prefix included: ``lc run`` filters on it);
    everything else (snakemake bootstrap, dask noise, stray prints) is
    dropped — unless the child failed without producing a single
    sentinel line, in which case a bounded raw tail is forwarded so
    bootstrap failures don't vanish into worker logs.
    """
    p = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, check=False
    )

    lines = [
        line
        for stream in (p.stdout, p.stderr)
        for line in stream.splitlines()
        if line.startswith(SENTINEL)
    ]
    if p.returncode != 0 and not lines:
        raw = (p.stdout + p.stderr).splitlines()[-_RAW_TAIL_LINES:]
        lines = [f"{SENTINEL}  {line}" for line in raw]

    block = "\n".join(lines) + "\n" if lines else ""
    return p.returncode, block


def _unpack_result(result: object) -> tuple[int, str]:
    """Accept both the current ``(exit_code, block)`` result and the
    bare ``int`` a worker running an older lightcone-cli release returns
    (dask resolves ``_run_shell`` by module path on the worker, so
    driver and worker versions can skew on image-based deployments)."""
    if isinstance(result, tuple) and len(result) == 2:
        return int(result[0]), str(result[1])
    return int(result), ""  # type: ignore[call-overload]


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
    """Connect to the run's cluster.

    Two rendezvous modes, both set up by ``lc run``:

    - :data:`GATEWAY_CLUSTER_ENV` names a Dask Gateway cluster the
      parent created. Gateway schedulers speak a ``gateway://`` comm
      scheme with per-cluster TLS credentials held by the Gateway API —
      a bare ``Client`` cannot dial them, so we rejoin through
      ``Gateway().connect(name)``.
    - Otherwise ``DASK_SCHEDULER_ADDRESS`` is a plain scheduler address.

    Returns ``(client, closer)`` where *closer* releases everything the
    rendezvous opened.
    """
    from dask.distributed import Client

    if name := os.environ.get(GATEWAY_CLUSTER_ENV):
        from dask_gateway import Gateway

        # shutdown_on_close=False: the parent lc run owns the cluster
        # lifecycle; the executor is a guest.
        cluster = Gateway().connect(name, shutdown_on_close=False)
        client = cluster.get_client()

        def closer() -> None:
            client.close()
            cluster.close()

        return client, closer

    addr = os.environ.get("DASK_SCHEDULER_ADDRESS")
    if not addr:
        raise WorkflowError(
            "Neither DASK_SCHEDULER_ADDRESS nor "
            f"{GATEWAY_CLUSTER_ENV} is set. `lc run` should set one "
            "before invoking snakemake; if you're calling snakemake "
            "directly, point it at a running dask scheduler."
        )
    client = Client(addr)
    return client, client.close


class DaskExecutor(RemoteExecutor):  # type: ignore[misc]
    def __init__(self, workflow, logger):  # type: ignore[no-untyped-def]
        super().__init__(workflow, logger)
        try:
            import dask.distributed  # noqa: F401
        except ImportError as exc:
            raise WorkflowError(
                "dask.distributed is required for the dask executor "
                "(`pip install distributed`)."
            ) from exc
        self._client, self._close_client = _connect_client()

    def get_job_exec_prefix(self, job: JobExecutorInterface) -> str:
        # Spawned job commands carry no --directory: snakemake expects
        # remote executors to cd into the workdir themselves (the
        # official kubernetes executor does the same). Local and SLURM
        # workers happen to inherit the driver's cwd, but a Dask
        # Gateway worker pod starts in its image's WORKDIR (e.g. /app),
        # where the child snakemake would resolve every relative path
        # — and die on a read-only ``.snakemake``.
        return f"cd {shlex.quote(self.workflow.workdir_init)}"

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

            exit_code, block = _unpack_result(future.result())
            if block:
                # One atomic write per finished rule. We run inside the
                # parent snakemake process, so this is naturally
                # serialised — no cross-process locking needed.
                sys.stdout.write(block)
                sys.stdout.flush()
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
            self._close_client()
        finally:
            super().shutdown()
