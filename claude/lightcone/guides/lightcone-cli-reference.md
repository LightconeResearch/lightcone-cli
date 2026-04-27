# lightcone-cli Reference

Reference for lightcone-cli execution: CLI commands, development workflow, status interpretation, and failure diagnosis. For `astra.yaml` spec syntax, see `astra-reference.md`.

## CLI Reference

```bash
lc init [DIR]                            # Scaffold a new ASTRA project
lc init NAME --sub-analysis              # Scaffold sub-analysis and wire into parent
lc run [OUTPUT] [--universe NAME]        # Materialize outputs (always via Dask)
lc build [--force] [--runtime docker]    # Build container images from specs
lc status [--universe NAME]              # Materialization + container status
lc verify [--universe NAME]              # Recompute hashes and walk the provenance chain
lc setup                                 # Write a minimal ~/.lightcone/config.yaml
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
3. **Materialize** — `lc run` dispatches recipes through a Dask cluster (`LocalCluster` on a workstation, srun-launched workers inside a SLURM allocation). Done when `lc status` shows all `ok`.

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
