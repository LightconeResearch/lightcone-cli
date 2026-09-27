# Running on a Cluster

Allocate compute explicitly, then pass the returned cluster ID to either execution
command. The same commands work for a local workstation and Slurm. No cluster is
started by `lc run` or `lc materialize`, even when Slurm environment variables are
present. `lc materialize --check` and `lc status` remain local project inspection.

## Start locally

Create `~/lightcone-compute.yaml` with a fixed resource offer. The namespace is a
stable UUID identifying this connection; keep it unchanged while its clusters exist.
The following small offer uses one logical CPU and 1 GiB on your workstation:

```yaml
version: 1
connections:
  workstation:
    namespace: 22c84e48-2f0a-4cd2-90a2-30ce2e909bd1
    provider: local
offers:
  - name: small
    connection: workstation
    resources: {cpus: 1, memory: 1}
    max_nodes: 1
    time: {default: 30m, max: 2h}
    startup: {class: fast}
```

For a different catalog location, set `LC_COMPUTE_CONFIG` for all commands. The
compute group's `--config PATH` overrides it for that invocation only.

```bash
lc compute resources
lc compute launch --cpus 1 --memory 1 --dry-run
CLUSTER=$(lc compute launch --cpus 1 --memory 1 --json | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
lc compute status "$CLUSTER" --wait
lc run "$CLUSTER" -- python -c 'print("hello from the cluster")'
lc materialize "$CLUSTER"
lc compute down "$CLUSTER"
```

Run the execution commands from your project root. A launch returns when native
allocation is accepted; `status --wait` waits for Dask readiness. Finishing a run
detaches its client and leaves the cluster available for another command. The
allocation ends at its time limit or when you call `down`.

Local resources are cooperative limits, not an exclusive CPU/RAM reservation.
An allocation owns a detached process session and standard `LocalCluster`.
Private process locators are checked against the current host, boot, UID, PID
birth time, session, and command before attachment or termination. Local compute
is refused on recognized login nodes. Worker placement is also checked before
executing a command or recipe.

## Configure Slurm

The CLI runs the native `sbatch`, `salloc`, `squeue`, `sacct`, `scontrol`, and
`scancel` commands as the current user. It needs a compatible Slurm client
installation and access to the selected service. `context` is the native Slurm
cluster name; omit it to use the current service.

This illustrative NERSC configuration requires deployment-specific paths, account,
and resource sizing. It has not been validated by submitting a job at NERSC:

```yaml
version: 1
connections:
  perlmutter:
    namespace: 9d0c0fc5-9be8-407a-a3ec-f17c4110b162
    provider: slurm
    context: perlmutter
    launch:
      python: /shared/tools/lightcone/bin/python
      connection_root: /shared/home/alice/.lightcone/compute
      scratch_root: /shared/scratch/alice/lightcone
      task_slots_per_node: 126
      cpu_bind: threads
      # interface: hsn0

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

The offered CPU and memory shape is per node. Bare resource quantities request an
exact match; a trailing `+` permits a larger offered shape. Selection takes the
first eligible offer in catalog order. `--startup fast` filters to that service
class; it does not guarantee a queue wait. Inspect the resolved plan before launch:

```bash
lc compute launch --cpus 32+ --memory 128+ --num-nodes 2 --time 1h --dry-run
```

One allocation contains one `srun` step with one process per node. Rank zero
composes standard Dask `Scheduler` and `Worker` objects, and every other rank
starts a standard `Worker`. A one-node allocation has both scheduler and worker.
The scheduler consumes part of the offered resources; `task_slots_per_node`
controls Dask task concurrency independently of the allocation's logical CPUs.
Dask memory management is disabled because recipes run in external subprocesses;
Slurm supplies allocation containment and memory enforcement. Planning reads
the effective partition overrun policy and termination grace, rejects an unlimited
overrun, and freezes the chosen partition. Native administrators can still change
policy after submission.

Jobs carry a random submission token in their `lc-dask-v1-…` name. The opaque
cluster ID encodes the connection namespace, native job ID, and token. There is
no job registry to reconcile. Removing an offer prevents new launches without
hiding existing jobs; retain its connection to inspect and terminate them.
Native job state and live Dask readiness are separate observations. A worker loss
can leave a job active but not ready. Unknown native state is reported as unknown.

An `salloc` launch retains native `salloc`/`srun` processes on the submit host.
Its survival across logout, Jupyter shutdown, and site session cleanup must be
checked on the deployment. Batch jobs are independent of the submitting CLI.
An ambiguous submission reports its token; inspect native state before retrying,
since the original allocation may have been accepted.

## Execution requirements and limits

Driver and workers must see the same project, prepared environment, and inputs
at the same absolute paths. They need matching Lightcone code, Python major/minor,
and Dask versions. Execution verifies shared storage and worker compatibility.
Containerized projects also require the prepared image and runtime on each
worker; `podman-hpc` can expose its migrated image across NERSC nodes.

The catalog contains policy, not credentials or live state. Scheduler connection
material is private and uses standard Dask TLS and scheduler files. Keep the
catalog in a visible, readable location if it is to be read by a browser frontend;
its location is independent of private connection files.

Use one execution invocation per project at a time. Concurrent writers,
comprehensive cancellation, task fencing, and recovery after client/worker loss
are not guaranteed. A lost client does not prove its subprocesses stopped.
Unreported partial outputs are retained after interruption rather than restored
while a task may still write them. End the allocation and establish that work has
stopped before inspecting or repairing that project's outputs.
