# Running on a Cluster

Allocate compute explicitly, then pass the returned cluster name to either execution
command. The same commands work for a local workstation and Slurm. No cluster is
started by `lc run` or `lc materialize`, even when Slurm environment variables are
present. `lc materialize --check` and `lc status` remain local project inspection.

## Start locally

No configuration is needed on a fresh installation. `lc compute launch` uses all
detected usable logical CPUs and RAM on this machine and names the cluster `local`.
The default lifetime is 30 minutes, with a maximum of two hours. Use `--time` to
change the lifetime. GPUs require explicit offers; see [GPU allocations](#gpu-allocations).

```bash
lc compute resources
lc compute launch --dry-run
CLUSTER=$(lc compute launch --wait)
lc run "$CLUSTER" -- python -c 'print("hello from the cluster")'
lc materialize "$CLUSTER"
lc compute down "$CLUSTER"
```

Run the execution commands from your project root. A launch returns when native
allocation is accepted; `launch --wait` or `status --wait` waits for Dask readiness.
Both accept `--timeout SECONDS` (default 300). Waiting failures retain the accepted
cluster ID and leave the allocation unchanged. Execution never
waits: `lc run` and `lc materialize` refuse a cluster that is not active with
every expected worker connected, for example:

```text
Error: this allocation's Dask scheduler has not started yet; wait for readiness with `lc compute status CLUSTER --wait`
```

Finishing a run detaches its client and leaves the cluster available for another
command. The allocation ends at its time limit or when you call `down`.

`lc compute status` lists allocations as `name: status`, one per line.
Use `lc compute status NAME` for resource details and Dask readiness.

## Local allocations

Local resources are cooperative limits, not an exclusive CPU/RAM reservation.
Only one local cluster can run per user on each machine. A launch checks the
process table for a running local cluster of yours and refuses if it finds one,
including one launched through a different name, catalog, namespace, or
connection root. End the existing cluster before launching another; once its
owner process exits, including on failure or walltime expiry, a new launch
proceeds. A refusal identifies the running cluster and its original catalog and
connection root. Use that catalog to inspect or stop the cluster if the current
catalog no longer includes its connection. If the cluster's record is missing or
damaged, the refusal names its process ID instead, to stop with `kill`.
Launches that overlap can both succeed, and the check sees only the processes
visible where `lc` runs: a launch inside a container does not see a cluster
started outside it.
An allocation owns a detached process session and standard `LocalCluster`: one
worker process with `task_slots_per_node` threads, and a scheduler that listens
on `127.0.0.1` over TLS. Its own logs are discarded; a startup failure is kept
and shown as the reason by `lc compute status`. At its time limit the whole
process session is killed with SIGKILL, so a recipe still running stops mid-write.
`down` sends SIGTERM, waits three seconds, then sends SIGKILL.
Private process locators are checked against the native boot UUID, UID, process
session, and exact command containing the allocation's random token before
attachment or termination. Hostname changes and clock adjustments do not change
that identity. Manage a local allocation from the host and boot session that
launched it. Other boot sessions are excluded from discovery, and an explicit
ID from one is refused rather than reported as stopped. Once an allocation has
ended, its credentials and scratch directory are removed; its full ID still
reports `ended`.
Local compute requires an enabled local policy and a valid local offer.
On NERSC login nodes it is disabled automatically, even with no catalog or with
`local.enabled: true`. The guard recognizes a nonempty `NERSC_HOST` and a short
hostname matching `login[0-9]+`; it does not perform DNS or scheduler queries.
The guard permits compute nodes such as `nid200021`, including interactive
sessions. A `SLURM_JOB_ID` variable does not exempt a login node.
See NERSC's [environment conventions](https://docs.nersc.gov/environment/) and
[interactive sessions](https://docs.nersc.gov/connect/vscode/).

A local connection's optional `launch` settings are `connection_root` (default
`~/.lightcone/compute`), `scratch_root` (default: the temporary directory),
`python` (default: the interpreter running `lc`), and `task_slots_per_node`
(default: all of the offer's CPUs). A local connection's `context`, when set, is
the hostname it belongs to. Local offers take no `config`.

## Cluster names

The local shortcut defaults to `local`. Explicit CPU/memory requests generate a
short name such as `lc-a1b2c3d4e5f6`. Override either with `--name`:

```bash
lc compute launch --name analysis --wait
lc compute down analysis
```

Names contain 1–63 lowercase ASCII letters, digits, or hyphens, starting with a
letter and ending with a letter or digit. Launch writes only the name to stdout;
readiness guidance goes to stderr. All execution and lifecycle commands accept
either that name or the full immutable ID available in launch and status JSON.

Names are checked against current allocations across all configured connections
before launch. An explicit duplicate is refused, and an autogenerated collision
is regenerated before submission. Discovery failures prevent this check from
succeeding. Concurrent launches can still choose the same name, so lookup also
refuses ambiguous names or incomplete discovery. Use a full ID to select a known
allocation directly when another connection cannot be queried.

This includes local connections when `local.enabled: false`: existing local
allocations remain visible and can still have conflicting names. Repair a
connection's discovery error before launching another cluster or resolving names.

A name may be reused once its allocation has ended. Keep the full ID when you
need a durable reference to one allocation; a later cluster with the same name
has a different ID. Names are discovered from allocation metadata, without a
separate name registry. Native state still decides whether an allocation exists.

## Customize resource offers

Create `~/.lightcone/compute.yaml`, or select a file with `LC_COMPUTE_CONFIG`.
For a smaller default local budget:

```yaml
version: 1
local:
  resources: {cpus: 4, memory: 8GiB}
```

Both CPU and memory are required in `local.resources`; omit that block to use
detected capacity. The same capacity validation applies to configured budgets.
This controls the default offer, not hard OS resource limits.

NERSC login nodes are guarded without setup. For other sites, or to disable local
compute on every node using the catalog, set:

```yaml
version: 1
local:
  enabled: false
# Add Slurm connections and offers as shown below.
```

This blocks local launches, including explicit local offers, and new execution
commands on local clusters. Existing local allocations remain inspectable and
stoppable; disabling does not kill them. Bare launch reports that local compute is
disabled; supply CPU/memory requirements to select a Slurm allocation. Select this
catalog on login nodes through `LC_COMPUTE_CONFIG`. This is Lightcone configuration
policy; native site permissions enforce machine-wide restrictions.
Leave `local.enabled` at its default to use local compute inside a NERSC
interactive compute-node session. The login-node guard still applies, and it
creates no configuration file.

By default, a built-in `local` connection and offer accompany remote offers, with
configured offers taking selection priority. If the catalog already defines local
connections, those offers replace the implicit local offer; omit `local.resources`
and size those offers directly. The no-resource shortcut selects the first eligible
local offer and defaults its cluster name to `local`.

Resource requests can select the built-in local offer when no earlier remote
offer is eligible. Set `local.enabled: false` for catalogs that must use only remote
compute. Without an explicit local connection, the connection name `local` is
reserved for the built-in backend; its offer name is also reserved while enabled.

The namespace is a stable UUID identifying a connection; keep it unchanged while
that connection's clusters exist.

For example, this catalog explicitly defines a local allocation:

```yaml
version: 1
connections:
  workstation:
    namespace: 22c84e48-2f0a-4cd2-90a2-30ce2e909bd1
    provider: local
offers:
  - name: workstation
    connection: workstation
    resources: {cpus: 4, memory: 8}
    max_nodes: 1
    time: {default: 30m, max: 2h}
    startup: {class: fast}
```

Set `LC_COMPUTE_CONFIG` to choose another file for all commands, including
`lc run` and `lc materialize`, which find clusters through the same catalog. A
missing explicit path or an invalid catalog is an error. An absent implicit default
file uses the default local policy. Stop existing built-in allocations before replacing
their connection namespace with your own.

This example keeps the built-in connection's namespace, so allocations launched
from the built-in offer stay visible and can still be stopped after the file
exists.

A catalog has `version: 1`, an optional `local` policy, a `connections` mapping,
and an ordered `offers` list. Connections and offers default to empty:

- A connection has a `namespace` (a UUID), a `provider` (`local` or `slurm`), an
  optional `context`, and optional provider `launch` settings. Namespaces must
  be unique, and so must each provider/`context` pair.
- An offer has a unique `name`, the `connection` it uses, per-node `resources`
  (`cpus`, `memory`, and optional `accelerators`), `max_nodes`, and `time` with a
  `default` no longer than its `max`. `startup` is optional (`fast`, `batch`, or the default
  `unknown`), written either as a bare class or as `{class: …, source: …}`.
  `config` holds provider-specific settings.

Catalog errors identify the invalid field, for example `offers.0.resources.cpus`.
Unknown common fields and duplicate YAML keys are rejected. CPU and node counts
must be positive integers. Compute memory follows SkyPilot's binary-unit
convention: `32`, `32GB`, and `32GiB` mean 32 GiB. Fractional quantities must
represent an exact number of bytes. An accelerator declaration names one type
and a positive whole count: `accelerators: A100:4` or `accelerators: {A100: 4}`;
`accelerators: A100` means one. Omit it for CPU-only offers. Durations use ordered
day/hour/minute/second units, such as
`30m`, `1h30m`, or `45s`.

Selection takes the first offer in catalog order that matches the request. An
offer this host cannot provide is skipped: a local offer with more nodes, CPUs or
memory than the host has, or whose `context` names another host. When nothing
matches, the error lists why each skipped offer was unavailable:

```text
Error: no configured offer matches this resource request; see lc compute resources; huge: the local offer exceeds this host's CPU or RAM capacity
```

## Configure Slurm

The CLI runs the native `sbatch`, `salloc`, `squeue`, `sacct`, `scontrol`, and
`scancel` commands as the current user. It needs a compatible Slurm client
installation and access to the selected service. `context` is the native Slurm
cluster name; omit it to use the current service.

Those commands run without inherited request settings: every `SBATCH_*`,
`SALLOC_*`, `SRUN_*`, `SQUEUE_*`, `SACCT_*`, `SCANCEL_*`, and `SLURM_*` variable
is removed, except `SLURM_CONF`, `SLURM_CONF_SERVER`, and `SLURM_JWT`. An
`SBATCH_ACCOUNT` in your shell profile therefore has no effect; put the account
in the offer.

This illustrative NERSC configuration requires a deployment-specific account and
resource sizing. It has not been validated by submitting a job at NERSC:

```yaml
version: 1
local:
  enabled: false
connections:
  perlmutter:
    namespace: 9d0c0fc5-9be8-407a-a3ec-f17c4110b162
    provider: slurm
    context: perlmutter

offers:
  - name: quick
    connection: perlmutter
    resources: {cpus: 256, memory: 480}
    max_nodes: 2
    time: {default: 1h, max: 4h}
    startup: {class: fast}
    config:
      submit: salloc
      account: myproject
      constraint: cpu
      qos: interactive
  - name: batch
    connection: perlmutter
    resources: {cpus: 256, memory: 480}
    max_nodes: 16
    time: {default: 1h, max: 12h}
    startup: {class: batch}
    config:
      submit: sbatch
      account: myproject
      constraint: cpu
      qos: regular
```

An offer's `config` accepts `submit` (`sbatch`, the default, or `salloc`),
`account`, `partition`, `qos`, `constraint`, `reservation`, and `gpu_type`.
For a named accelerator offer, `gpu_type` maps the public type to the site's
native Slurm GRES name; it is required even when the spellings happen to match.
A generic `GPU` offer may omit it. Slurm offers must state memory as a whole
number of MiB.

Every setting under a Slurm connection's `launch` mapping is optional. The
defaults assume a home directory that the login and compute nodes share:

- `python`: the interpreter that ran `lc compute launch`, so workers use the
  driver's own Lightcone installation. `uv tool install lightcone-cli` places it
  under `$HOME`. Avoid launching through `uvx`, whose environment lives in uv's
  cache and can be pruned while the allocation runs.
- `connection_root`: `~/.lightcone/compute`. The scheduler's connection files,
  TLS credentials, and batch logs live in private directories there, which the
  driver and every node must reach.
- `scratch_root`: each node's own temporary directory (`$TMPDIR`, usually
  `/tmp`), which holds the Dask workers' files.
- `cwd`: your home directory, as the job's working directory.
- `task_slots_per_node`: one fewer than the offer's CPUs, leaving room for the
  scheduler. Lower it when recipes are multithreaded or memory-heavy.
- `interface`: unset, so Dask listens on the node's hostname. Name a network
  interface instead if nodes cannot reach each other by hostname; Perlmutter's
  high-speed network is `hsn0`.

The offered CPU and memory shape is per node. Bare resource quantities request an
exact match; a trailing `+` permits a larger offered shape. Selection takes the
first eligible offer in catalog order. `--startup fast` filters to that service
class; it does not guarantee a queue wait. Inspect the resolved plan before launch:

```bash
lc compute launch --cpus 32+ --memory 128+ --num-nodes 2 --time 1h --dry-run
```

One allocation contains one `srun` step with one rank per node, bound with
`--cpu-bind=threads` to exactly the hardware threads Slurm allocated. Each rank
starts a standard Dask `Nanny`, which supervises a separate `Worker` process.
Rank zero also runs the scheduler, so a worker process exiting does not take the
scheduler with it. A one-node allocation has both scheduler and worker.
Neither serves a dashboard or any other HTTP route.
The scheduler consumes part of the offered resources; `task_slots_per_node`
controls Dask task concurrency independently of the allocation's logical CPUs.

Dask's Nanny defaults `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, and
`OPENBLAS_NUM_THREADS` to `1`, preventing each concurrent recipe from requesting
the whole node's numerical-library threads. Values explicitly set in the launch
environment take precedence and also reach containerized probes. A recipe's
own pools are sized to its declared CPUs instead (see
[Recipe resource requirements](#recipe-resource-requirements)); `task_slots_per_node` can further cap
concurrency. See
[Dask's Nanny environment settings](https://distributed.dask.org/en/stable/worker.html#nanny).

The Nanny restarts an exited worker, and Dask can reschedule its tasks. The step
uses `--kill-on-bad-exit=0 --wait=0` so an exited rank does not itself trigger
termination of the remaining ranks. This does not provide complete failure
isolation: site OOM policy can still kill the step or allocation, and the
scheduler and Nanny processes are not restarted if they die. Dask memory
management remains disabled because it does not account for the recipe
subprocesses' memory. Reduce task concurrency for memory-heavy recipes; there is
no per-recipe memory limit. A lost worker can also leave a recipe subprocess
running while Dask reschedules its task, so retries do not guarantee exclusive
access to output files. New commands still require every expected worker to be
connected. See [Dask's failure behavior](https://distributed.dask.org/en/stable/resilience.html)
and [Slurm's step termination settings](https://slurm.schedmd.com/srun.html).

Lightcone always requests a finite native `--time`. Actual termination follows Slurm's
`OverTimeLimit` and `KillWait` policy, which can permit an unlimited overrun.
Lightcone does not impose an independent Slurm runtime deadline or require a
preflight time-policy query.

`config.partition` is optional. Lightcone passes `--partition` only when it is
explicitly configured; otherwise the site selects the partition. Omit it at
NERSC so site routing can select from the QOS and constraint. During deployment
testing, inspect the submitted job's actual `Partition` with `scontrol show job`.
[NERSC's workflow guidance](https://docs.nersc.gov/jobs/workflow/maestro/)
describes its QOS-driven partition selection.

At NERSC, move uv's cache off `$HOME` before launching. Every recipe and probe
runs through `uv run`, which locks uv's cache, and Perlmutter's compute nodes
cannot lock files in `$HOME`, where the cache lives by default. Workers inherit
the environment `lc compute launch` runs in, so set the variable there, for
example in your shell profile:

```bash
export UV_CACHE_DIR=$PSCRATCH/uv-cache
```

An allocation launched without it has to be relaunched. `$PSCRATCH` is purged
when idle; a purged cache is only downloaded again.

Slurm displays `lc-v1-<name>` as the job name, for example `lc-v1-analysis`.
Its native comment carries the random submission token as
`lightcone:v1:kind=dask:token=<32hex>`. Lightcone verifies the name, token, and owner
before attaching or cancelling; a job name alone does not establish identity.
The opaque cluster ID encodes the connection namespace, native job ID, token,
and name. There is no job registry to reconcile. Removing an offer prevents new launches without
hiding existing jobs; retain its connection to inspect and terminate them.
Native job state and live Dask readiness are separate observations. A worker loss
can leave a job active but not ready. Unknown native state is reported as unknown.

Live discovery requires the native comment. Historical inspection also needs
Slurm accounting to retain it through `AccountingStoreFlags=job_comment`.
If accounting has no matching token, Lightcone reports unknown rather than
assuming the allocation ended or cancelling a job with a reused ID.
[Slurm documents this comment-retention setting](https://slurm.schedmd.com/sacct.html).

An `salloc` launch retains native `salloc`/`srun` processes on the submit host.
Its survival across logout, Jupyter shutdown, and site session cleanup must be
checked on the deployment. Batch jobs are independent of the submitting CLI.
An ambiguous submission reports its token; inspect native state before retrying,
since the original allocation may have been accepted.

A batch launch submits with `sbatch --parsable --no-requeue`. An `salloc` launch
starts `salloc --kill-command=TERM` detached and returns once Slurm lists the job,
waiting at most ten seconds. Everything lives under the connection root:

- Submission logs: `submissions/<token>/<job-id>.out` for `sbatch`, or
  `submissions/<token>/salloc.log`.
- Scheduler connection files and TLS credentials:
  `<namespace>/<job-id>-<token>/attempt-<restarts>/`.

Each worker's files go under `<scratch>/<token>/attempt-<restarts>/<rank>`.

Before starting Dask, every rank checks that Slurm gave it what the plan
requested: the node count, CPUs per task and its actual CPU affinity, and memory
per node. GPU allocations also validate Slurm's native GPU count.
Ranks other than zero wait up to 120 seconds for the scheduler, which
has as long to start. A failed check or timeout logs
`Slurm Dask startup failed: …` to the submission log and exits nonzero. Look
there when a job is active but never becomes ready.

## GPU allocations

GPU support uses NVIDIA CUDA devices on Linux. `lc compute resources` shows
configured accelerator types and counts; Lightcone does not probe CUDA or discover
local hardware. There is no fractional GPU or MIG management.

Requests use SkyPilot-style `NAME[:COUNT]`: `--gpus A100` means one A100,
`--gpus A100:4` means exactly four, and `--gpus GPU:4` accepts any configured model
with exactly four. Names are case-insensitive catalog labels; GPU counts do not
accept `+`. Omitting `--gpus`, or passing `0`, selects CPU-only offers.

For local GPUs, add an offer to the [workstation catalog above](#customize-resource-offers):

```yaml
  - name: workstation-gpu
    connection: workstation
    resources: {cpus: 4, memory: 8GB, accelerators: 'GPU:1'}
    max_nodes: 1
    time: {default: 30m, max: 2h}
    startup: fast
```

Set the devices available to that allocation when launching it:

```bash
CUDA_VISIBLE_DEVICES=0 lc compute launch --cpus 4 --memory 8GB --gpus GPU:1
```

Lightcone freezes the nonempty mask and `CUDA_DEVICE_ORDER`, if set, at launch.
You are responsible for matching the catalog's count and model to those devices;
Lightcone does not verify them. Local allocations do not reserve GPUs exclusively
against other allocations or programs on the host. Before launch, the host must
have loaded the NVIDIA driver and created its character devices, including UVM.
Lightcone grants existing device nodes and does not initialize them; see
[NVIDIA's device setup utility](https://github.com/NVIDIA/nvidia-modprobe/blob/main/nvidia-modprobe.1.m4).

On Slurm, the offer's `config.gpu_type` maps its catalog label to a native GRES
type. For example, add this offer with settings adjusted to your site:

```yaml
- name: gpu-batch
  connection: perlmutter
  resources: {cpus: 32, memory: 128GB, accelerators: 'A100:4'}
  max_nodes: 2
  time: {default: 1h, max: 4h}
  startup: batch
  config:
    account: myproject
    constraint: gpu
    gpu_type: a100
```

```bash
lc compute launch --cpus 32 --memory 128GB --gpus A100:4 --dry-run
```

This is an illustrative shape, not a tested site configuration. Slurm allocates
GPUs through GRES; Lightcone checks the native count and passes Slurm's CUDA mask
through unchanged, using `CUDA_DEVICE_ORDER=PCI_BUS_ID`.

GPU commands inherit the allocation's whole CUDA mask. CPU commands receive an
empty mask. These are cooperative visibility settings; native OS and cgroup
permissions remain authoritative.

Containerized GPU execution currently supports **podman-hpc** through its native
`--gpu` option. See [NERSC's GPU container guidance](https://docs.nersc.gov/development/containers/podman-hpc/overview/#using-nvidia-gpus-in-podman-hpc).
Recipes explicitly requesting GPUs with ordinary Docker or Podman are refused
before image preparation. `lc run` probes on those runtimes remain usable on a
GPU cluster: they run without GPUs and report that limitation. CPU execution
supports all three runtimes and sets `NVIDIA_VISIBLE_DEVICES=void` to override
GPU-enabled image defaults. Physical GPU execution remains a deployment
validation step.

A standalone GPU rerun needs a device mask in its own environment, for example
`CUDA_VISIBLE_DEVICES=0 datalad rerun`. It does not inherit an old allocation's
mask or reserve devices through Dask.

## Recipe resource requirements

Declare each recipe's needs in `astra.yaml`:

```yaml
recipe:
  command: python src/fit.py {output}
  resources:
    cpus: 4
    memory: 8Gi
    gpus: 1
```

Each recipe runs on one worker. Its CPU, memory, and GPU request must fit that
worker, even when the cluster has several nodes. Dask reserves CPU and memory
while the task runs, so recipes can run together only when their combined
requests fit. `task_slots_per_node` also caps concurrent tasks; it does not
limit how many CPUs a single recipe may request.

CPUs must be positive whole numbers and default to one. Memory needs units:
`512Mi` and `8Gi` are binary sizes; `8GB` is decimal, unlike compute memory.
Bare quantities are not accepted. Without a memory declaration, no RAM is
reserved: CPU requests and `task_slots_per_node` control concurrency.

Recipe `gpus` is a nonnegative whole count, defaulting to zero; accelerator type
selection belongs to cluster allocation. A GPU recipe reserves the worker's
entire GPU budget, so only one GPU recipe runs on that worker at a time. The
requested count is a minimum capacity requirement: the command inherits the
worker's whole allocated CUDA mask and may see more GPUs than requested. CPU
recipes may still run alongside it when CPU, memory, and task slots permit; their
CUDA mask is empty. `lc run` reserves the worker's entire CPU, memory, and GPU
budgets; direct and podman-hpc probes inherit that allocation mask.

Recipe `time_limit` is not supported and is refused before preparation or
execution. Set the allocation lifetime with `lc compute launch --time` instead.
Fractional CPU/GPU counts, GPU model requests inside a recipe, and disk requests
are also rejected rather than ignored.

`lc materialize` first classifies the selected graph, then checks resources for
outputs that may rebuild before preparation or submission. Already-current or
unrefreshed behind outputs reserve nothing. Dependents of an output that may
change still need resources; the worker may later skip them if the actual
upstream digest is unchanged. Use `lc materialize --check` to inspect currency
without allocation. Read-only `status` and `--check` accept valid ASTRA resource
declarations even when this executor cannot satisfy them.

These are scheduling reservations, not per-recipe CPU or RAM enforcement.
Recipes must respect their declarations; a subprocess can otherwise exceed
its request. Slurm enforces the overall allocation, while local execution
uses cooperative budgets. Leave capacity for the scheduler, workers, and other
overhead when declaring recipe requirements.

A recipe's declared CPUs also size its numerical-library thread pools: the
recipe runs with `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, and
`NUMBA_NUM_THREADS` set to its `cpus` (1 when undeclared), in direct and
containerized mode alike, so it uses the cores reserved for it and no more. Set
a variable in the recipe command to choose another value. `lc run` probes have
no declaration and keep the worker's values: Dask's Nanny defaults of `1` for
the first three, or values set before `lc compute launch`. See
[Dask's defaults](https://docs.dask.org/en/stable/configuration.html#distributed.nanny.pre-spawn-environ.OMP_NUM_THREADS).

## Execution requirements and limits

Driver and workers must see the same project, prepared environment, and inputs
at the same absolute paths. They need matching Lightcone code, Python major/minor,
and Dask versions. By default, Slurm workers run the driver's own installation
(see the `python` launch setting above). Relaunch allocations after upgrading
the worker installation.
Commands and recipes are ordinary tasks submitted to the Dask
scheduler, which chooses their workers; task runtime and sandbox checks still
apply. Recipe output is forwarded to the invoking terminal on stderr; `run`
preserves the command's stdout and stderr bytes separately.
Containerized projects also require the prepared image and runtime on each
worker; `podman-hpc` can expose its migrated image across NERSC nodes.
Direct recipes inherit the allocation workers' environment, not variables added
to the invoking CLI after launch. Remote execution does not forward stdin.

The catalog contains policy, not credentials or live state. Scheduler connection
material is private and uses standard Dask TLS and scheduler files. Configured
connection and scratch roots can contain symlinks, including a symlinked home
directory: Lightcone resolves the root before appending managed paths. Allocation
directories and credential files still reject symlinks, retain ownership and
ancestor-permission checks, and require modes `0700` and `0600`, respectively.
The catalog's location is independent of the private connection files.

Use one execution invocation per project at a time. Concurrent writers,
comprehensive cancellation, task fencing, and recovery after client/worker loss
are not guaranteed. A lost client does not prove its subprocesses stopped.
Unreported partial outputs are retained after interruption rather than restored
while a task may still write them. End the allocation and establish that work has
stopped before inspecting or repairing that project's outputs.
For local containerized execution, `down` and walltime expiry stop the managed
process group but do not guarantee termination of containers managed by an
external runtime. A Podman container that ignores SIGTERM can survive. Inspect
and stop such containers through the container runtime before cleaning results.
