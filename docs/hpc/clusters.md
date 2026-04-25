# Clusters

A *cluster* is a long-lived SLURM allocation that hosts a persistent Dask
scheduler and pool of workers. After `lc cluster start`, every `lc run`
connects to the same cluster via `dagster-dask` with `cluster: existing`
— **zero queue wait** for the second-through-Nth invocation.

This page describes the YAML schema and how lightcone-cli renders it
into an sbatch script. The
[`lc cluster`](../cli/cluster.md) reference documents the CLI surface.

## File layout

```
~/.lightcone/
├── clusters/
│   ├── <name>.yaml           # static config
│   └── <name>.state.json     # live state — present iff started
├── cache/
│   └── <site>.cluster.yaml   # site-keyed cluster info for QoS preflight
└── envs/
    └── <site>/               # auto-provisioned uv venv for workers
```

## Schema (single-pool, the common case)

```yaml
site: perlmutter        # one of the sites in lightcone.engine.site_registry
account: m1234          # SLURM account/allocation
qos: regular
walltime: 24h           # 24h | 30m | 01:30:00

workers:
  - nodes: 4
    threads_per_node: 64
    memory: 200GB
```

`scratch_root`, `container_runtime`, and `worker_init` come from
`site_registry.SITE_DEFAULTS[site].cluster` and only appear in the YAML
when the user overrides them.

## Schema (multi-pool, mixed CPU + GPU)

When a single cluster needs to host both CPU and GPU workers, declare
multiple pools:

```yaml
site: perlmutter
account: m1234
qos: regular
walltime: 24h

workers:
  - nodes: 3                  # CPU pool
    threads_per_node: 64
    memory: 200GB
  - nodes: 1                  # GPU pool
    threads_per_node: 4
    memory: 200GB
    resources: { GPU: 4 }
    constraint: gpu
```

ASTRA recipes that request `resources.gpus > 0` are tagged with
`dagster-dask/resource_requirements: {"GPU": N}` automatically by
[`build_asset_definitions`](../api/assets.md). Dask routes those steps
to a worker advertising the matching `--resources GPU=N` (the GPU pool).
Recipes without GPU requests have no tag and run on any worker.

## Sbatch rendering

`lc cluster start` writes the rendered sbatch to
`<project>/results/.slurm/lc-cluster-<name>.sbatch`. A simplified single-pool
example:

```bash
#!/bin/bash
#SBATCH --job-name=lc-cluster-perlmutter
#SBATCH --nodes=4
#SBATCH --time=24:00:00
#SBATCH --qos=regular
#SBATCH --account=m1234
#SBATCH --output=results/.slurm/lc-cluster-perlmutter-%j.out

set -euo pipefail
module load python
source $HOME/.lightcone/envs/perlmutter/bin/activate

SCHED_FILE="$PSCRATCH/lightcone/clusters/perlmutter.json"
mkdir -p "$(dirname "$SCHED_FILE")"

dask scheduler --scheduler-file "$SCHED_FILE" --port 8786 \
               --dashboard-address :8787 &
SCHED_PID=$!

srun --nodes=4 --ntasks=4 --ntasks-per-node=1 \
     dask worker --scheduler-file "$SCHED_FILE" \
                 --nworkers 64 --memory-limit "200GB" \
                 --local-directory "$PSCRATCH/lightcone/clusters/scratch" &

wait "$SCHED_PID"
```

A multi-pool cluster emits one `srun` per pool, each with its own
`--constraint` and `--resources` tag.

## Worker env auto-bootstrap

The first `lc cluster start` for a site provisions
`~/.lightcone/envs/<site>/` as a `uv venv` and installs
`lightcone-cli`, `dagster-dask`, and `distributed` into it. The default
`worker_init` activates this venv on each compute node. Sites with
non-standard Python (e.g. `module load python` first) override
`worker_init` in the cluster YAML.

## QoS preflight

Before submitting, `lc cluster start` consults the cluster cache at
`~/.lightcone/cache/<site>.cluster.yaml` and validates the cluster's
`(qos, nodes, walltime)` against the QoS limits. Two strategies are
supported:

- `fit` (default) — clamp walltime / nodes to fit the requested QoS.
- `switch` — pick a different QoS from the site's choices that fits the
  requested resources, holding the constraint fixed.

`lc cluster refresh-cache <site>` re-runs `sacctmgr` / `scontrol` discovery
and rewrites the cache. The cache is also auto-refreshed if older than
30 days at start time.

## State and lifetime

`lc cluster start` writes:

```json
{
  "name": "perlmutter",
  "job_id": "12345678",
  "site": "perlmutter",
  "submitted_at": "2026-04-25T10:00:00Z",
  "walltime_seconds": 86400,
  "scheduler_file": "/pscratch/sd/u/u/lightcone/clusters/perlmutter.json"
}
```

The Dask scheduler address (`tcp://host:port`) is **not** cached — it's
read live from the scheduler-file each time `lc run` resolves the cluster,
so a cluster restart picks up a new address without intervention.

`lc cluster stop` cancels the SLURM job, removes the scheduler-file, and
deletes the state file. When SLURM hits walltime, the cluster dies cleanly
and the next `lc run` reports `slurm_state == "DEAD"` with a clear
message instructing the user to restart.
