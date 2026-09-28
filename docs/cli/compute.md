# lc compute

Manage explicitly allocated Dask clusters using resource offers.
No project is required for these commands.

```text
lc compute resources [--json]
lc compute launch --cpus VALUE --memory VALUE
    [--name NAME] [--num-nodes N] [--time DURATION] [--startup fast] [--dry-run] [--json]
lc compute status [CLUSTER] [--wait] [--timeout SECONDS] [--json]
lc compute down CLUSTER [--json]
```

Without configuration, `resources` exposes a built-in `local` offer: one CPU,
1 GiB, one node, fast startup, and a 30-minute default lifetime (two-hour maximum).
Launch it with `lc compute launch --cpus 1 --memory 1`; execution still requires
the returned cluster name or its full immutable ID.

`~/.lightcone/compute.yaml`, when present, replaces this built-in catalog.
`LC_COMPUTE_CONFIG` selects another file for both compute and execution commands,
so an allocation launched from a catalog can be found by `lc run` and
`lc materialize` too. Missing explicit paths and invalid catalogs are errors. Only a missing implicit default file enables the
built-in catalog, without writing a file or starting any compute. See the
[local and Slurm setup](../user/cluster.md) for examples.

| Command | Behavior |
|---|---|
| `resources` | Ordered available offers, per-node shape, node limit, default/maximum time, and startup class. Free capacity remains unknown. |
| `launch` | Resolve one resource request and submit exactly once; print only the cluster name to stdout on acceptance. |
| `launch --dry-run` | Show the resolved shape and native launch parameters without allocation. |
| `status` | List one `name: status` line per current allocation across every configured connection; report the connections that could not be queried. |
| `status CLUSTER` | Resolve a name or full ID, inspect native state, and probe Dask readiness separately. |
| `status CLUSTER --wait` | Wait for readiness: the allocation is active and every node's worker is connected. The default deadline is 300 seconds, and queries grow less frequent as the wait goes on (up to every 30 seconds). Exits 1 on timeout, or at once if the allocation is ending or has ended; the allocation is left unchanged. |
| `down CLUSTER` | Request native termination even if the scheduler is unavailable. An allocation that has already ended is a successful no-op when addressed by full ID; its name no longer resolves. A Slurm job that has left the queue is refused unless accounting confirms it ended. |

Choose a name with `--name analysis`, or omit it to generate `lc-` followed by
12 random hexadecimal characters. Names contain 1–63 lowercase ASCII letters,
digits, or hyphens; they start with a letter and end with a letter or digit.
Launch guidance goes to stderr, so the default output can be captured directly:

```bash
CLUSTER=$(lc compute launch --cpus 1 --memory 1)
lc compute status "$CLUSTER" --wait
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

CPU quantities are logical CPUs **per node**, memory is **GiB per node**, and
`--num-nodes` defaults to one. Bare quantities are exact; `4+` means at least four.
Time accepts positive durations with day/hour/minute/second units, such as `30m`,
`1h30m`, or `45s`. Without
`--time`, the chosen offer's default applies. `fast` is a service class, not a
queue-time promise. Limits apply to each allocation; aggregate quotas remain
with the native backend.

For Slurm, time is a finite native `--time` request. Slurm's overtime and
termination-grace policy determines actual expiry and can allow unlimited
overrun; Lightcone supplies no independent Slurm runtime deadline. A partition
is passed only when explicitly set in the offer's configuration.

The first eligible offer wins; an offer this host cannot provide is skipped, and
the error lists why when nothing matches. An invalid configuration or failed
submission is an error, with no automatic resubmission elsewhere. An uncertain submission error
includes its token and any known cluster ID. Inspect existing allocations before
retrying it.

`--wait` requires a CLUSTER, and `--timeout` requires `--wait`.

`--json` emits versioned (`schema_version: 1`), allowlisted data without
scheduler credentials:

| Command | Keys |
|---|---|
| `launch` | `plan`, `id`, `name`, `accepted` (only `plan` with `--dry-run`) |
| `status CLUSTER` | `id`, `name`, `phase`, `allocation`, `dask`, `reason`, `native_state` |
| `status` | `clusters` (a list of the above) and `errors` (by connection name) |
| `down` | `id`, `name`, `termination_requested` |
| any failure | `error`, `id`, `submission_token`, on stdout, with exit 1 |

`phase` is `pending`, `active`, `stopping`, `ended`, or `unknown`. `allocation`
holds `num_nodes`, per-node `resources`, and their `evidence` (`configured`,
`requested`, or `unknown`). `dask` is observed separately: `observation`
(`unverified`, `reachable`, or `unreachable`), `ready`, and `workers`. A
discovery that partially succeeds still exits 1. Native errors, invalid
requests, and readiness timeouts also exit 1.
