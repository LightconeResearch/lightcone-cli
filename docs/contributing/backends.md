# Adding an Execution Backend (rewritten)

The `ASTRAContainerRunner` plugin point is gone. Execution is structured
quite differently now, and "adding a backend" decomposes into one or both
of these:

## Adding a container runtime

The supported runtimes are `docker`, `podman`, and `podman-hpc` (plus the
`none` no-op). They are listed in
`src/lightcone/engine/container.py::RUNTIMES`. To add a new one:

1. Append the binary name to `RUNTIMES` (detection priority is the tuple
   order).
2. If detection needs a probe (like the docker-daemon ping), extend
   `detect_runtime()`.
3. If `wrap_recipe()` needs different flags for the runtime, branch on
   `runtime` there.
4. If post-build migration is required (the `podman-hpc migrate` model),
   add a `_<runtime>_migrate(tag)` and call it from `build_image()` /
   `pull_image()`.
5. Add tests in `tests/test_container.py`.

## Adding a Dask cluster shape

Today the cluster manager has three branches: existing scheduler, SLURM
allocation, local. To add a fourth (for example, a custom GPU farm):

1. Add a branch to `cluster_for_run()` in
   `src/lightcone/engine/dask_cluster.py`.
2. Make sure it advertises the same resource keys (`cpus`, `memory`,
   `gpus`) so the [Snakemake executor plugin](../api/dask_executor.md)
   can match.
3. Add tests in `tests/test_dask_cluster.py`.

## Adding a non-Snakemake executor

In principle Snakemake supports multiple executors and we ship one
(`snakemake_executor_plugin_dask`). If you need a different scheduler,
you can write another Snakemake executor plugin and pass it through
`lc run --executor <name>` — but that flag does not exist today and would
need to be added to `src/lightcone/cli/commands.py::run`.
