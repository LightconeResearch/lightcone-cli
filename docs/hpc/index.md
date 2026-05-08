# HPC & SLURM (consolidated)

The standalone HPC subsystem (target files, site registry, sbatch
generation) is gone. SLURM execution is now handled by Dask: when `lc run`
is invoked inside an existing SLURM allocation, the cluster manager
launches one `dask worker` per allocated node via `srun` and Snakemake
dispatches each rule across them.

For the user-facing flow, see [Running on a Cluster](../user/cluster.md).

For maintainer detail:

- [api/dask_cluster](../api/dask_cluster.md) — the three-branch decision
  (existing scheduler / SLURM allocation / local).
- [api/dask_executor](../api/dask_executor.md) — the Snakemake executor
  plugin that turns each rule into a `client.submit(...)` call.
- [api/container](../api/container.md) — `podman-hpc` build & migrate.
