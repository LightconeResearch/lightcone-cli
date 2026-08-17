"""Snakemake executor plugin: dispatches each rule's shell command to a
running ``dask.distributed`` cluster.

The cluster rendezvous is read from the environment: a scheduler address
in ``DASK_SCHEDULER_ADDRESS``, set by ``lc materialize`` when it
constructs the run-scoped ``LocalCluster``.

The plugin is intentionally minimal: each Snakemake job becomes a
``client.submit(_run_shell, cmd, resources={...})`` call. Workers run
the shell command as-is.
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
