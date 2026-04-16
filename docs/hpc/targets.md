# Target Configuration

See also [CLI: prism target](../cli/target.md) and [API: targets](../api/targets.md).

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

# Injected by prism run for SLURM flags
extra_slurm_args:
  - --partition=gpu-a100
  - --gres=gpu:1
```

## How target config flows into SLURM scripts

`build_definitions()` in `assets.py` transforms the flat target YAML into the shape the runner expects:

```python
runner_config = {
    "connection": target_config.get("connection", {}),
    "scheduler": {
        "site": ...,
        "account": ...,
        "qos": ...,
        "constraint": ...,
        "container_runtime": ...,
        "extra_slurm_args": [...],
    }
}
```

The runner's `_run_slurm()` method passes this to `generate_sbatch_script()`, which assembles the `#SBATCH` directives.

## Resource limit enforcement

Claude Code reads the target YAML to know what it's allowed to request per job. These are enforced by convention (in skill prompts) rather than technically — they cap the numbers Claude writes into recipe `resources:` blocks.
