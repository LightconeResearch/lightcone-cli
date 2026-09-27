# lc compute

Manage explicitly allocated Dask clusters using resource offers.
No project is required for these commands.

```text
lc compute [--config PATH] resources [--json]
lc compute [--config PATH] launch --cpus VALUE --memory VALUE
    [--num-nodes N] [--time DURATION] [--startup fast] [--dry-run] [--json]
lc compute [--config PATH] status [CLUSTER_ID] [--wait] [--timeout SECONDS] [--json]
lc compute [--config PATH] down CLUSTER_ID [--json]
```

Without configuration, `resources` exposes a built-in `local` offer: one CPU,
1 GiB, one node, fast startup, and a 30-minute default lifetime (two-hour maximum).
Launch it with `lc compute launch --cpus 1 --memory 1`; execution still requires
the returned cluster ID.

`~/lightcone-compute.yaml`, when present, replaces this built-in catalog.
`LC_COMPUTE_CONFIG` selects another file for both compute and execution commands;
`--config PATH` overrides it for this invocation. Missing explicit paths and
invalid catalogs are errors. Only a missing implicit default file enables the
built-in catalog, without writing a file or starting any compute. See the
[local and Slurm setup](../user/cluster.md) for examples.

| Command | Behavior |
|---|---|
| `resources` | Ordered available offers, per-node shape, node limit, default/maximum time, and startup class. Free capacity remains unknown. |
| `launch` | Resolve one resource request and submit exactly once; print the opaque cluster ID on acceptance. |
| `launch --dry-run` | Show the resolved shape and native launch parameters without allocation. |
| `status` | Query each configured native authority once; retain partial discovery errors. |
| `status ID` | Inspect native state and probe Dask readiness separately. |
| `status ID --wait` | Wait for readiness, with a default deadline of 300 seconds; timeout leaves the allocation unchanged. |
| `down ID` | Request native termination even if the scheduler is unavailable. |

CPU quantities are logical CPUs **per node**, memory is **GiB per node**, and
`--num-nodes` defaults to one. Bare quantities are exact; `4+` means at least four.
Time accepts positive whole minutes or hours, such as `30m` or `2h`. Without
`--time`, the chosen offer's default applies. `fast` is a service class, not a
queue-time promise. Limits apply to each allocation; aggregate quotas remain
with the native backend.

The first eligible offer wins. An invalid configuration or failed submission is
an error, with no automatic resubmission elsewhere. An uncertain submission error
includes its token and any known cluster ID. Inspect existing allocations before
retrying it.

`--json` emits versioned, allowlisted data without scheduler credentials. Status
reports phases `pending`, `active`, `stopping`, `ended`, or `unknown`; allocation
evidence is separate from Dask `observation`, `ready`, and worker count. Discovery
can partially succeed and still exit 1. Native errors, invalid requests, and
readiness timeouts also exit 1.
