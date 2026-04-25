# lc cluster

Manage long-lived Dask clusters — persistent rendezvous to a Dask
scheduler that every `lc run` dispatches to. After `lc cluster start`,
the cluster stays up for its full walltime; subsequent `lc run`s
connect with **zero queue wait**.

The substrate that provides the cluster is selected by the `type:`
field in the cluster YAML. **SLURM** is supported today; **k8s** is the
planned next addition (single-file extension, no architectural change).

## Concepts

A cluster is described by a YAML file at
`~/.lightcone/clusters/<name>.yaml` (see [clusters.md](../hpc/clusters.md)
for the schema). `lc cluster start` provisions the substrate (sbatch on
SLURM), writes `<name>.state.json` next to the config, and the state
file is removed by `lc cluster stop`.

The project picks a cluster via `.lightcone/lightcone.yaml: cluster: <name>`
or, when only one is configured, falls back to that one. `lc run`
without a configured cluster spins up an ephemeral
`distributed.LocalCluster` for the duration of the run.

## Subcommands

### `lc cluster add [NAME] [--site SITE]`

Stamp a cluster YAML from the site registry's substrate-specific defaults
(`SITE_DEFAULTS[site].slurm` today), prompt for the SLURM `account`, and
open the file in `$EDITOR` for review. The site is auto-detected from the
hostname when possible. The wizard writes `type: slurm` automatically.

### `lc cluster list`

Table of configured clusters with their type, live substrate state, and
Dask scheduler address.

### `lc cluster start [NAME] [--qos Q] [--walltime W] [--strategy fit|switch] [--wait/--detach]`

Provision the cluster's substrate. For SLURM: render the sbatch, run QoS
preflight against the cluster cache, auto-provision the worker venv on
first use, submit via `sbatch`, and (by default) block until the Dask
scheduler is reachable.

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

Tail (or follow) the substrate's stdout log. For SLURM, that's
`results/.slurm/lc-cluster-<name>-<jobid>.out`.

### `lc cluster stop [NAME]`

Tear down the substrate. For SLURM: `scancel` the job, remove the
scheduler-file, delete the state file.

### `lc cluster edit NAME`

Open the cluster YAML in `$EDITOR`.

### `lc cluster refresh-cache [SITE]`

Re-run substrate discovery (`sacctmgr` / `scontrol` for SLURM) and rewrite
`~/.lightcone/cache/<site>.slurm.yaml`. Used by QoS preflight at
`lc cluster start`. Future substrates that need a cache layer add their
own filename (e.g. `<site>.k8s.yaml`).

## Typical session

```bash
lc cluster add perlmutter         # one-time wizard
lc cluster start perlmutter       # one queue wait, ~minutes
lc run                            # ~1s round-trip, every time
lc run                            # again — instant
lc cluster stop perlmutter        # done
```
