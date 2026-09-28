# lightcone.engine.compute

The allocation boundary shared by CLI lifecycle operations and execution.
`Compute` loads resource policy and obtains fresh native observations.
It owns no service, registry, or saved current-cluster selection.

| Symbol | Contract |
|---|---|
| `Request.parse(...)` | Common exact/minimum CPU and memory requests, node count, walltime, startup class. |
| `Catalog.load(path)` | Ordered fixed shapes and stable connection namespaces; use the built-in local catalog only when the implicit default file is absent. |
| `Compute.plan(request, *, name=None)` | Select an eligible offer and freeze its native launch settings and optional name without allocation. |
| `Compute.launch(plan)` | Check names across native authorities, generate one if omitted, submit once, and return a self-contained `Identity`. |
| `Compute.discover()` | Snapshots and per-connection errors, querying each authority once. |
| `Compute.status(cluster_id, wait=False, timeout=300)` | Resolve a name or full ID; return native allocation state plus authenticated Dask readiness. |
| `Compute.down(cluster_id)` | Resolve a name or full ID, request native termination independent of scheduler health, and return the canonical `Identity`. |
| `connect(cluster_id, timeout=10, config_path=None)` | Resolve a name or full ID; borrow a standard Dask client, closing the client but never the allocation. |
| `Provider` | `plan`, `launch`, `discover`, `inspect`, `connect`, `terminate`. |

`Catalog.load()` defaults to `~/.lightcone/compute.yaml`. When that implicit file
is absent, the built-in catalog exposes one `local` offer: one CPU, 1 GiB, one node,
fast startup, 30-minute default and two-hour maximum lifetime. It creates no
configuration file or allocation. Configured catalogs replace it completely.
Missing paths selected through an argument or `LC_COMPUTE_CONFIG`, unreadable
files, and invalid catalogs remain errors. Stable connection namespaces let
separate invocations discover and attach to the same local allocations.

`local.py` and `slurm.py` implement the provider protocol. Adding an adapter means
adding one provider factory and its native mapping; `run` and `materialize` only
borrow clients through the common API. Provider settings stay behind that seam.
`runtime.py` owns private files, standard TLS material, and authenticated scheduler
identity checks. `local_runtime.py` and `slurm_bootstrap.py` compose stock Dask
components; they do not define custom workers or membership protocols.

Configured connection and scratch roots are resolved before managed paths are
appended, so filesystem aliases such as a symlinked home directory are supported.
Managed directories and credential files retain strict symlink, ownership, and
permission checks, including modes `0700` and `0600`, respectively.

Slurm planning includes a partition only when explicitly configured and does
not query or freeze the site's time policy. Every launch requests a finite native
`--time`; native overtime and termination grace govern actual expiry, with no
independent Lightcone deadline or guarantee of a finite overrun.

`Snapshot` distinguishes native allocation evidence from scheduler observations.
No live allocation size is filled from today's catalog. Connection namespaces
persist independently of offers, and IDs encode native incarnation evidence
without a UUID-to-job lookup database. Exceptions retain known cluster IDs and
submission tokens for partial/ambiguous acceptance.

`Identity.name` is a human-facing name; `Identity.encode()` is the immutable
allocation reference. Generated names use `lc-` plus 12 hexadecimal characters
from a standard-library UUID4. Launch checks every configured connection and
rejects explicit duplicates or incomplete discovery before submission. Name
resolution also requires complete discovery and exactly one current match.
Concurrent launches can still race; ambiguous names are refused. Names can be
reused after termination, while full IDs continue to identify the original
allocation without discovering unrelated connections. Slurm carries the name
in `JobName=lc-v1-<name>` and the submission token in
`Comment=lightcone:v1:kind=dask:token=<32hex>`; local private locators carry the
encoded identity. Slurm discovery and lifecycle checks verify both native fields
and the owner. A marked live job with no valid token makes discovery incomplete.
Neither is a second source of lifecycle state or a name-to-ID registry.

The Slurm provider resolves its user ID once through `id -u` using the same
command runner as Slurm. Discovery, accounting, cancellation, and allocation
ownership checks all use that ID. Commands currently execute locally; filesystem
ownership checks still validate the local process's access to connection material.

Historical Slurm identity requires the accounting `Comment` field. Slurm stores
it when `AccountingStoreFlags` includes `job_comment`; without a matching retained
token, a missing live job remains unknown and cannot authorize cancellation.
See [Slurm's accounting field documentation](https://slurm.schedmd.com/sacct.html).

Execution submits ordinary tasks through the borrowed client's `submit` method.
Dask chooses the workers and handles dependencies; invocation-specific keys prevent
unintended reuse across commands. There is no worker-selection layer, per-worker
preflight orchestration, source fingerprinting, or login-node guard. Driver-side
preparation and the existing task runtime/sandbox checks remain in their owners.
`output.py` transports byte chunks through standard Dask events so detached
workers' output reaches the invoking CLI. It uses the borrowed client's event
topic, which Dask removes according to its native client-disconnect cleanup
policy, rather than retaining a separate topic for every command. Probes preserve both streams;
materialization sends recipe output to stderr to leave stdout for its report.

Local teardown drains the allocation's validated process group rather than
assuming the owner's exit proves every child stopped. Boot UUID, UID, process
session and the exact command containing a random allocation token establish
identity without depending on hostname or wall-clock creation time. Discovery
skips other boot sessions; explicit operations refuse them because this process
cannot establish their state on another host. Failed unpublished launches
are cleaned up, and incomplete locator directories do not hide healthy allocations.
Cancellation and concurrent project writers are not made safe by allocation
management; callers must respect the documented execution limits. Containers
managed outside that process group can survive local teardown.

Tests cover deterministic selection, malformed identities and catalogs, partial
native failures, acceptance ambiguity, PID reuse, detached local lifetime, standard
Dask bootstrap, and explicit execution through borrowed clients. Slurm command
contracts are simulated; a real NERSC submission remains a deployment check.
