# NERSC (Perlmutter)

Site-specific overlays for running lightcone-cli on NERSC Perlmutter. The generic [Install](install.md), [Getting Started](getting-started.md), and [Running on a Cluster](cluster.md) pages cover the main flow — this page documents only what's different on NERSC.

If you're new, the recommended order is:

1. [Install](install.md) — `lc` and Claude Code (skip the Python step; see below)
2. [Getting Started](getting-started.md) — `lc init my-analysis` and the agent workflow
3. This page — Perlmutter-specific overlays
4. [Running on a Cluster](cluster.md) — `lc build`, allocations, `lc run` inside SLURM

## 1. Python environment

[Install](install.md#1-python-311) assumes Python is already on your `PATH`. On Perlmutter, Python comes via modules:

```bash
module load conda                           # NERSC's miniconda
conda create -n your-env-name python=3.11 -y
conda activate your-env-name
```

Then continue with [Install §2](install.md#2-lightcone-cli) (`pip install lightcone-cli`).

> **Home quota is 40 GB on Perlmutter.** For larger envs, move the env to `$SCRATCH` and symlink the original location:
>
> ```bash
> conda deactivate
> mv ~/.conda/envs/your-env-name $SCRATCH/conda-envs/
> ln -s $SCRATCH/conda-envs/your-env-name ~/.conda/envs/your-env-name
> ```
>
> `$SCRATCH` is purged on a 12-week rolling window — for a more permanent location, use `/global/cfs/cdirs/<project>/`. See [NERSC's Python guide](https://docs.nersc.gov/development/languages/python/nersc-python/) for the full storage strategy.

## 2. Snakemake state must live on `$SCRATCH`

This is the one Perlmutter gotcha that breaks lightcone-cli silently:

`$HOME` and `/global/cfs/` are mounted on compute nodes via DVS, which ignores `flock()`. Snakemake (and any sane locking system) uses `flock`, so its `.snakemake/` directory and Dask spill files must go on Lustre (`$SCRATCH`), which honors `flock`. Otherwise you get intermittent silent rule-rerun loops or hangs.

`lc` redirects state automatically when it detects Perlmutter, so this usually just works. To pin it explicitly per project, either pass `--scratch` at init time:

```bash
lc init your-analysis --scratch '$SCRATCH'   # expanded at run time, kept verbatim in config
```

…or after the fact, edit `<project>/.lightcone/lightcone.yaml`:

```yaml
scratch_root: $SCRATCH
```

## 3. Allocations

Follow [Running on a Cluster](cluster.md) for the general pattern. The Perlmutter-specific bit is the allocation invocation — Perlmutter requires `-A <project>` and a QoS:

```bash
salloc -A <your_project> -q interactive -C gpu --nodes=1 -t 00:30:00
# allocation drops you onto a compute node; from there:
cd /path/to/your-analysis
claude    # or: lc run, if running directly without the agent
```

The `interactive` QoS is appropriate for development. For longer or larger sessions, see [NERSC's queue policy](https://docs.nersc.gov/jobs/policy/) for the full table.

## 4. Container runtime

Compute nodes ship `podman-hpc`. The `lc build` step from [cluster.md → Pre-flight](cluster.md#pre-flight-pick-the-right-container-runtime) just works — no NERSC-specific config needed beyond what that page describes.

## Further reading

- [NERSC interactive jobs](https://docs.nersc.gov/jobs/interactive/) — `salloc` patterns and reservation queues
- [Perlmutter system overview](https://docs.nersc.gov/systems/perlmutter/) — node types and partitions
- [Best practices for running jobs](https://docs.nersc.gov/jobs/best-practices/) — when to pick which QoS, GPU vs CPU sizing
