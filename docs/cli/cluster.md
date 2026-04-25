# lc cluster

Manage long-lived SLURM Dask clusters — single allocations that host a
persistent Dask scheduler + workers. After `lc cluster start`, every
`lc run` connects to the same cluster with **zero queue wait**.

## Concepts

A cluster is described by a YAML file at `~/.lightcone/clusters/<name>.yaml`
(see [clusters.md](../hpc/clusters.md) for the schema). `lc cluster start`
submits the sbatch, writes `<name>.state.json` next to it, and the file
is removed by `lc cluster stop`.

The project picks a cluster via `.lightcone/lightcone.yaml: cluster: <name>`,
or `lc run` falls back to the single configured cluster.

## Subcommands

### `lc cluster add [NAME] [--site SITE]`

Stamp a cluster YAML from the site registry's defaults, prompt for the
SLURM `account`, and open the file in `$EDITOR` for review. Site is
auto-detected from the hostname when possible.

### `lc cluster list`

Table of configured clusters with their live SLURM state and Dask
scheduler addresses.

### `lc cluster start [NAME] [--qos Q] [--walltime W] [--strategy fit|switch] [--wait/--detach]`

Render the sbatch, run QoS preflight against the cluster cache,
auto-provision the worker venv on first use, submit via `sbatch`, and
(by default) block until the Dask scheduler is reachable.

| Flag | Meaning |
|---|---|
| `--qos Q` | Override the cluster's `qos` for this submission. |
| `--walltime W` | Override walltime (e.g. `30m`, `24h`). |
| `--strategy fit` | If preflight fails, clamp walltime/nodes to fit the QoS. |
| `--strategy switch` | If preflight fails, pick a different QoS that fits. |
| `--wait` (default) | Block until scheduler is up. |
| `--detach` | Return immediately after submission. |

### `lc cluster status [NAME]`

Pretty-prints the cluster state — SLURM job id, submission time, walltime
budget, scheduler address.

### `lc cluster logs [NAME] [-f] [-n N]`

Tail (or follow) the SLURM output log at
`results/.slurm/lc-cluster-<name>-<jobid>.out`.

### `lc cluster stop [NAME]`

`scancel` the SLURM job, remove the scheduler-file, delete the state file.

### `lc cluster edit NAME`

Open the cluster YAML in `$EDITOR`.

### `lc cluster refresh-cache [SITE]`

Re-run `sacctmgr` / `scontrol` discovery and rewrite
`~/.lightcone/cache/<site>.cluster.yaml`. Used by QoS preflight at
`lc cluster start`.

## Typical session

```bash
lc cluster add perlmutter        # one-time wizard
lc cluster start perlmutter      # one queue wait
lc run                          # instant
lc run                          # instant
lc run                          # ...
lc cluster stop perlmutter       # done
```
