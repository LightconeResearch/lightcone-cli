"""Snakemake executor plugin: dispatches each rule's shell command to a
running ``dask.distributed`` cluster.

The scheduler address is read from ``DASK_SCHEDULER_ADDRESS`` in the
environment. ``lc run`` is responsible for setting that — typically by
constructing a ``LocalCluster()`` for the duration of the run, optionally
backed by ``srun``-launched workers when invoked inside an SLURM allocation.

The plugin is intentionally minimal: each Snakemake job becomes a
``client.submit(_run_shell, cmd, resources={...})`` call. Workers run the
shell command as-is (recipes are already containerized at Snakefile
generation time, so the worker just shells out).
"""

from snakemake_interface_executor_plugins.settings import (  # type: ignore[import-untyped]
    CommonSettings,
)

from .executor import DaskExecutor as Executor  # noqa: F401

common_settings = CommonSettings(
    job_deploy_sources=True,
    non_local_exec=True,
    implies_no_shared_fs=False,
)
