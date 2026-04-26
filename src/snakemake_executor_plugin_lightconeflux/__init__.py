"""Snakemake executor plugin: dispatches jobs to a running Flux instance.

Vendored and adapted from upstream ``snakemake-executor-plugin-flux``
(https://github.com/snakemake/snakemake-executor-plugin-flux), MIT licensed.

Differences from upstream:
- Maps ``gpus`` / ``gpus_per_task`` resources to ``JobspecV1.gpus_per_task``.
- Maps ``nodes`` resource to ``JobspecV1.num_nodes``.
- Fixes ``cancel_jobs`` (upstream references attributes that do not exist on
  ``SubmittedJobInfo``).
"""

from snakemake_interface_executor_plugins.settings import (  # type: ignore[import-untyped]
    CommonSettings,
)

from .executor import LightconeFluxExecutor as Executor  # noqa: F401

common_settings = CommonSettings(
    job_deploy_sources=True,
    non_local_exec=True,
    implies_no_shared_fs=False,
)
