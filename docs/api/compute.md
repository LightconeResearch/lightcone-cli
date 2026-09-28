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
| `Compute.status(cluster_id, wait=False, timeout=300)` | Resolve a name or full ID; return native allocation state plus authenticated Dask readiness. Waiting backs off from one to 30 seconds between native queries. |
| `Compute.down(cluster_id)` | Resolve a name or full ID, request native termination independent of scheduler health, and return the canonical `Identity`. |
| `connect(cluster_id, timeout=10)` | Resolve a name or full ID; borrow a standard Dask client, closing the client but never the allocation. |
| `Provider` | `plan`, `launch`, `discover`, `inspect`, `connect`, `terminate`. |

`Catalog.load()` defaults to `~/.lightcone/compute.yaml`. When that implicit file
is absent, the built-in catalog exposes one `local` offer: one CPU, 1 GiB, one node,
fast startup, 30-minute default and two-hour maximum lifetime. It creates no
configuration file or allocation. Configured catalogs replace it completely.
Missing paths selected through an argument or `LC_COMPUTE_CONFIG`, unreadable
files, and invalid catalogs remain errors. Stable connection namespaces let
separate invocations discover and attach to the same local allocations.

`model.py` defines the shared Pydantic models: `Connection`, `Offer`, `Resources`,
`TimeLimits`, `Startup`, `Request`, `Identity`, `LaunchPlan`, and `Snapshot`.
`Catalog` validates YAML directly into these objects, which providers also use.
Unknown common fields are rejected; schema errors identify paths such as
`offers.0.resources.cpus` without echoing input values. The YAML loader rejects
duplicate and non-string mapping keys before model validation. Provider-specific
`launch` and `config` mappings remain the provider's responsibility.

Units are explicit. `Resources.memory_gib` stores exact decimal GiB (the YAML key
is `memory`), and `memory_bytes` derives an exact integer. Native observations use
`Resources.from_bytes(...)`; requests store `Request.memory_bytes`. `TimeLimits`
keeps the configured `default` and `max` duration strings and exposes
`default_seconds` and `max_seconds`. `Startup.class_` corresponds to YAML `class`.
Connection names exist only as catalog mapping keys, referenced by `Offer.connection`.

Model constructors take keyword arguments. `replace(...)` validates updates;
`model_dump()` and `model_validate()` support internal roundtrips without changing
units. Keep the explicit `as_dict()` methods for public CLI output so internal
configuration does not leak. Value models are frozen; `Snapshot` permits validated
updates as native and scheduler observations arrive. Nested settings dictionaries
and catalog collections are not deeply immutable.

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
and the owner. Discovery makes one `squeue` query, then one single-job
`scontrol` lookup per managed job. A marked live job with no valid token makes
discovery incomplete. A verified live job is cancelled whatever state Slurm
reports; a job absent from live jobs must prove from accounting that it ended.
Neither is a second source of lifecycle state or a name-to-ID registry.

The Slurm provider resolves its user ID once through `id -u` using the same
command runner as Slurm. Discovery, accounting, cancellation, and allocation
ownership checks all use that ID. The commands execute on the host running `lc`,
and filesystem ownership checks validate that process's access to connection
material.

`slurm.py` maps native states onto the common phases: `PENDING`, `CONFIGURING`,
`SUSPENDED`, `RESIZING` and the requeue states are `pending`, `RUNNING` is
`active`, `COMPLETING` is `stopping`, and every terminal state is `ended`. Any
other state is `unknown` for `status`, while `terminate` still cancels such a
job once its owner, name and token verify. `connect` pins the job's current
restart count and reads only that attempt's connection material, then checks the
count again after the TLS handshake, so a requeued job cannot hand over an
earlier attempt's scheduler. A job that is running but has not yet published its
scheduler, like a local allocation still starting, is refused with
`runtime.NOT_STARTED`.

Historical Slurm identity requires the accounting `Comment` field. Slurm stores
it when `AccountingStoreFlags` includes `job_comment`; without a matching retained
token, a missing live job remains unknown and cannot authorize cancellation.
See [Slurm's accounting field documentation](https://slurm.schedmd.com/sacct.html).

Execution submits ordinary tasks through the borrowed client's `submit` method.
Dask chooses the workers and handles dependencies; invocation-specific keys prevent
unintended reuse across commands. There is no worker-selection layer, per-worker
preflight orchestration, source fingerprinting, or login-node guard. Driver-side
preparation and the existing task runtime/sandbox checks remain in their owners.

Workers advertise standard Dask `CPU` and `MEMORY` resources; memory is measured
in bytes. `engine.execution_resources.TaskResources` validates ASTRA's
`recipe.resources` into whole CPUs, bytes, and optional walltime seconds at
execution admission. `plan.Task` preserves the ASTRA mapping so read-only
classification does not impose executor restrictions. `requirements(workers)`
checks that one worker can satisfy it and returns the resource dictionary used
by `Client.submit`.
An omitted memory request reserves the full homogeneous worker budget;
`whole_worker=True` reserves CPU and memory for a probe. Unsupported GPU/disk
requests and fractional CPUs fail before execution.

The materialize scheduler validates every selected task before preparation or
submission, preventing earlier tasks from starting before a later impossible
request is discovered, then passes each task's reservation explicitly to
submission. Allocation and task requests share byte and duration conversion
utilities; their models remain distinct because allocation selection supports
minimum quantities and node counts. Standard Dask scheduling accounts for
concurrent CPU and memory reservations; Dask execution-thread counts remain a
separate concurrency cap. Reservations do not impose hard limits on recipe
subprocesses. Task walltime uses the subprocess boundary's timeout and teardown,
independent of the native allocation's lifetime.

`output.py` transports byte chunks through standard Dask events so detached
workers' output reaches the invoking CLI. It uses the borrowed client's event
topic, which the schedulers lc launches drop as soon as the client disconnects
(`runtime.SCHEDULER_CONFIG`), rather than retaining a separate topic for every
command. Output-delivery errors cannot replace an execution-safety exception.
Probes preserve both streams;
materialization sends recipe output to stderr to leave stdout for its report.

`engine.execution.invocation` owns a short renewable authorization in the existing
scheduler. Each task claims its logical key before touching files. Completion
receipts preserve the original result if Dask recomputes a lost result; a running
or uncertain claim refuses replay and revokes the invocation. Missing state also
refuses execution. This uses ordinary tasks and `run_on_scheduler`, without a
custom worker, service, project lock, or persistent execution registry.
Driver heartbeats and worker authorization polls retry transient RPC failures
within the last confirmed 15-second lease. A failed RPC does not extend that
lease; explicit revocation, missing state, or expiry stops execution.

On exit the invocation revokes admission, cancels pending futures, and waits for
claimed tasks to acknowledge cleanup. Dask cancellation alone is insufficient:
running tasks poll authorization and the subprocess boundary stops their commands.
Only a positive `Invocation.stopped` flag permits restoring unconsumed outputs;
an exception from closing another context cannot manufacture that confirmation.
Without positive completion evidence, scheduler loss or an unacknowledged attempt
raises `ExecutionUncertain` and retains outputs. Known terminal uncertainty is
reported immediately. A finished task already confirms command cleanup and receipt
publication, so metadata cleanup failures cannot discard its result. Receipts are
removed best-effort after confirmed cleanup; uncertain records remain
until the allocation ends. They are not a recovery log for a later invocation.

Local teardown drains the allocation's validated process session rather than
assuming the owner's exit proves every child stopped. Boot UUID, UID, process
session and the exact command containing a random allocation token establish
identity without depending on hostname or wall-clock creation time. Discovery
skips other boot sessions; explicit operations refuse them because this process
cannot establish their state on another host. Failed unpublished launches
are cleaned up, and incomplete locator directories do not hide healthy allocations.
An allocation verified as ended, by `down` or by discovery, is retired: its TLS
material, scheduler files and scratch are removed, and a marker lets discovery
skip it unread. Its identity record stays, so a full ID still reports `ended`.
Concurrent invocations writing the same project remain unsupported. Command
cleanup covers process groups and native OCI container identities; recipes must
not daemonize into new sessions. Killing the command supervisor can leave an
external runtime's container alive, so an uncertain execution requires native
verification before output repair.

Tests cover deterministic selection, malformed identities and catalogs, partial
native failures, acceptance ambiguity, PID reuse, detached local lifetime, standard
Dask bootstrap, and explicit execution through borrowed clients. Slurm command
contracts are simulated; a real NERSC submission remains a deployment check.
