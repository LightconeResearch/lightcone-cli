# HPC & SLURM

Prism supports executing ASTRA recipes on SLURM-based HPC clusters. The SLURM backend generates `sbatch` scripts from recipe definitions, submits them, and polls for completion.

## Supported sites

| Site | Key | Container runtime |
|------|-----|------------------|
| NERSC Perlmutter | `perlmutter` | `podman-hpc` |
| Any SLURM cluster | (custom) | `singularity`, `docker`, `podman` |

## Quick start

```bash
# Configure a target (one time)
prism setup   # select Perlmutter or custom SLURM

# Build containers
prism build --runtime podman-hpc

# Run on SLURM
prism run --target perlmutter-gpu

# With SLURM scheduling options
prism run --qos debug --constraint gpu --time 30:00
```

## Interactive allocation

On login nodes, `prism run` submits batch jobs and waits for them. For faster iteration, start an interactive allocation first:

```bash
salloc --nodes=1 --qos=interactive --constraint=gpu --time=01:00:00 --account=m4031_g
# → now on a compute node
prism run   # executes instantly via srun
```

Prism detects an existing `SLURM_JOB_ID` and uses `srun` instead of `sbatch` when inside an allocation.

## See also

- [Site Registry](site-registry.md) — how site defaults work and how to add new sites
- [Target Configuration](targets.md) — target YAML format reference
- [Container Builds](containers.md) — `podman-hpc` build and migrate workflow
