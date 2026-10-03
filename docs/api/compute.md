# lightcone.engine.compute

The allocation boundary shared by CLI lifecycle operations and execution.
`Compute` loads resource policy and obtains fresh native observations.
It owns no service, registry, or saved current-cluster selection.

| Symbol | Contract |
|---|---|
| `Request.parse(...)` | Exact/minimum CPU and memory requests, exact accelerator type/count, node count, walltime, startup class. |
| `Catalog.load(path)` | Ordered fixed shapes, each naming its provider; apply local defaults or disable policy alongside configured offers. |
| `Compute.plan(request, *, name=None)` | Select an eligible offer and freeze its native launch settings and optional name without allocation. |
| `Compute.launch(plan)` | Check names across native authorities, generate one if omitted, submit once, and return a self-contained `Identity`. |
| `Compute.discover()` | Snapshots and per-provider errors, querying each provider in `Catalog.providers` once. |
| `Compute.status(cluster_id, wait=False, timeout=300)` | Resolve a name or full ID; return native allocation state plus authenticated Dask readiness. Waiting backs off from one to 30 seconds between native queries. |
| `Compute.down(cluster_id)` | Resolve a name or full ID, request native termination independent of scheduler health, and return the canonical `Identity`. |
| `connect(cluster_id, timeout=10)` | Resolve a name or full ID; borrow a standard Dask client, closing the client but never the allocation. Submits one no-op task, so a caller's preparation restarts the idle countdown. |
| `Provider` | `plan`, `launch`, `discover`, `inspect`, `connect`, `terminate`. |

`Catalog.load()` defaults to `~/.lightcone/compute.yaml`. The built-in `local`
offer uses detected usable CPUs and RAM, one node, fast startup, and no walltime:
it ends after 30 minutes without task activity. `local.resources` overrides its
CPU/RAM budget and `local.time` its time limits;
`local.enabled: false` blocks local launch and execution while local discovery
continues for inspection and termination. Remote catalogs retain the implicit local
offer unless disabled. Explicit local offers replace it and cannot be combined
with `local.resources` or `local.time`. GPU offers require explicit configuration.
Loading creates no configuration file or allocation.
The effective local policy also disables local offers on recognized NERSC login
nodes: nonempty `NERSC_HOST` and a short hostname matching `login[0-9]+`.
Explicit enablement and Slurm job environment variables do not override this
guard; interactive compute nodes remain eligible. Local planning, launch, and
execution check the same policy, while status and termination remain available.
Missing paths selected through an argument or `LC_COMPUTE_CONFIG`, unreadable
files, and invalid catalogs remain errors. `Catalog.providers` names the native
authorities to query: those the offers use, and always `local`. Each provider
keeps its allocations in one directory under the catalog's `connection_root`, so
separate invocations discover and attach to the same allocations.

`Compute.plan_local()` selects only local offers and defaults the name to `local`.
CLI `launch --wait` waits through `Compute.status` using the accepted immutable ID;
errors retain that ID without resubmission or termination.

`model.py` defines the shared Pydantic models: `Offer`, `Resources`, `Accelerator`,
`TimeLimits`, `Startup`, `Request`, `Identity`, `LaunchPlan`, and `Snapshot`.
`Catalog` validates YAML directly into these objects, which providers also use.
Unknown common fields are rejected; schema errors identify paths such as
`offers.0.resources.cpus` without echoing input values. The YAML loader rejects
duplicate and non-string mapping keys before model validation. Each offer's
provider-specific `config` mapping remains the provider's responsibility.

Units are explicit. `Resources.memory_gib` stores exact decimal GiB (the YAML key
is `memory`), and `memory_bytes` derives an exact integer. Native observations use
`Resources.from_bytes(...)`; requests store `Request.memory_bytes`. `TimeLimits`
keeps the configured `default`, `max`, and `idle` duration strings, each optional
but requiring a `default` or an `idle`, and exposes `default_seconds`,
`max_seconds`, and `idle_seconds` (`None` when unset). A `LaunchPlan` carries the
resolved hard walltime as `seconds` and derives `idle_seconds` from its offer;
either may be `None` for a local plan, while Slurm plans always have `seconds` and
never `idle_seconds`. `Startup.class_` corresponds to YAML `class`.
`Offer.provider` names a factory in `PROVIDERS`, which receives the catalog's
`connection_root`.

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

The Slurm bootstrap runs one stock `Nanny` per rank, each with a separate worker
process; rank zero also hosts the scheduler. The Nanny restarts an exited worker
and supplies Dask's default `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, and
`OPENBLAS_NUM_THREADS` values of `1`, preserving explicit launch environment
values. Sandbox policy forwards those effective values into recipe containers.
`memory_limit=0` remains deliberate: Dask's worker memory accounting excludes
the external recipe subprocesses. The payload uses
`srun --kill-on-bad-exit=0 --wait=0` to avoid terminating healthy ranks merely
because another rank exited. Site OOM policy can still terminate the step or
allocation, and there is no recovery for a dead scheduler or Nanny. Dask can
reschedule lost tasks, but surviving recipe subprocesses are not fenced from
those retries. Connection readiness still requires every expected worker.
See [Dask's Nanny](https://distributed.dask.org/en/stable/worker.html#nanny),
[Dask resilience](https://distributed.dask.org/en/stable/resilience.html), and
[Slurm's `srun` options](https://slurm.schedmd.com/srun.html).

The configured `connection_root` and scratch roots are resolved before managed paths are
appended, so filesystem aliases such as a symlinked home directory are supported.
Managed directories and credential files retain strict symlink, ownership, and
permission checks, including modes `0700` and `0600`, respectively.

Slurm planning includes a partition only when explicitly configured and does
not query or freeze the site's time policy. Every launch requests a finite native
`--time`; native overtime and termination grace govern actual expiry, with no
independent Lightcone deadline or guarantee of a finite overrun.

`Snapshot` distinguishes native allocation evidence from scheduler observations.
No live allocation size is filled from today's catalog. IDs encode their provider
and native incarnation evidence, so a full ID routes without consulting the offers
and without a UUID-to-job lookup database. Exceptions retain known cluster IDs and
submission tokens for partial/ambiguous acceptance.

`Identity.name` is a human-facing name; `Identity.encode()` is the immutable
allocation reference. Generated names use `lc-` plus 12 hexadecimal characters
from a standard-library UUID4. Launch checks every provider in `Catalog.providers` and
rejects explicit duplicates or incomplete discovery before submission. Name
resolution also requires complete discovery and exactly one current match.
Concurrent launches can still race; ambiguous names are refused. Names can be
reused after termination, while full IDs continue to identify the original
allocation without discovering other providers. Slurm carries the name
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
preflight orchestration, or source fingerprinting. The local login-node guard
does not restrict remote Slurm execution from a login node. Driver-side
preparation and the existing task runtime/sandbox checks remain in their owners.

Workers advertise standard Dask `CPU`, `MEMORY`, and `GPU` resources; memory is measured
in bytes. `engine.execution_resources.TaskResources` validates ASTRA's
`recipe.resources` into whole CPUs, bytes, and a whole GPU count at
execution admission. `plan.Task` preserves the ASTRA mapping so read-only
classification does not impose executor restrictions. `worker_capacities(workers)`
normalizes advertised budgets once; `requirements(capacities)` checks that one
worker can satisfy a task and returns its `Client.submit` resource dictionary.
Omitted memory adds no `MEMORY` reservation. `whole_worker=True` reserves CPU,
memory, and GPUs for a probe. Recipe GPU counts default to zero; a GPU recipe
reserves the full GPU budget of a fitting worker
and inherits its whole allocation mask. The requested count is a minimum, not a
visibility limit. This serializes GPU recipes per worker without device assignment.
Unsupported disk/type requests and fractional CPU/GPU counts fail before execution.

The driver reuses the read-only classification walk before admission. Known
current or unrefreshed behind outputs become `TaskResult` values, without Dask
submission or resource reservations. Tasks that may execute, including dependents
of potentially rebuilt outputs, have their resource requests validated before
preparation. Workers recheck actual upstream digests and may still skip a reserved
task if its inputs prove unchanged. Allocation and task requests share byte
conversion utilities; their models remain distinct because allocation selection supports minimum quantities
and node counts. Standard Dask scheduling accounts for
concurrent CPU, memory, and GPU reservations; Dask execution-thread counts remain a
separate concurrency cap. Reservations do not impose hard limits on recipe
subprocesses. Recipe `time_limit` is unsupported and explicitly refused before
preparation or execution; allocation walltime remains supported.

Recipe memory remains ASTRA-style: `8Gi` is binary, `8GB` is decimal, and units
are required. Allocation memory follows the compute convention above; keep the
two parsers' contracts explicit even though they share exact byte arithmetic in
`units.py`. Allocation duration parsing stays in `compute.model.duration`, raising
`ValueError` for Pydantic; `Request.parse` converts it to `ComputeError`.

## GPU allocation and visibility

Local GPU offers require Linux and an explicit nonempty `CUDA_VISIBLE_DEVICES`.
Planning freezes that mask and optional `CUDA_DEVICE_ORDER`; launch passes them
to the worker unchanged. Count and model are catalog declarations, not hardware
observations. The built-in local offer remains CPU-only.

Slurm requests native GPU GRES and validates `SLURM_GPUS_ON_NODE` before
advertising the worker's GPU budget. Bootstrap preserves Slurm's CUDA mask and
sets `CUDA_DEVICE_ORDER=PCI_BUS_ID`. There is no CUDA probe, device inventory, or
custom Dask worker.

The sandbox's `use_gpus` policy option inherits the worker's mask for GPU commands
and supplies an empty mask for CPU commands, without modifying the reusable
worker's environment. Direct GPU policies grant native NVIDIA character devices.
Container GPU execution uses podman-hpc's `--gpu`. Explicit GPU recipes on ordinary
Docker or Podman are refused before image preparation; probes use a CPU policy and
report that GPU access is unavailable while retaining their whole-worker reservation.
Native permissions and cgroups remain authoritative. NVIDIA devices, including UVM,
must already exist; policy construction does not load drivers or create devices.
See [GPU deployment requirements](../user/cluster.md#gpu-allocations).

## Execution output and teardown

The driver still saves each output in its own Git/annex commit while Dask runs
the submitted graph. This serial storage work can dominate many short recipes;
adding workers does not accelerate it.

`output.py` transports byte chunks through standard Dask events so detached
workers' output reaches the invoking CLI. It uses the borrowed client's event
topic, which the schedulers lc launches drop as soon as the client disconnects
(`runtime.SCHEDULER_CONFIG`), rather than retaining a separate topic for every
command. A driver that exits before every task reports says so with
`UNSTOPPED`: closing a client cannot prove that a remote subprocess stopped. Probes preserve both streams;
materialization sends recipe output to stderr to leave stdout for its report.

Local allocations are limited to one per user on each machine, independent of
connection roots. Before spawning, the launcher scans the process
table for a live owner of the same user: a session leader running `-P -m
lightcone.engine.compute.local_runtime <directory>`, which excludes workers forked
from it. The process table spans every catalog and connection root and needs no
file lock, which some shared home filesystems, NERSC's included, do not support.
The owner's directory argument locates its identity record, so a refused launch
names the running cluster, its connection root, and the catalog it was launched
with. A record not yet written means the owner is still starting; one that is
missing or unreadable after that never recovers, so the refusal names the
owner's PID instead. Two limits are accepted rather than closed with a lock:
launches that overlap can both pass the scan, and the scan covers one PID
namespace, so a container sharing the home directory does not see the host's
owner.

A startup pipe lets the owner proceed only after the launcher publishes its
identity and launch records. If the launcher dies before completing publication,
the pipe closes and the owner exits. Failures before identity
publication remove the launcher's private files; a published identity remains
inspectable after a startup failure.

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
native failures, acceptance ambiguity, PID reuse, detached local walltime and idle
expiry, standard
Dask bootstrap, and explicit execution through borrowed clients. Slurm command
contracts are simulated; a real NERSC submission remains a deployment check.
