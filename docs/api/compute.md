# lightcone.engine.compute

The allocation boundary shared by CLI lifecycle operations and execution.
`Compute` loads resource policy and obtains fresh native observations.
It owns no service, registry, or saved current-cluster selection.

| Symbol | Contract |
|---|---|
| `Request.parse(...)` | Exact/minimum CPU and memory requests, exact accelerator type/count, node count, walltime, startup class. |
| `Catalog.load(path)` | Ordered fixed shapes and stable connection namespaces; use the built-in local catalog only when the implicit default file is absent. |
| `Compute.plan(request, *, name=None)` | Select an eligible offer and freeze its native launch settings and optional name without allocation. |
| `Compute.launch(plan)` | Check names across native authorities, generate one if omitted, submit once, and return a self-contained `Identity`. |
| `Compute.discover()` | Snapshots and per-connection errors, querying each authority once. |
| `Compute.status(cluster_id, wait=False, timeout=300)` | Resolve a name or full ID; return native allocation state plus authenticated Dask readiness. Waiting backs off from one to 30 seconds between native queries. |
| `Compute.down(cluster_id)` | Resolve a name or full ID, request native termination independent of scheduler health, and return the canonical `Identity`. |
| `connect(cluster_id, timeout=10)` | Resolve a name or full ID; borrow a standard Dask client, closing the client but never the allocation. |
| `Provider` | `plan`, `launch`, `discover`, `inspect`, `connect`, `terminate`. |

`Catalog.load()` defaults to `~/.lightcone/compute.yaml`. When that implicit file
is absent, the built-in catalog exposes a `local` offer: one CPU, 1 GiB, one node,
fast startup, 30-minute default and two-hour maximum lifetime. Successful Linux
CUDA discovery adds one GPU offer per native model: `local-gpu` for one model,
or `local-gpu-1`, `local-gpu-2`, etc. GPU discovery failure preserves the CPU offer.
It creates no configuration file or allocation. Configured catalogs replace it completely.
Missing paths selected through an argument or `LC_COMPUTE_CONFIG`, unreadable
files, and invalid catalogs remain errors. Stable connection namespaces let
separate invocations discover and attach to the same local allocations.

`model.py` defines the shared Pydantic models: `Connection`, `Offer`, `Resources`, `Accelerator`,
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

Compute memory accepts bare GiB quantities and SkyPilot-style binary units:
`32`, `32GB`, and `32GiB` agree. CPU and memory requests accept a trailing `+`.
`Accelerator` accepts one `NAME[:COUNT]` or one-entry mapping, such as `A100:4`
or `{A100: 4}`, and serializes to that mapping. Counts are exact positive integers;
type matching is case-insensitive, and the generic name `GPU` accepts any model.
No accelerator registry or model alias expansion is maintained. Slurm's
`config.gpu_type` maps a named catalog accelerator to its native GRES type.

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

Workers advertise standard Dask `CPU`, `MEMORY`, and `GPU` resources; memory is measured
in bytes. `engine.execution_resources.TaskResources` validates ASTRA's
`recipe.resources` into whole CPUs, bytes, and a whole GPU count at
execution admission. `plan.Task` preserves the ASTRA mapping so read-only
classification does not impose executor restrictions. `requirements(workers)`
checks that one worker can satisfy it and returns the resource dictionary used
by `Client.submit`.
An omitted memory request reserves the full homogeneous worker budget;
`whole_worker=True` reserves CPU, memory, and GPUs for a probe. Recipe GPU counts
default to zero; a GPU recipe reserves the full GPU budget of a fitting worker,
while exposing only the requested device count. This serializes GPU recipes per
worker without a device-assignment service. Unsupported disk/type requests and
fractional CPU/GPU counts fail before execution.

The materialize scheduler validates every selected task before preparation or
submission, preventing earlier tasks from starting before a later impossible
request is discovered, then passes each task's reservation explicitly to
submission. Allocation and task requests share byte conversion utilities; their
models remain distinct because allocation selection supports minimum quantities
and node counts. Standard Dask scheduling accounts for
concurrent CPU, memory, and GPU reservations; Dask execution-thread counts remain a
separate concurrency cap. Reservations do not impose hard limits on recipe
subprocesses. Recipe `time_limit` is unsupported and explicitly refused before
preparation or execution; allocation walltime remains supported.

Recipe memory remains ASTRA-style: `8Gi` is binary, `8GB` is decimal, and units
are required. Allocation memory follows the compute convention above; keep the
two parsers' contracts explicit even though they share exact byte arithmetic.

## GPU discovery and visibility

`engine.gpu.inventory()` uses a short isolated stdlib process to query the CUDA
Driver API for native UUIDs and model names, respecting `CUDA_VISIBLE_DEVICES`.
`visible_devices()` returns those UUIDs; `device_paths()` lists NVIDIA character
device nodes for direct sandbox grants. CUDA is never initialized in the reusable
worker, and no additional GPU Python dependency or custom Dask worker is needed.
Missing drivers produce an empty inventory. Discovery rejects invalid identities,
driver failures, and partitioned MIG devices rather than inventing capacity.

Local launch selects devices matching the offer, freezes their UUID mask in the
allocation environment, and checks the visible count before starting Dask. Slurm
validates native GPU capacity and CUDA visibility before advertising the offer's
GPU budget. Workers check recipe visibility before resetting output files.
Slurm bootstrap sets `CUDA_DEVICE_ORDER=PCI_BUS_ID` before discovery so CUDA
interprets native numeric masks in Slurm/NVML's device order.
Probes receive the reserved GPU count explicitly and never expose excess native
devices. CPU commands receive an empty CUDA mask without GPU discovery.

The sandbox applies masks per command, preserving the worker's shared environment.
OCI backends request native device injection by UUID; direct policies grant known
NVIDIA device nodes. Visibility is cooperative, with native permissions and cgroups
still authoritative. See [GPU deployment requirements](../user/cluster.md#gpu-allocations).

## Execution output and teardown

`output.py` transports byte chunks through standard Dask events so detached
workers' output reaches the invoking CLI. It uses the borrowed client's event
topic, which the schedulers lc launches drop as soon as the client disconnects
(`runtime.SCHEDULER_CONFIG`), rather than retaining a separate topic for every
command. A driver that exits before every task reports says so with
`UNSTOPPED`: closing a client cannot prove that a remote subprocess stopped. Probes preserve both streams;
materialization sends recipe output to stderr to leave stdout for its report.

Local teardown drains the allocation's validated process group rather than
assuming the owner's exit proves every child stopped. Boot UUID, UID, process
session and the exact command containing a random allocation token establish
identity without depending on hostname or wall-clock creation time. Discovery
skips other boot sessions; explicit operations refuse them because this process
cannot establish their state on another host. Failed unpublished launches
are cleaned up, and incomplete locator directories do not hide healthy allocations.
An allocation verified as ended, by `down` or by discovery, is retired: its TLS
material, scheduler files and scratch are removed, and a marker lets discovery
skip it unread. Its identity record stays, so a full ID still reports `ended`.
Cancellation and concurrent project writers are not made safe by allocation
management; callers must respect the documented execution limits. Containers
managed outside that process group can survive local teardown.

Tests cover deterministic selection, malformed identities and catalogs, partial
native failures, acceptance ambiguity, PID reuse, detached local lifetime, standard
Dask bootstrap, and explicit execution through borrowed clients. Slurm command
contracts are simulated; a real NERSC submission remains a deployment check.
