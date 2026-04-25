# Clusters

A *cluster* is a persistent rendezvous to a Dask scheduler. The substrate
that provides the scheduler (today: SLURM via `sbatch`; planned: k8s via
`dask-kubernetes`) is a private detail of how the cluster comes up and
goes down — `lc run` always dispatches via `dagster-dask` regardless of
which substrate hosts the scheduler.

This page describes the YAML schema and how lightcone-cli renders it.
The [`lc cluster`](../cli/cluster.md) reference documents the CLI surface.

## File layout

```
~/.lightcone/
├── clusters/
│   ├── <name>.yaml             # static config (declares type:)
│   └── <name>.state.json       # live substrate state — present iff started
├── cache/
│   └── <site>.slurm.yaml       # SLURM-specific sacctmgr/scontrol cache
└── envs/
    └── <site>/                 # auto-provisioned uv venv for SLURM workers
```

Future substrates add their own files here without overlapping (k8s would
read kubeconfig from the standard location and write its state-file
analogue alongside).

## Schema (single-pool SLURM, the common case)

```yaml
type: slurm                # required discriminator; values: slurm
site: perlmutter           # one of the sites in lightcone.engine.site_registry
account: m1234             # SLURM account/allocation
qos: regular
walltime: 24h              # 24h | 30m | 01:30:00

workers:
  - nodes: 4
    threads_per_node: 64
    memory: 200GB
```

`scratch_root`, `container_runtime`, and `worker_init` come from
`site_registry.SITE_DEFAULTS[site].slurm` and only appear in the YAML
when the user overrides them. Each substrate has its own block in the
site registry — when k8s lands, sites can declare a `k8s:` block beside
their `slurm:` block, and the matching block is consulted based on the
cluster YAML's `type:`.

## Schema (multi-pool, mixed CPU + GPU)

When a single cluster needs to host both CPU and GPU workers, declare
multiple pools:

```yaml
type: slurm
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

This routing mechanism is substrate-agnostic — when k8s arrives, the
GPU pool will be a labeled node group with `nvidia.com/gpu: 4` resource
limits, and the same `dagster-dask/resource_requirements` op tag will
route GPU recipes there.

## Sbatch rendering (SLURM)

`lc cluster start` writes the rendered sbatch to
`<project>/results/.slurm/lc-cluster-<name>.sbatch`. A simplified
single-pool example:

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

## Worker env auto-bootstrap (SLURM)

The first `lc cluster start` for a site provisions
`~/.lightcone/envs/<site>/` as a `uv venv` and installs
`lightcone-cli`, `dagster-dask`, and `distributed` into it. The default
`worker_init` activates this venv on each compute node. Sites with
non-standard Python (e.g. `module load python` first) override
`worker_init` in the cluster YAML.

(For a future k8s cluster type, the equivalent is a worker-image build
declared by the cluster YAML; same idea, different mechanism.)

## QoS preflight (SLURM)

Before submitting, `lc cluster start` consults the cluster cache at
`~/.lightcone/cache/<site>.slurm.yaml` and validates the cluster's
`(qos, nodes, walltime)` against the QoS limits. Two strategies are
supported:

- `fit` (default) — clamp walltime / nodes to fit the requested QoS.
- `switch` — pick a different QoS from the site's choices that fits the
  requested resources, holding the constraint fixed.

`lc cluster refresh-cache <site>` re-runs `sacctmgr` / `scontrol`
discovery and rewrites the cache. The cache is also auto-refreshed if
older than 30 days at start time.

## State and lifetime

`lc cluster start` writes a substrate-tagged state file:

```json
{
  "name": "perlmutter",
  "type": "slurm",
  "job_id": "12345678",
  "site": "perlmutter",
  "submitted_at": "2026-04-25T10:00:00Z",
  "walltime_seconds": 86400,
  "scheduler_file": "/pscratch/sd/u/u/lightcone/clusters/perlmutter.json"
}
```

The Dask scheduler address (`tcp://host:port`) is **not** cached — it's
read live from the scheduler-file each time `lc run` resolves the
cluster, so a cluster restart picks up a new address without intervention.

`lc cluster stop` tears down the substrate (scancel + cleanup for SLURM)
and deletes the state file. When SLURM hits walltime, the cluster dies
cleanly and the next `lc run` reports `slurm_state == "DEAD"` with a
clear message instructing the user to restart.

## Local "cluster"

`lc run` without a configured cluster spins up a fresh
`distributed.LocalCluster` for the run via `dagster-dask`'s built-in
`cluster: { local: {} }` mode. There's no config file, no state file —
the local case is handled inline. Cost: ~1-2s startup tax per run.
