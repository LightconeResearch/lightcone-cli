# lightcone-cli Reference

Reference for lightcone-cli execution: CLI commands, development workflow, status interpretation, and failure diagnosis. For `astra.yaml` spec syntax, see `astra-reference.md`.

## CLI Reference

```bash
lc init [DIR]                            # Scaffold a new ASTRA project
lc run [OUTPUT] [--universe NAME]        # Materialize outputs
lc build [--force] [--runtime docker]    # Build container images from specs
lc status [--universe NAME] [--json]     # Materialization status (text or JSON)
lc verify [--universe NAME]              # Recompute hashes and walk the provenance chain
```

The first `lc` invocation auto-creates `~/.lightcone/config.yaml` with defaults; edit it directly to pin a container runtime or set the extraction model.

**Always run via `lc`.** Recipes must execute through `lc run` so that container builds, option resolution, resource limits, and result paths are applied. Treat the underlying execution engine as a black box — never invoke schedulers or container runtimes directly, that will bypass reproducibility guarantees.

## Creating Sub-Analyses

Sub-analyses are scaffolded by hand, since each one is just another `astra.yaml` nested in a directory. To add one:

1. Create `analyses/<name>/` with its own `astra.yaml` (and optionally `scripts/`, `universes/baseline.yaml`, `results/`).
2. Add a `path:` entry to the parent `astra.yaml` under `analyses:` (e.g. `analyses: { my_sub: { path: ./analyses/my_sub } }`).
3. Add a `<name>: { universe: baseline }` entry to each existing parent universe file.

Populate the sub-analysis's `astra.yaml` with inputs, outputs, and decisions. Use `from:` references to wire inputs and decisions to the parent or siblings — see `astra-reference.md` under "Composition Mechanics."

## Development Workflow

Three overlapping phases:

1. **Write & Debug** — Run scripts directly (`python scripts/compute.py`) to iterate. Write them recipe-ready from the start: parameterize decisions, write to convention paths, one script per output.
2. **Integrate** — Add `recipe:` blocks to outputs in `astra.yaml`. Track with `lc status` (`alias` / `missing` / `stale` / `ok`). Set `container:` at analysis level or per-recipe — pass an image name (e.g., `python:3.12-slim`) or a path to a Containerfile (e.g., `Containerfile`).
3. **Materialize** — `lc run` executes recipes inside their declared containers and writes a content-addressed manifest next to each output. Done when `lc status` shows all `ok`.

**An output is not done until `lc run` produces it.** Running scripts directly is for debugging only — final results must always come from `lc run` so they are reproducible.

### Spec-Code Invariant

**`astra.yaml` must always reflect the code and vice versa.** When you change one, update the other immediately:
- Add a decision to code? Add it to `astra.yaml` and all universe files.
- Add an output or change a script? Update the `recipe:` block in `astra.yaml`.
- Remove or rename something? Update both sides and run `astra validate astra.yaml`.

## Status Interpretation

`lc status` shows each declared output's materialization state per universe. Pass `--json` for machine-readable output.

- `ok` — Recipe exists, results on disk, manifest matches the current spec. Done.
- `stale` — Recipe or decisions changed since the last run. Re-run `lc run`.
- `missing` — Recipe exists but no manifest (never run, or output deleted). Run `lc run`.
- `alias` — Output has no recipe of its own; produced as a side effect of an upstream output (or a `from:` reference into a sub-analysis). Not independently materializable.

## Failure Diagnosis

- **Script arg not recognized** — The recipe's `command` template controls how decisions reach the script. Make sure each `{decisions.<id>}` is paired with a flag the script's argparse defines (e.g. `--<id> {decisions.<id>}` ↔ `parser.add_argument('--<id>')`).
- **Recipe input not found** — Materialize upstream outputs first.
- **Undeclared placeholder error** — A `{decisions.<id>}` or `{inputs.<id>}` in the recipe references something not listed in `Output.decisions` / `Output.inputs`. Add it to the Output's declaration, or remove the placeholder.

After failure: fix, then `lc run <output_id> --universe <name>`.
