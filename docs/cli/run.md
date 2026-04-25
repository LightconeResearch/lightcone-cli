# lc run

Materialise ASTRA outputs via Dagster. Runs locally by default, or
dispatches to a [pilot](../hpc/pilots.md) when one is configured.

## Synopsis

```
lc run [OPTIONS] [OUTPUTS]...
```

## Description

`lc run` loads `astra.yaml`, builds Dagster asset definitions, and
materialises the requested outputs.

It picks one of two execution paths:

- **Local** — used when no pilot is resolvable. Container images are
  auto-detected (Docker / Podman) and recipes execute in-process via
  `dagster.materialize()`.
- **Pilot** — used when [a pilot is configured](../hpc/pilots.md) and
  `lc pilot start` has produced a live Dask scheduler. Each asset is
  shipped to the running cluster via `dagster_dask.dask_executor` with
  `cluster: existing`. **Zero queue wait.**

Pilot resolution: `--pilot NAME` flag → project's
`.lightcone/lightcone.yaml: pilot:` field → the single configured pilot
in `~/.lightcone/pilots/` → fall back to local.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `OUTPUTS` | all recipe outputs | Output IDs to materialise. Sub-analysis dot-notation supported: `hod_fitting.galaxy_mesh`. |
| `--universe`, `-u` | `baseline` | Universe to materialise. |
| `--pilot` | resolved | Pilot to dispatch on. Overrides project default. |
| `--local` | false | Force local execution even when a pilot is configured. |
| `--no-build` | false | Skip automatic container image builds. |

Scheduler-side knobs (`--qos`, `--walltime`, `--nodes`, …) live on
`lc pilot start`, not on `lc run` — pilots are long-lived, so those
choices are made once at submission time.

## Examples

```bash
lc run                              # all outputs, baseline universe
lc run accuracy                     # specific output
lc run --universe experiment1       # different universe
lc run accuracy -u baseline         # output + universe
lc run --pilot perlmutter           # explicit pilot
lc run --local                      # force local even with pilot configured
lc run --no-build                   # skip container builds
lc run hod_fitting.galaxy_mesh      # sub-analysis output
```

## Execution order

Dagster resolves dependencies from `recipe.inputs` and materialises in
topological order. Failed recipes block their downstreams.

## Output paths

Results are always at `results/{universe_id}/{output_id}/`. The
`ASTRA_OUTPUT_DIR` environment variable is set before each recipe runs.

## Dagster persistence

Materialisation events go to `results/.dagster/` (SQLite). This is what
`lc status` queries. `lc run` creates `dagster.yaml` automatically if
missing.

## How a pilot run works under the hood

1. `lc run` reads the pilot state file
   (`~/.lightcone/pilots/<pilot>.state.json`), confirms the SLURM job is
   `RUNNING`, and reads the Dask scheduler-file to discover
   `tcp://host:port`.
2. It exports `LIGHTCONE_PROJECT_PATH`, `LIGHTCONE_PILOT`, and
   `LIGHTCONE_UNIVERSE` so the worker-side reconstructable callable
   ([`lightcone.engine.dask_entrypoint.build_pilot_job`](../api/dask_entrypoint.md))
   can rebuild the same `Definitions` in-process on each worker.
3. It calls `dagster.execute_job(reconstructable(...), run_config={
   "execution": { "config": { "cluster": { "existing": { "address": ...
   }}}}})`.
4. Each Dagster step becomes a Dask task; the asset body shells out to
   the configured container runtime (e.g. `podman-hpc run …`) on a
   compute node.

The shared-filesystem assumption is load-bearing: workers read the same
`astra.yaml`, recipe scripts, and inputs that the orchestrator wrote.
