# lc compute

Manage explicitly allocated Dask clusters using resource offers.
No project is required for these commands.

```text
lc compute resources [--json]
lc compute launch [--cpus VALUE --memory VALUE] [--gpus NAME[:COUNT]|0]
    [--name NAME] [--num-nodes N] [--time DURATION] [--startup fast] [--dry-run] [--wait] [--timeout SECONDS] [--json]
lc compute status [CLUSTER] [--wait] [--timeout SECONDS] [--json]
lc compute down CLUSTER [--json]
```

With no resource flags, `lc compute launch` starts the default local CPU offer,
names the cluster `local`, and uses all detected usable logical CPUs and RAM.
The built-in offer has one node, fast startup, and no fixed lifetime: it ends
after 30 minutes without task activity. `--name` overrides the name, and `--time`
adds a hard lifetime that ends the cluster even while work is running.
CPU and RAM are cooperative scheduling budgets, not exclusive reservations.
GPUs still require explicit offers and, locally, a `CUDA_VISIBLE_DEVICES` mask.

`~/.lightcone/compute.yaml` configures resource offers; `LC_COMPUTE_CONFIG` selects
another file for all compute and execution commands. The top-level `local` block
can override the built-in CPU/RAM budget or time limits, or disable local compute.
Without an explicit local connection, the built-in local offer is appended after
configured offers. Catalogs with explicit local connections use their own offers instead;
the shortcut chooses the first eligible local offer. See
[local configuration](../user/cluster.md#customize-resource-offers).
Missing explicit paths and invalid catalogs are errors. Loading a catalog or
planning with `--dry-run` creates no files or compute.

Local launch and execution are automatically disabled on recognized NERSC login
nodes, including the first run without a catalog. Interactive compute nodes remain
eligible. This runtime guard creates no configuration file and cannot be overridden
by `local.enabled: true`; Slurm, status, and termination remain available.
See [local allocations](../user/cluster.md#local-allocations) for detection details.

| Command | Behavior |
|---|---|
| `resources` | Ordered available offers, per-node shape, node limit, default/maximum walltime, idle timeout, and startup class. Free capacity remains unknown. |
| `launch` | Resolve one resource request and submit exactly once; print only the cluster name to stdout on acceptance. |
| `launch --wait` | Submit once, then wait for all expected workers. `--timeout` sets the readiness deadline (default 300 seconds). |
| `launch --dry-run` | Show the resolved shape and native launch parameters without allocation. |
| `status` | List one `name: status` line per current allocation across every configured connection; report the connections that could not be queried. |
| `status CLUSTER` | Resolve a name or full ID, inspect native state, and probe Dask readiness separately. |
| `status CLUSTER --wait` | Wait for readiness: the allocation is active and every node's worker is connected. The default deadline is 300 seconds, and queries grow less frequent as the wait goes on (up to every 30 seconds). Exits 1 on timeout, or at once if the allocation is ending or has ended; the allocation is left unchanged. |
| `down CLUSTER` | Request native termination even if the scheduler is unavailable. An allocation that has already ended is a successful no-op when addressed by full ID; its name no longer resolves. A Slurm job that has left the queue is refused unless accounting confirms it ended. |

The local shortcut defaults to `local`. For explicit CPU/memory requests, omitting
`--name` generates `lc-` followed by 12 random hexadecimal characters.
Use `--name analysis` to choose either name yourself. Names contain 1–63 lowercase ASCII letters,
digits, or hyphens; they start with a letter and end with a letter or digit.
Launch guidance goes to stderr, so the default output can be captured directly:

```bash
CLUSTER=$(lc compute launch --wait)
lc compute down "$CLUSTER"
```

Launch checks current allocations across all configured connections. An explicit
name already in use is rejected; generated collisions are retried before the
single submission. This check is not an atomic reservation: concurrent launches
can race. Name lookup refuses ambiguous or incomplete discovery instead of
choosing a cluster. A name can be reused after its allocation ends; it is not a
durable reference to that allocation. Use the full immutable `id` from launch or
status JSON to address one allocation directly, including when unrelated
connections are unavailable. No name registry is maintained.

Supply both `--cpus` and `--memory`, or omit both for the local shortcut.
The shortcut never selects a remote offer, including when local compute is disabled.

Resource quantities are **per node**, and `--num-nodes` defaults to one. CPU and
memory requests follow SkyPilot's exact/minimum convention: `4` is exact and `4+`
means at least four. Compute memory uses binary units: `16`, `16GB`, and `16GiB`
all mean 16 GiB; `16GB+` permits a larger offer.

`--gpus A100:4` requests exactly four GPUs from an offer whose accelerator type is `A100`;
`--gpus A100` means one, `--gpus GPU:4` accepts any GPU model, and the default
`--gpus 0` selects CPU-only offers. Names match case-insensitively. GPU counts
are positive whole numbers, with no `+` or fractional form. Lightcone does not
maintain SkyPilot's accelerator alias registry: use the labels configured in
`resources` or use `GPU:N`. Catalog shapes use `accelerators: A100:4` or
`accelerators: {A100: 4}`. See [GPU allocations](../user/cluster.md#gpu-allocations).

Time accepts positive durations with day/hour/minute/second units, such as `30m`,
`1h30m`, or `45s`. Without
`--time`, the chosen offer's default walltime applies, if it has one. `fast` is a
service class, not a queue-time promise. Limits apply to each allocation; aggregate
quotas remain with the native backend.

A local allocation ends at its walltime, after its idle timeout, or at whichever
comes first when it has both. The idle timeout is Dask's scheduler
`idle-timeout`: running or queued tasks keep the allocation alive, and new work
restarts the countdown; connected clients and `status` queries do not. When it
expires, the allocation ends, and both its name and this machine's one local
allocation are free again. A walltime ends the allocation even during active work.
`down` still ends it at once.

For Slurm, time is a finite native `--time` request, so a Slurm offer needs a
`time.default` and cannot declare `time.idle`:

```text
Error: Slurm allocations end at their native walltime: set the offer's time.default and remove time.idle
```

Slurm's overtime and
termination-grace policy determines actual expiry and can allow unlimited
overrun; Lightcone supplies no independent Slurm runtime deadline. A partition
is passed only when explicitly set in the offer's configuration.

The first eligible offer wins; an offer this host cannot provide is skipped, and
the error lists why when nothing matches. An invalid configuration or failed
submission is an error, with no automatic resubmission elsewhere. An uncertain submission error
includes its token and any known cluster ID. Inspect existing allocations before
retrying it.

On `status`, `--wait` requires a CLUSTER. On both commands, `--timeout` requires
`--wait`. Launch rejects `--wait --dry-run`. A waiting launch prints its cluster
name only once ready; JSON adds `ready: true`. A timeout or startup failure exits
1 and includes the accepted immutable ID in the error. Waiting never resubmits
or terminates the accepted allocation.

Only one local cluster may run per user on a machine, across names, namespaces,
and configured roots. A launch that finds one of your local clusters running in
the process table fails until that cluster ends; launches that overlap can both
succeed.

`--json` emits versioned (`schema_version: 1`), allowlisted data without
scheduler credentials:

| Command | Keys |
|---|---|
| `launch` | `plan`, `id`, `name`, `accepted` (`ready: true` after `--wait`; only `plan` with `--dry-run`) |
| `status CLUSTER` | `id`, `name`, `phase`, `allocation`, `dask`, `reason`, `native_state` |
| `status` | `clusters` (a list of the above) and `errors` (by connection name) |
| `down` | `id`, `name`, `termination_requested` |
| any failure | `error`, `id`, `submission_token`, on stdout, with exit 1 |

`phase` is `pending`, `active`, `stopping`, `ended`, or `unknown`. `allocation`
holds `num_nodes`, per-node `resources`, and their `evidence` (`configured`,
`requested`, or `unknown`). Resource objects contain `cpus`, `memory` in GiB,
and `accelerators` as a one-entry type/count mapping, or `null` for CPU-only shapes.
`dask` is observed separately: `observation`
(`unverified`, `reachable`, or `unreachable`), `ready`, and `workers`. A
discovery that partially succeeds still exits 1. Native errors, invalid
requests, and readiness timeouts also exit 1.
