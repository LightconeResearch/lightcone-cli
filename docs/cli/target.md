# lc target (removed)

The target/options subsystem (per-machine targets with QoS, constraint, and
time-limit options) was removed. lightcone-cli now resolves the container
runtime and the cluster shape from a much smaller surface:

- **Container runtime** — `~/.lightcone/config.yaml` carries a single
  `container.runtime` key (`auto | docker | podman | podman-hpc | none`).
  See [`lc setup`](setup.md) and [api/container](../api/container.md).
- **Cluster shape** — derived at runtime from the environment.
  `lc run` always dispatches through a Dask cluster; the cluster manager
  picks `LocalCluster` on a workstation, `srun`-launched workers when
  `SLURM_JOB_ID` is set, or whatever scheduler is on
  `DASK_SCHEDULER_ADDRESS` if you are connecting to one. See
  [api/dask_cluster](../api/dask_cluster.md).

There are no project-level target files, no `.lightcone/lightcone.yaml`
shape beyond an opaque scratchpad, and no `lc target` command.
