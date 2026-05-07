# Adding an HPC Site (deprecated)

There is no longer a meaningful concept of "adding an HPC site" — the
target system that this used to feed is gone. The `site_registry` module
is still present in the source tree but unused. See
[api/site_registry](../api/site_registry.md).

If you want lightcone-cli to behave well on a new cluster, what you
actually need is:

1. **A container runtime that works on compute nodes.** `podman-hpc` is
   the supported case. Wire it up via `~/.lightcone/config.yaml`.
2. **Dask workers reachable from the scheduler.** `lc run` already does
   the right thing inside an `salloc`/`sbatch` allocation — the cluster
   manager binds the scheduler to the SLURM canonical hostname and
   launches one worker per node via `srun`. See
   [api/dask_cluster](../api/dask_cluster.md).
3. **A scratch path that the agent should not edit.** Today's permission
   tiers hard-code Perlmutter scratch deny rules; if you add a new
   site, update `PERMISSION_TIERS` in
   `src/lightcone/cli/commands.py`.
