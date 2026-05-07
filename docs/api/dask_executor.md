# snakemake_executor_plugin_dask

Snakemake executor plugin that submits each rule as a `client.submit()`
on a `dask.distributed` cluster. Lives at the top of `src/` because
Snakemake discovers executor plugins through the
`snakemake_executor_plugin_*` package-naming convention.

Source: `src/snakemake_executor_plugin_dask/`.

## Module shape

```
snakemake_executor_plugin_dask/
├── __init__.py            # plugin metadata + Executor re-export
└── executor.py            # DaskExecutor class
```

## Plugin metadata

```python
common_settings = CommonSettings(
    job_deploy_sources=True,    # send Snakefile + sources to workers
    non_local_exec=True,         # workers may live elsewhere
    implies_no_shared_fs=False,  # we *do* assume a shared FS
)
```

We assume a shared filesystem because all our workers (local threads,
SLURM nodes) see the project tree the same way. If you change this,
you also need to teach `wrap_recipe()` not to use `$PWD` bind mounts.

## `DaskExecutor`

Inherits from `snakemake_interface_executor_plugins.executors.remote.RemoteExecutor`.

### `__init__(workflow, logger)`

Imports `dask.distributed` lazily; raises `WorkflowError` if missing
("`pip install distributed`"). Reads `DASK_SCHEDULER_ADDRESS` from the
environment and opens a `Client(addr)`. Raises `WorkflowError` if the
env var is unset — `lc run` is responsible for setting it.

### `run_job(job)`

Translate a Snakemake job to a Dask submission:

```python
client.submit(
    _run_shell, job.format_job_exec(),
    resources=_build_resources(job),
    pure=False,
    key=f"snakejob-{job.name}-{job.jobid}",
)
```

`_run_shell` just runs `subprocess.run(cmd, shell=True, check=False)`
on the worker and returns the exit code. The recipe is already
container-wrapped at Snakefile generation time, so the worker has no
runtime logic of its own.

### `check_active_jobs(active_jobs)`

Async generator: for each submitted job, check `future.done()`. Yield
back jobs that are still in flight. For finished jobs:

- `future.exception() is not None` → `report_job_error(...)`
- `future.result() != 0` → `report_job_error(...)`
- otherwise → `report_job_success(...)`

### `cancel_jobs(active_jobs)`

Best-effort `future.cancel()` on each in-flight job. **Does not** close
the `Client` — Snakemake calls `cancel_jobs` for partial cancellations
as well as at shutdown, so closing here would break subsequent
submissions. The client is closed in `shutdown()` exclusively.

### `shutdown()`

`self._client.close()` then `super().shutdown()`.

## Resource translation

```python
def _build_resources(job) -> dict[str, float]:
    res = {}
    cpus = job.resources.get("cpus_per_task") or job.threads
    if cpus: res["cpus"] = float(cpus)
    mem_mb = job.resources.get("mem_mb")
    if mem_mb: res["memory"] = float(mem_mb) * 1e6        # MB → bytes
    gpus = job.resources.get("gpus_per_task") or job.resources.get("gpus")
    if gpus: res["gpus"] = float(gpus)
    return res
```

Returns `None` if the resulting dict is empty (Dask's "no constraints"
sentinel). Resource keys must match those advertised by workers
(`cpus`, `memory`, `gpus` — see [`engine.dask_cluster`](dask_cluster.md)).

## Tests

`tests/test_dask_plugin.py` exercises the `_build_resources` mapping
and the executor's lifecycle (init / run / check / shutdown) against a
local `LocalCluster` fixture.
