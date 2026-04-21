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

## Interactive allocation

On login nodes, `lc run` submits batch jobs and waits for them. For faster iteration, start an interactive allocation first:

```bash
salloc --nodes=1 --qos=interactive --constraint=gpu --time=01:00:00 --account=m4031_g
# → now on a compute node
lc run   # executes instantly via srun
```

lightcone-cli detects an existing `SLURM_JOB_ID` and uses `srun` instead of `sbatch` when inside an allocation.

## See also

- [Site Registry](site-registry.md) — how site defaults work and how to add new sites
- [Target Configuration](targets.md) — target YAML format reference
- [Container Builds](containers.md) — `podman-hpc` build and migrate workflow
