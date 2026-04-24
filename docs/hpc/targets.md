# Target Configuration

See also [CLI: lc target](../cli/target.md) and [API: targets](../api/targets.md).

## Full target YAML reference

```yaml
# Required
backend: slurm           # "slurm" | "docker" | "local"

# Connection (SLURM only)
connection:
  hostname: perlmutter.nersc.gov
  username: jdoe

# SLURM scheduler options
account: m4031_g         # SLURM allocation account
container_runtime: podman-hpc
constraint: gpu          # --constraint value
qos: regular             # --qos value
site: perlmutter         # site key for site-specific logic

# Resource limits (caps Claude's resource requests)
max_nodes: 4
max_walltime_minutes: 360
max_concurrent_jobs: 8

# Optional per-run defaults
nodes: 1
time_limit: "30m"
ntasks_per_node: 1

# Injected by lc run for SLURM flags
extra_slurm_args:
  - --partition=gpu-a100
  - --gres=gpu:1
```

## How target config flows into Parsl

`build_definitions()` in `assets.py` passes the target YAML to the runner, which maps each entry under `pilots:` to a `SlurmProvider` + `WorkQueueExecutor` in the Parsl config. Recipes are dispatched to the appropriate pilot based on their `resources` (see [Parsl pilot model](parsl-pilot.md)).

The old `scheduler:` key and per-recipe `sbatch` script generation are no longer used. Pilot-level SLURM parameters (`account`, `qos`, `constraint`, etc.) now live under each named pilot block.

## Resource limit enforcement

Claude Code reads the target YAML to know what it's allowed to request per job. These are enforced by convention (in skill prompts) rather than technically — they cap the numbers Claude writes into recipe `resources:` blocks.
