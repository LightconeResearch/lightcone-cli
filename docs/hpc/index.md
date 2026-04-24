# HPC & SLURM

lightcone-cli supports executing ASTRA recipes on SLURM-based HPC clusters. The SLURM backend generates `sbatch` scripts from recipe definitions, submits them, and polls for completion.

## Supported sites

| Site | Key | Container runtime |
|------|-----|------------------|
| NERSC Perlmutter | `perlmutter` | `podman-hpc` |
| Any SLURM cluster | (custom) | `singularity`, `docker`, `podman` |

## Quick start

```bash
# Configure a target (one time)
lc setup   # select Perlmutter or custom SLURM

# Build containers
lc build --runtime podman-hpc

# Run on SLURM
lc run --target perlmutter-gpu

# With SLURM scheduling options
lc run --qos debug --constraint gpu --time 30:00
```

## Interactive iteration

From a login node, an HPC target submits batch jobs and waits in the queue — fine for production runs, slow for iteration. For fast turnaround, grab an interactive compute node yourself and point the project at the `local` target while you're on it:

```bash
# From the login node:
salloc --nodes=1 --qos=interactive --constraint=gpu --time=01:00:00 --account=m4031_g
# → now in a shell on a compute node

lc target --set local    # runs execute here, no queueing
lc run                   # executes immediately, using the node you just allocated
```

When you're done iterating, `lc target --set <hpc-target>` switches back to queued submission for production runs.

lightcone-cli deliberately does **not** auto-detect allocations: recipe resource declarations and QoS limits only apply on the scheduler path, and silently bypassing them would be surprising. Switching the target makes the mode explicit.

## See also

- [Site Registry](site-registry.md) — how site defaults work and how to add new sites
- [Target Configuration](targets.md) — target YAML format reference
- [Container Builds](containers.md) — `podman-hpc` build and migrate workflow
