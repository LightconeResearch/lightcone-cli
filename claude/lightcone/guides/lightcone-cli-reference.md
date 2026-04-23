# lightcone-cli Reference

Reference for lightcone-cli execution: CLI commands, development workflow, status interpretation, and failure diagnosis. For `astra.yaml` spec syntax, see `astra-reference.md`.

## CLI Reference

```bash
lc init [DIR]                            # Scaffold a new ASTRA project
lc init NAME --sub-analysis              # Scaffold sub-analysis and wire into parent
lc run [OUTPUT] [--universe NAME]        # Execute recipes via Dagster (auto-builds)
lc run [--qos V] [--constraint V] [--time-limit V] [--account V] [--partition V]
                                         # Override target run options per invocation
lc run --no-build                        # Skip automatic container builds
lc build [--force] [--runtime docker]    # Build container images from specs
lc status [--universe NAME]              # Materialization + container status
lc dev [--port 3000]                     # Dagster webserver UI
lc target                                # Show current target + available run options
lc target --show NAME                    # Show a target's run options
lc target --set NAME                     # Switch project target
lc target --list                         # List available targets
lc setup                                 # Interactive setup wizard
```

**Always run via `lc`.** Recipes must execute through `lc run` so that container builds, option resolution, resource limits, and result paths are applied. Never invoke schedulers or container runtimes directly — it will bypass reproducibility guarantees.

## Creating Sub-Analyses

`lc init NAME --sub-analysis` scaffolds a sub-analysis and wires it into the parent project. It:

1. Creates `analyses/<name>/` with its own `astra.yaml`, `CLAUDE.md`, `scripts/`, `universes/baseline.yaml`, and `results/`
2. Adds a `path:` entry to the parent `astra.yaml` under `analyses:`
3. Adds a `universe: baseline` entry to all existing parent universe files

After scaffolding, populate the sub-analysis's `astra.yaml` with inputs, outputs, and decisions. Use `from:` references to wire inputs and decisions to the parent or siblings — see `astra-reference.md` under "Composition Mechanics."

## Development Workflow

Three overlapping phases:

1. **Write & Debug** — Run scripts directly (`python scripts/compute.py`) to iterate. Write them recipe-ready from the start: parameterize decisions, write to convention paths, one script per output.
2. **Integrate** — Add `recipe:` blocks to outputs in `astra.yaml`. Track with `lc status` (`no recipe` / `pending` / `ok`). Set `container:` at analysis level or per-recipe — pass an image name (e.g., `python:3.12-slim`) or a path to a Containerfile (e.g., `Containerfile`).
3. **Materialize** — `lc run` executes via Dagster in the target's environment. Done when `lc status` shows all `ok`.

**An output is not done until `lc run` produces it.** Running scripts directly is for debugging only — final results must always come from `lc run` so they are reproducible.

### Spec-Code Invariant

**`astra.yaml` must always reflect the code and vice versa.** When you change one, update the other immediately:
- Add a decision to code? Add it to `astra.yaml` and all universe files.
- Add an output or change a script? Update the `recipe:` block in `astra.yaml`.
- Remove or rename something? Update both sides and run `astra validate astra.yaml`.

## Status Interpretation

`lc status` shows outputs vs universes. **Progression:** `no recipe` → `pending` → `ok`

- `ok` — Recipe exists, results on disk. Done.
- `pending` — Recipe exists, not materialized. Run `lc run`.
- `no recipe` — No `recipe:` block yet. Still in Write & Debug phase.

Container status: `prebuilt: image`, `build: Containerfile (built)`, or `(not built)` (needs `lc build`).

## Failure Diagnosis

- **Script arg not recognized** — Use underscores in argparse to match decision IDs
- **Recipe input not found** — Materialize upstream outputs first

After failure: fix, then `lc run <output_id> --universe <name>`.

## Choosing run options

Every target exposes a small set of orthogonal **options** (commonly `qos`, `constraint`, `time_limit`, `account`, `partition`). To see what this target offers:

```bash
lc target
```

Each option has a default and a set of valid choices with short guidance. Override any of them for a single run:

```bash
lc run --qos debug --time-limit 30m        # quick iteration
lc run --qos regular --time-limit 4h       # production run
lc run --constraint cpu                    # CPU-only hardware
```

If your recipe's `resources` exceed the limits implied by the selected options, `lc run` will either trim the request to fit (`strategy: fit`, the default) or switch to a different `qos` choice (`strategy: switch`). Pass `--strategy switch` per run to prefer the latter.

Recipe `resources` (gpus, memory, cpus, nodes, time_limit) are portable across targets — they inform how `lc run` dispatches work in the target's environment.
