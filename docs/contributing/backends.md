# Adding an Execution Backend

The `ASTRAContainerRunner` plugin point is gone. Execution is structured
quite differently now, and "adding a backend" decomposes into one or more
of these:

## Adding a container runtime

The OCI runtimes are `podman-hpc`, `podman`, and `docker`, listed in
`src/lightcone/engine/container.py::RUNTIMES` in detection-priority
order (plus the `none` no-op). To add another OCI CLI:

1. Append the binary name to `RUNTIMES` (the tuple order *is* the
   detection priority).
2. If detection needs a probe (like the docker-daemon ping), extend
   `detect_runtime()`.
3. If `wrap_recipe()` needs different flags for the runtime, branch on
   `runtime` there.
4. If post-build migration is required (the `podman-hpc migrate` model),
   add a `_<runtime>_migrate(tag)` and call it from `build_image()` /
   `pull_image()`.
5. Add tests in `tests/test_container.py`.

### `kubernetes` is deliberately *not* in `RUNTIMES`

`KUBERNETES = "kubernetes"` is a separate constant on purpose, and
appending it to `RUNTIMES` would be wrong: `RUNTIMES` means "a binary we
can find on PATH and shell out to", and the guards in `build_image()`,
`pull_image()`, and `wrap_recipe()` reject anything outside it.

The `kubernetes` path is structurally different at every step:

| Step | `RUNTIMES` member | `kubernetes` |
|------|-------------------|--------------|
| Selection | PATH probe, or explicit pin | site detection (`jupyterhub`) or explicit pin — never PATH |
| Build | `<runtime> build` locally | [`engine.cloudbuild.ensure_image()`](../api/cloudbuild.md) |
| Image identity | `lc-<name>-<digest>` local tag | `<registry>/lc-<name>:<digest>` registry ref |
| Freshness | local image-store inspect | registry HEAD |
| Run | `wrap_recipe()` wraps the recipe | passthrough — the worker pod *is* the container |

A genuinely new *non-CLI* backend therefore follows the `kubernetes`
shape instead: its own constant, its own branches in `detect_runtime()` /
`load_runtime()` / `runtime_registry()` / `get_container_status()`, and a
passthrough (or bespoke) `wrap_recipe()` case.

## Adding a Dask cluster shape

The cluster manager has four branches, checked in this order: existing
scheduler (`DASK_SCHEDULER_ADDRESS`), Dask Gateway
(`DASK_GATEWAY__ADDRESS`), SLURM allocation (`SLURM_JOB_ID`), local. To
add a fifth (for example, a custom GPU farm):

1. Add a branch to `cluster_for_run()` in
   `src/lightcone/engine/dask_cluster.py`, in the right priority
   position.
2. Yield the env overlay the child snakemake needs to reach it.
   Address-based branches yield `{"DASK_SCHEDULER_ADDRESS": addr}`; a
   scheduler that can't be dialled directly needs its own rendezvous
   variable (the Gateway branch yields `LIGHTCONE_GATEWAY_CLUSTER` and
   the executor rejoins by name through the Gateway API).
3. Make sure workers advertise the same resource keys (`cpus`, `memory`,
   `gpus`) so the [Snakemake executor plugin](../api/dask_executor.md)
   can match. Dask schedules a task only on workers advertising *every*
   requested key, so a missing key hangs silently instead of erroring —
   fail fast on it, as `_assert_worker_resources()` does.
4. Own the lifetime: if you create the cluster, tear it down on exit.
5. If `lc run` must know about the branch *before* entering the context
   (as it does for the Gateway one-image-per-run check), expose a pure
   predicate alongside `gateway_branch_active()`.
6. Add tests in `tests/test_dask_cluster.py`.

## Adding a non-Snakemake executor

In principle Snakemake supports multiple executors and we ship one
(`snakemake_executor_plugin_dask`). If you need a different scheduler,
you can write another Snakemake executor plugin and pass it through
`lc run --executor <name>` — but that flag does not exist today: the
executor is hard-coded in
`src/lightcone/cli/commands.py::_build_snakemake_cmd`.
