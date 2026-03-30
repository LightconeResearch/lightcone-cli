# Prism Reference

Reference for Prism execution: CLI commands, development workflow, status interpretation, HPC execution, and failure diagnosis. For astra.yaml spec syntax, see `astra-reference.md`.

## CLI Reference

```bash
prism init [DIR]                            # Scaffold a new ASTRA project
prism init NAME --sub-analysis              # Scaffold sub-analysis and wire into parent
prism run [OUTPUT] [--universe NAME]        # Execute recipes via Dagster (auto-builds)
prism run --partition gpu --qos shared      # Unknown flags passed through to SLURM
prism run --no-build                        # Skip automatic container builds
prism build [--force] [--runtime docker]    # Build container images from specs
prism status [--universe NAME]              # Materialization + container status
prism dev [--port 3000]                     # Dagster webserver UI
prism target [--set NAME] [--list]          # Manage execution targets
prism setup                                 # Interactive target setup wizard
```

## Creating Sub-Analyses

`prism init NAME --sub-analysis` scaffolds a sub-analysis and wires it into the parent project. It:

1. Creates `analyses/<name>/` with its own `astra.yaml`, `CLAUDE.md`, `scripts/`, `universes/baseline.yaml`, and `results/`
2. Adds a `path:` entry to the parent `astra.yaml` under `analyses:`
3. Adds a `universe: baseline` entry to all existing parent universe files

After scaffolding, populate the sub-analysis's `astra.yaml` with inputs, outputs, and decisions. Use `from:` references to wire inputs and decisions to the parent or siblings — see `astra-reference.md` under "Composition Mechanics."

## Development Workflow

Three overlapping phases:

1. **Write & Debug** -- Run scripts directly (`python scripts/compute.py`) to iterate. Write them recipe-ready from the start: parameterize decisions, write to convention paths, one script per output.
2. **Integrate** -- Add `recipe:` blocks to outputs in `astra.yaml`. Track with `prism status` (`no recipe` / `pending` / `ok`). Container build specs (Containerfile or image string) can be set at the analysis level or per-recipe.
3. **Materialize** -- `prism run` executes via Dagster in containers (Docker or SLURM). Falls back to local execution if Docker is unavailable. Done when `prism status` shows all `ok`.

**An output is not done until `prism run` produces it.** Running scripts directly is for debugging only — final results must always come from `prism run` so they are reproducible inside containers.

### Spec-Code Invariant

**`astra.yaml` must always reflect the code and vice versa.** When you change one, update the other immediately:
- Add a decision to code? Add it to `astra.yaml` and all universe files.
- Add an output or change a script? Update the `recipe:` block in `astra.yaml`.
- Remove or rename something? Update both sides and run `astra validate astra.yaml`.

## Status Interpretation

`prism status` shows outputs vs universes. **Progression:** `no recipe` --> `pending` --> `ok`

- `ok` -- Recipe exists, results on disk. Done.
- `pending` -- Recipe exists, not materialized. Run `prism run`.
- `no recipe` -- No `recipe:` block yet. Still in Write & Debug phase.

Container status: `prebuilt: image`, `build: Containerfile (built)`, or `(not built)` (needs `prism build`).

## Failure Diagnosis

- **Script arg not recognized** -- Use underscores in argparse to match decision IDs
- **Recipe input not found** -- Materialize upstream outputs first

After failure: fix, then `prism run <output_id> --universe <name>`.

## HPC / Interactive Execution

If `SLURM_JOB_ID` is set in the environment, `prism run` automatically uses `srun` (instant) instead of `sbatch` (queued). This means the user is inside an interactive allocation and execution will be fast.

If `SLURM_JOB_ID` is not set and the target is SLURM, `prism run` submits via `sbatch`. Any unknown flags are passed through as SLURM directives:
```bash
prism run --qos shared --constraint gpu      # NERSC
prism run --partition gpu-a100               # TACC, SDSC, etc.
prism run --gres=gpu:4 --partition batch     # any cluster
```

Recipe `resources` (gpus, memory, cpus, time_limit) are portable and used for batch scheduling. They are ignored in interactive mode since the allocation already provides them.
