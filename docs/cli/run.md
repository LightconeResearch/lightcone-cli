# prism run

Materialise ASTRA outputs via Dagster.

## Synopsis

```
prism run [OPTIONS] [OUTPUTS]... [SLURM_FLAGS]...
```

## Description

`prism run` loads `astra.yaml`, builds Dagster asset definitions, and calls `dagster.materialize()`. Container images are built automatically before execution (unless `--no-build` is given).

Arguments that start with `-` are treated as SLURM scheduling directives and passed through to the `sbatch` script. Everything else is treated as an output name to materialise.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `OUTPUTS` | all | Output IDs to materialise. Supports dot-notation for sub-analyses: `hod_fitting.galaxy_mesh`. |
| `--universe`, `-u` | `baseline` | Universe to materialise |
| `--target`, `-t` | project default | Execution target |
| `--no-build` | false | Skip automatic container builds |

## SLURM passthrough

Any unknown flags are forwarded as SLURM scheduling directives:

```bash
prism run --qos shared --constraint gpu
prism run --partition gpu-a100
prism run --gres gpu:1 --time 30:00
```

These are collected in `target_config["extra_slurm_args"]` and injected into the `sbatch` script verbatim.

## Examples

```bash
prism run                              # all outputs, baseline universe
prism run accuracy                     # specific output
prism run --universe experiment1       # different universe
prism run accuracy -u baseline         # output + universe
prism run --target perlmutter-gpu      # on SLURM
prism run --no-build                   # skip container builds
prism run hod_fitting.galaxy_mesh      # sub-analysis output
prism run --qos debug --constraint gpu # with SLURM scheduling flags
```

## Execution order

Dagster resolves the dependency graph from `recipe.inputs` entries and materialises outputs in topological order. If an output's recipe fails, downstream outputs are not attempted.

## Output paths

Results are always written to:

```
results/{universe_id}/{output_id}/
```

The `ASTRA_OUTPUT_DIR` environment variable is set to the correct path before each recipe runs.

## Dagster persistence

Materialisation events are stored in `results/.dagster/` (SQLite). This is what `prism status` queries. If `dagster.yaml` is missing, `prism run` creates it automatically.
