"""Snakemake executor plugin: dispatches each rule's shell command to a
running ``dask.distributed`` cluster.

The cluster rendezvous is read from the environment: either a plain
scheduler address in ``DASK_SCHEDULER_ADDRESS`` or a Dask Gateway
cluster name in ``LIGHTCONE_GATEWAY_CLUSTER`` (rejoined through the
Gateway API — ``gateway://`` schedulers cannot be dialled directly).
``lc run`` is responsible for setting one of them — typically by
constructing a ``LocalCluster()`` for the duration of the run, backed by
``srun``-launched workers inside a SLURM allocation, or by creating a
run-scoped Gateway cluster on a JupyterHub deployment.

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
