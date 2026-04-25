# lc pilot

Manage long-lived SLURM Dask pilots — single allocations that host a
persistent Dask scheduler + workers. After `lc pilot start`, every
`lc run` connects to the same cluster with **zero queue wait**.

## Concepts

A pilot is described by a YAML file at `~/.lightcone/pilots/<name>.yaml`
(see [pilots.md](../hpc/pilots.md) for the schema). `lc pilot start`
submits the sbatch, writes `<name>.state.json` next to it, and the file
is removed by `lc pilot stop`.

The project picks a pilot via `.lightcone/lightcone.yaml: pilot: <name>`,
or `lc run` falls back to the single configured pilot.

## Subcommands

### `lc pilot add [NAME] [--site SITE]`

Stamp a pilot YAML from the site registry's defaults, prompt for the
SLURM `account`, and open the file in `$EDITOR` for review. Site is
auto-detected from the hostname when possible.

### `lc pilot list`

Table of configured pilots with their live SLURM state and Dask
scheduler addresses.

### `lc pilot start [NAME] [--qos Q] [--walltime W] [--strategy fit|switch] [--wait/--detach]`

Render the sbatch, run QoS preflight against the cluster cache,
auto-provision the worker venv on first use, submit via `sbatch`, and
(by default) block until the Dask scheduler is reachable.

| Flag | Meaning |
|---|---|
| `--qos Q` | Override the pilot's `qos` for this submission. |
| `--walltime W` | Override walltime (e.g. `30m`, `24h`). |
| `--strategy fit` | If preflight fails, clamp walltime/nodes to fit the QoS. |
| `--strategy switch` | If preflight fails, pick a different QoS that fits. |
| `--wait` (default) | Block until scheduler is up. |
| `--detach` | Return immediately after submission. |

### `lc pilot status [NAME]`

Pretty-prints the pilot state — SLURM job id, submission time, walltime
budget, scheduler address.

### `lc pilot logs [NAME] [-f] [-n N]`

Tail (or follow) the SLURM output log at
`results/.slurm/lc-pilot-<name>-<jobid>.out`.

### `lc pilot stop [NAME]`

`scancel` the SLURM job, remove the scheduler-file, delete the state file.

### `lc pilot edit NAME`

Open the pilot YAML in `$EDITOR`.

### `lc pilot refresh-cache [SITE]`

Re-run `sacctmgr` / `scontrol` discovery and rewrite
`~/.lightcone/cache/<site>.cluster.yaml`. Used by QoS preflight at
`lc pilot start`.

## Typical session

```bash
lc pilot add perlmutter        # one-time wizard
lc pilot start perlmutter      # one queue wait
lc run                          # instant
lc run                          # instant
lc run                          # ...
lc pilot stop perlmutter       # done
```
