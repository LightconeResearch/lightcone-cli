# lc run

Materialise ASTRA outputs via Dagster, dispatching every step to a Dask
cluster. The cluster is either ephemeral (local) or one you started with
[`lc cluster start`](cluster.md).

## Synopsis

```
lc run [OPTIONS] [OUTPUTS]...
```

## Description

`lc run` loads `astra.yaml`, builds the Dagster asset definitions, and
calls `dagster.execute_job(...)` with `dagster-dask` as the executor.
The mental model:

> **Every Dagster step runs on a Dask cluster. The substrate just
> provides the cluster.**

Two cluster modes are wired:

- **Local** — `lc run` (or `lc run --local`) when no cluster is
  resolvable. `dagster-dask`'s built-in `local` mode spins up an
  ephemeral `distributed.LocalCluster` for the run and tears it down on
  completion. Cost: ~1-2s startup tax. Container images are auto-detected
  (Docker / Podman) on the local machine.
- **Substrate-backed** — when a cluster is resolved
  ([`lc cluster start`](cluster.md) has produced a live Dask scheduler
  on SLURM today, k8s tomorrow). Each asset is shipped to the running
  cluster via `cluster: existing`. **Zero queue wait** for the
  second-through-Nth invocation.

Cluster resolution: `--cluster NAME` flag → project's
`.lightcone/lightcone.yaml: cluster:` field → the single configured
cluster in `~/.lightcone/clusters/` → fall back to local.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `OUTPUTS` | all recipe outputs | Output IDs to materialise. Sub-analysis dot-notation supported: `hod_fitting.galaxy_mesh`. |
| `--universe`, `-u` | `baseline` | Universe to materialise. |
| `--cluster` | resolved | Cluster to dispatch on. Overrides project default. |
| `--local` | false | Force local execution even when a cluster is configured. |
| `--no-build` | false | Skip automatic container image builds. |

Scheduler-side knobs (`--qos`, `--walltime`, `--nodes`, …) live on
`lc cluster start`, not on `lc run` — clusters are long-lived, so those
choices are made once at submission time.

## Examples

```bash
lc run                              # all outputs, baseline universe (local)
lc run accuracy                     # specific output
lc run --universe experiment1       # different universe
lc run accuracy -u baseline         # output + universe
lc run --cluster perlmutter         # explicit cluster
lc run --local                      # force local even with cluster configured
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

## Under the hood

`lc run` always calls:

```python
dg.execute_job(
    reconstructable(build_cluster_job),     # imports lightcone.engine.dask_entrypoint
    instance=instance,
    run_config={"execution": {"config": {"cluster": cluster_mode}}},
    op_selection=...,
)
```

`cluster_mode` is the only thing that varies:

```python
# Local — dagster-dask spins up a LocalCluster
cluster_mode = {"local": {}}

# Substrate-backed cluster (SLURM today, k8s tomorrow)
cluster_mode = {"existing": {"address": "tcp://nidXXXX:8786"}}
```

For substrate-backed runs, three env vars carry the worker-side state
(workers re-import the entrypoint and re-read these):

- `LIGHTCONE_PROJECT_PATH` — the orchestrator's working directory; the
  shared-filesystem assumption means workers see the same path.
- `LIGHTCONE_CLUSTER` — the cluster config name (empty string for local).
- `LIGHTCONE_UNIVERSE` — the universe id (defaults to `baseline`).

Each Dagster step becomes a Dask task; the asset body shells out to the
configured container runtime (e.g. `podman-hpc run …` on Perlmutter,
`docker run …` on a local laptop).

The shared-filesystem assumption (orchestrator + workers see the same
`astra.yaml`, recipe scripts, inputs) is load-bearing for substrate-backed
clusters. For local, everything runs on one machine so it's automatic.
