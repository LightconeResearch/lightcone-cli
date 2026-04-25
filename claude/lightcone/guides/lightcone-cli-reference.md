# lightcone-cli Reference

Reference for lightcone-cli execution: CLI commands, development workflow, status interpretation, and failure diagnosis. For `astra.yaml` spec syntax, see `astra-reference.md`.

## CLI Reference

```bash
lc init [DIR]                            # Scaffold a new ASTRA project
lc init NAME --sub-analysis              # Scaffold sub-analysis and wire into parent
lc run [OUTPUT] [-u UNIVERSE]            # Execute recipes via Dagster (auto-builds)
lc run --cluster NAME                      # Dispatch to a configured cluster's Dask cluster
lc run --local                           # Force local execution (override project cluster)
lc run --no-build                        # Skip automatic container builds
lc build [--force] [--runtime docker]    # Build container images from specs
lc status [--universe NAME]              # Materialization + container status
lc dev [--port 3000]                     # Dagster webserver UI
lc cluster list                            # List configured clusters and live state
lc cluster add [NAME]                      # Create a cluster YAML from site defaults
lc cluster start [NAME]                    # Submit the SLURM allocation
lc cluster stop [NAME]                     # Cancel the allocation
lc cluster status [NAME]                   # Show one cluster's state
lc cluster logs [NAME] [-f]                # Tail SLURM stdout
```

**Always run via `lc`.** Recipes must execute through `lc run` so that container builds, cluster dispatch, and result paths are applied. Never invoke schedulers or container runtimes directly — it will bypass reproducibility guarantees.

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
3. **Materialize** — `lc run` executes via Dagster, locally or via a cluster. Done when `lc status` shows all `ok`.

**An output is not done until `lc run` produces it.** Running scripts directly is for debugging only — final results must always come from `lc run` so they are reproducible.

### Spec-Code Invariant

**`astra.yaml` must always reflect the code and vice versa.** When you change one, update the other immediately:
- Add a decision to code? Add it to `astra.yaml` and all universe files.
- Add an output or change a script? Update the `recipe:` block in `astra.yaml`.
- Remove or rename something? Update both sides and run `astra validate astra.yaml`.

## Clusters — running on SLURM with zero queue wait

A *cluster* is a long-lived SLURM allocation that hosts a Dask scheduler.
After `lc cluster start`, every `lc run` dispatches to the same cluster
without re-queueing.

```bash
lc cluster add perlmutter         # one-time wizard
lc cluster start perlmutter       # one queue wait, ~minutes
lc run                          # ~1s round-trip
lc run                          # again — instant
lc cluster stop perlmutter        # done
```

The project picks a cluster via `.lightcone/lightcone.yaml: cluster: NAME`,
or `lc run` falls back to the single configured cluster. Without a cluster,
recipes run locally with the auto-detected container runtime.

Scheduler-side knobs (`--qos`, `--walltime`, `--nodes`, GPU vs CPU
constraint) live on `lc cluster start`, not on `lc run` — clusters are
long-lived, so those choices are made once at submission.

## Status Interpretation

`lc status` shows outputs vs universes. **Progression:** `no recipe` → `pending` → `ok`

- `ok` — Recipe exists, results on disk. Done.
- `pending` — Recipe exists, not materialized. Run `lc run`.
- `no recipe` — No `recipe:` block yet. Still in Write & Debug phase.

Container status: `prebuilt: image`, `build: Containerfile (built)`, or `(not built)` (needs `lc build`).

## Failure Diagnosis

- **"No active cluster for 'NAME'"** — `lc cluster start NAME`, or `lc run --local` to bypass.
- **Cluster dies (walltime exceeded)** — `lc cluster start NAME` to renew. Walltime is a property of the cluster YAML.
- **Script arg not recognized** — Use underscores in argparse to match decision IDs.
- **Recipe input not found** — Materialize upstream outputs first.

After failure: fix, then `lc run <output_id> --universe <name>`.
