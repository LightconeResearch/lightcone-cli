# Prism Reference

Reference for Prism execution: CLI commands, status interpretation, and failure diagnosis. For astra.yaml spec syntax, see `astra-reference.md`.

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
