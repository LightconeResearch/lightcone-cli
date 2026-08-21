# Adding an HPC Site

The old target system is gone; what remains is the lightweight
[`site_registry`](../api/site_registry.md) module, which supplies
per-site defaults (scratch root, preferred container runtime) via
`detect_current_site()`.

If you want lightcone-cli to behave well on a new cluster, what you
actually need is:

1. **A container runtime that works on compute nodes.** `podman-hpc` is
   the supported case. Wire it up via `~/.lightcone/config.yaml`, or
   declare it as the site's `container_runtime` in `SITE_DEFAULTS`.
2. **Dask workers reachable from the scheduler.** `lc run` already does
   the right thing inside an `salloc`/`sbatch` allocation — the cluster
   manager binds the scheduler to the SLURM canonical hostname and
   launches one worker per node via `srun`. See
   [api/dask_cluster](../api/dask_cluster.md).
3. **A sane scratch root.** `lc run` keeps its operational state
   (snakemake metadata, dask spill, cross-node run locks) under a
   scratch root that must honour `flock` — on Perlmutter that means
   `$SCRATCH` (Lustre), not DVS-mounted home/CFS. Declare
   `scratch_root` in the site's `SITE_DEFAULTS` entry; users can
   override it per-project with `lc init --scratch`.
