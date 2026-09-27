# Serverless Compute Allocation Management for `lightcone-cli`

**Status:** proposed specification\
**Date:** 2026-09-27\
**Target:** `LightconeResearch/lightcone-cli` main at `3aa823b46ec016a1b7a52515f4c0ee6eb35d3b8d`

## 1. Purpose

Extend `lc` with a serverless compute-management layer for reusable Dask clusters.

The first implementation MUST support three providers:

1. **local** — a Dask scheduler/workers running on the current machine;
2. **slurm** — a persistent Dask cluster running inside a Slurm allocation;
3. **gateway** — a Dask cluster managed by Dask Gateway.

The design MUST NOT require a persistent Lightcone daemon or control-plane service. A JupyterLab frontend SHOULD be able to request compute using services already supplied by the deployment (for example `jupyterlab-slurm` or `dask-labextension`) without requiring a Lightcone Jupyter server extension.

This proposal is about compute allocation and discovery. Remote execution from a laptop, code synchronization, and remote project materialization are explicitly out of scope.

---

## 2. Design principles

### 2.1 Provider owns reality

Lightcone MUST NOT treat cached filesystem state as authoritative for whether compute is alive.

Each provider supplies the source of truth:

| Provider | Source of truth |
|---|---|
| `slurm` | Slurm (`squeue`, then `sacct` where needed) |
| `gateway` | Dask Gateway API |
| `local` | local process/scheduler liveness |

Filesystem state is registration and connection material only.

### 2.2 Reconcile on demand

There is no background manager.

`lc compute list`, `lc compute status`, `lc compute wait`, `lc materialize`, and `lc compute stop` refresh provider state when invoked. A process that needs to wait polls synchronously.

A resource may terminate while no Lightcone process is running. Nothing must notice at the instant this happens; the next command reconciles the state.

### 2.3 Persistent compute is separate from execution

A **provider** creates and manages a compute allocation. A ready allocation exposes a **Dask execution endpoint**.

Conceptually:

```text
ComputeProvider
    create(spec) -> AllocationRef
    discover() -> list[AllocationRef]
    inspect(ref) -> AllocationStatus
    connect(ref) -> Dask scheduler/client
    stop(ref)
```

`wait(ref)` is generic polling over `inspect(ref)` and does not require a provider-specific daemon.

The existing materialization scheduler seam remains narrow: execution still ultimately needs `submit(...)` and `completed(...)`. Compute management sits outside that seam.

---

## 3. Allocation identity and normalized state

Every allocation has a stable Lightcone reference:

```text
local:<uuid>
slurm:<job-id>
gateway:<cluster-name>
```

Provider-native IDs SHOULD be used directly where they are already stable.

Normalized states:

```text
pending   requested but not yet usable
starting  provider says active, but Dask endpoint is not yet connectable
ready     Dask endpoint is connectable
stopping  termination requested
stopped   terminated normally
failed    provider reports failure/cancellation/timeout
unknown   provider cannot currently determine state
```

Provider-specific detail MUST remain available in structured output, for example:

```json
{
  "ref": "slurm:12345",
  "provider": "slurm",
  "state": "failed",
  "provider_state": "TIMEOUT"
}
```

A cached `"state"` field MUST NOT be used as the current state.

---

## 4. Filesystem conventions

Private Lightcone compute data lives under:

```text
~/.lightcone/compute/
```

Recommended layout:

```text
~/.lightcone/compute/
  local/<uuid>/
    metadata.json
    scheduler.json
    pids.json
    logs/
  slurm/<job-id>/
    metadata.json
    scheduler.json
    tls/
  gateway/
    # optional cache only; no registry is required
```

Directories containing credentials MUST be mode `0700`; credential files MUST be mode `0600`.

Writes SHOULD be atomic (`tmp` + rename).

Registration directories MAY be removed on clean shutdown, but correctness MUST NOT depend on cleanup. Stale directories are ignored or garbage-collected after provider reconciliation.

### `metadata.json`

When present:

```json
{
  "version": 1,
  "provider": "slurm",
  "provider_id": "12345",
  "scheduler_file": "scheduler.json",
  "created_at": "2026-09-27T19:00:00Z"
}
```

This file identifies connection material; it does not certify liveness.

---

## 5. Provider: local Dask

### Creation

```bash
lc compute start --provider local --workers 8
```

starts a scheduler and workers that remain alive after the command returns.

The scheduler MUST bind to loopback by default.

The provider writes:

```text
~/.lightcone/compute/local/<uuid>/scheduler.json
~/.lightcone/compute/local/<uuid>/pids.json
```

### Discovery and state

`lc compute list` discovers Lightcone-created local registrations and validates them.

A local allocation is:

- `ready` if its scheduler is reachable;
- `failed`/`stopped` if the recorded processes are gone and the scheduler is unreachable;
- `unknown` if validation cannot be completed.

Stale registrations MAY be removed opportunistically.

### Stop

`lc compute stop local:<uuid>` terminates the recorded local scheduler/workers and removes the registration.

No TLS is required for a scheduler bound strictly to loopback. Binding a local Lightcone scheduler to a non-loopback address is out of scope for the first implementation unless TLS is enabled.

---

## 6. Provider: Slurm

### 6.1 Allocation discovery

Lightcone Slurm jobs MUST carry a machine-readable marker. Preferred convention:

```bash
#SBATCH --comment=lightcone:v1:kind=dask
```

Optional fields may be appended, for example:

```text
lightcone:v1:kind=dask:project=<project-id>
```

If a deployment does not preserve or expose comments, a reserved job-name prefix such as `lc-dask-` MAY be used as a fallback.

`lc compute list` queries active jobs using Slurm and filters for the Lightcone marker. It MUST NOT infer liveness from `~/.lightcone/compute`.

### 6.2 Submission

CLI submission:

```bash
lc compute start --provider slurm \
  --nodes 4 \
  --time 02:00:00 \
  --account <account> \
  --qos <qos>
```

renders and submits an `sbatch` script whose payload invokes a job-side Lightcone helper, conceptually:

```bash
lc compute serve
```

Exact command spelling is not normative.

The job-side helper:

1. obtains `$SLURM_JOB_ID`;
2. creates `~/.lightcone/compute/slurm/$SLURM_JOB_ID/`;
3. generates Dask TLS credentials;
4. starts a Dask scheduler;
5. writes `scheduler.json`;
6. launches workers across the allocation;
7. attempts cleanup on exit.

### 6.3 State reconciliation

For an allocation `slurm:<job-id>`:

- Slurm `PENDING` -> `pending`;
- Slurm running, scheduler registration absent/unreachable -> `starting`;
- Slurm running and scheduler connectable -> `ready`;
- Slurm terminal success -> `stopped`;
- Slurm timeout/cancel/failure/node failure -> `failed`.

For active discovery, `squeue` is sufficient. For a known allocation that has disappeared from `squeue`, `sacct` SHOULD be queried to distinguish normal completion, cancellation, timeout, and failure.

### 6.4 Security

The Dask scheduler MUST use TLS on a shared HPC network.

TLS credentials remain private under the registration directory. The browser MUST NOT receive them.

A scheduler file existing on disk is never sufficient proof that the cluster is alive.

---

## 7. Provider: Dask Gateway

Dask Gateway already supplies the control plane. Lightcone MUST use the official `dask_gateway` client rather than reproduce Gateway lifecycle logic.

### Creation

```bash
lc compute start --provider gateway [provider-specific options]
```

uses:

```python
gateway = Gateway()
cluster = gateway.new_cluster(..., shutdown_on_close=False)
```

`shutdown_on_close=False` is required for a managed allocation intended to outlive the requesting CLI process.

The returned cluster name becomes the Lightcone reference:

```text
gateway:<cluster-name>
```

### Discovery

`Gateway.list_clusters()` is authoritative. Lightcone SHOULD expose all clusters owned by the authenticated user, not require a Lightcone filesystem registration.

A future deployment-specific tag/filter may distinguish Lightcone-created Gateway clusters, but this is not required for v1.

### Connect

For an existing cluster:

```python
cluster = gateway.connect(cluster_name, shutdown_on_close=False)
client = cluster.get_client()
```

Gateway remains responsible for scheduler routing, authentication, and TLS.

### Stop

`lc compute stop gateway:<cluster-name>` connects to the Gateway allocation and shuts it down through Gateway.

### Configuration

Lightcone SHOULD honor normal Dask Gateway client configuration (`Gateway()` with no hard-coded deployment URL) so site configuration remains outside Lightcone.

---

## 8. Proposed CLI surface

Minimum v1:

```text
lc compute start --provider <local|slurm|gateway> [options]
lc compute list [--json]
lc compute status <ref> [--json]
lc compute wait <ref>
lc compute stop <ref>
```

Useful structured list fields:

```json
{
  "ref": "slurm:12345",
  "provider": "slurm",
  "state": "ready",
  "provider_state": "RUNNING",
  "dashboard": "...",
  "created_at": "...",
  "resources": {}
}
```

Provider-specific options MAY remain provider-specific rather than forcing a false common schema. Common concepts such as workers, cores, memory, walltime, GPUs, account, QOS, and Gateway option names can be normalized later.

---

## 9. Integration with `lc materialize`

Current `main` selects an execution venue inside `materialize.cluster_for_run()` and exposes a deliberately small scheduler interface.

The change SHOULD preserve that execution seam while separating allocation selection from scheduler construction.

Proposed behavior:

```bash
lc materialize --compute slurm:12345
lc materialize --compute gateway:abc123
lc materialize --compute local:de305d54-...
```

Before use:

1. resolve the provider from the reference;
2. reconcile current provider state;
3. refuse anything not `ready`;
4. establish a Dask client;
5. adapt that client to the existing Lightcone scheduler seam.

Without `--compute`, v1 SHOULD preserve existing behavior to avoid a surprising semantic change:

- inside an existing Slurm allocation: use that allocation as today;
- otherwise: use the current local execution behavior.

Automatic selection of an already-running managed allocation can be added later via an explicit project/user default.

---

## 10. JupyterLab compatibility

### 10.1 Principle

A browser UI MUST NOT need to communicate directly with the `lc` process or write Lightcone's private registry.

The provider and the resource itself are the rendezvous.

The Lightcone JupyterLab extension SHOULD detect and opportunistically use existing site services. No Lightcone Jupyter server extension is required for these paths.

### 10.2 Slurm via `jupyterlab-slurm`

On NERSC, the existing `jupyterlab-slurm` server extension exposes authenticated routes for `sbatch`, `squeue`, job inspection, and cancellation.

Browser flow:

```text
Lightcone JupyterLab UI
        |
        | submit Lightcone-marked sbatch job
        v
      Slurm
        |
        | job starts and runs `lc compute serve`
        v
~/.lightcone/compute/slurm/<job-id>/
        ^
        |
        | independently rediscovered via Slurm
        |
      lc CLI
```

The browser never writes the private registration. The running Slurm job self-registers.

**Current NERSC caveat:** `jupyterlab-slurm`'s `sbatch` route accepts a script path, not script text. A browser-only implementation therefore needs either:

1. the standard Jupyter Contents API to write a temporary submission script under the server root, then call `sbatch`; or
2. a future `jupyterlab-slurm` API that accepts script contents.

This does not require access to `~/.lightcone`.

The browser may poll the existing Slurm routes for UX, but that polling is not required for CLI correctness.

### 10.3 Gateway from JupyterLab

There are two distinct reusable capabilities.

#### A. Existing `dask-labextension` server API

Current `dask-labextension` includes a Jupyter server extension with authenticated same-origin routes under:

```text
/dask/clusters
/dask/clusters/<id>
```

It can create, list, scale, and delete cluster objects supplied by a configurable Dask cluster factory.

A Lightcone browser extension MAY use these existing routes when present, with no Lightcone server-side code.

Using `dask_gateway.GatewayCluster` as that factory appears compatible with the cluster interface expected by `dask-labextension` (`asynchronous=True`, `scale`, `adapt`, `close`), but this combination SHOULD be integration-tested before being a documented guarantee.

Regardless of how the browser creates the cluster, the CLI SHOULD rediscover it through the native Gateway API (`Gateway.list_clusters()`), not through the Jupyter server's in-memory cluster manager.

#### B. Direct browser -> Dask Gateway HTTP API

Dask Gateway itself exposes authenticated HTTP endpoints including:

```text
GET    /api/v1/clusters/
POST   /api/v1/clusters/
GET    /api/v1/clusters/<name>
DELETE /api/v1/clusters/<name>
POST   /api/v1/clusters/<name>/scale
POST   /api/v1/clusters/<name>/adapt
```

However, direct browser access MUST be treated as an optional deployment capability, not the v1 baseline.

With standard JupyterHub authentication, the official Dask Gateway client reads `JUPYTERHUB_API_TOKEN` from the **server process environment** and sends it in the `Authorization` header. A frontend-only extension should not expect to possess that token.

Therefore:

- if a deployment provides a browser-usable authenticated Gateway endpoint, Lightcone MAY call it directly;
- otherwise Lightcone SHOULD use an already-installed same-origin Jupyter service such as `dask-labextension`;
- if neither exists, Gateway allocation remains available from the CLI but not from the browser.

This preserves the browser-only Lightcone package without inventing a Lightcone proxy service.

---

## 11. Failure handling

### Allocation ends while idle

No action is required. The next `lc compute ...` command reconciles with the provider.

### Allocation ends during materialization

A Dask communication failure SHOULD trigger provider inspection.

Where possible, report the provider cause rather than only a generic Dask error, e.g.:

```text
Compute allocation slurm:12345 ended during materialization.
Slurm state: TIMEOUT
```

### Stale registration

A stale scheduler file is ignored after provider reconciliation and MAY be deleted opportunistically.

### Provider unavailable

If Slurm/Gateway cannot be queried, state is `unknown`. Lightcone MUST NOT promote cached state to `ready`.

---

## 12. Implementation shape

Suggested modules:

```text
src/lightcone/compute/
    model.py
    provider.py
    local.py
    slurm.py
    gateway.py
    registry.py
```

Core interface:

```python
class ComputeProvider(Protocol):
    def create(self, spec: ComputeSpec) -> AllocationRef: ...
    def discover(self) -> Sequence[AllocationRef]: ...
    def inspect(self, ref: AllocationRef) -> AllocationStatus: ...
    def connect(self, ref: AllocationRef) -> Scheduler: ...
    def stop(self, ref: AllocationRef) -> None: ...
```

`registry.py` only manages local connection/registration material. It MUST NOT become an independent lifecycle database.

Provider dependencies SHOULD be optional where practical (`dask-gateway` for Gateway support).

---

## 13. Suggested delivery order

### Phase 1 — abstraction + local

- allocation model and references;
- `lc compute start/list/status/wait/stop`;
- persistent local Dask provider;
- scheduler adapter into `lc materialize --compute`.

### Phase 2 — Slurm

- marked `sbatch` submission;
- `squeue`/`sacct` reconciliation;
- job-side Dask bootstrap;
- self-registration and TLS;
- failure translation;
- NERSC integration tests.

### Phase 3 — Gateway

- official `dask_gateway` client provider;
- cluster options passthrough;
- discover/connect/stop;
- persistence with `shutdown_on_close=False`.

### Phase 4 — JupyterLab

- capability detection;
- `jupyterlab-slurm` submission path;
- `dask-labextension` cluster-management path where configured;
- direct Gateway API only when deployment authentication explicitly supports browser clients.

---

## 14. Validation checklist

### Slurm / NERSC

- [ ] `#SBATCH --comment` marker survives and is visible in the chosen Slurm query.
- [ ] login-node `lc` can reach the scheduler interface selected by the job.
- [ ] TLS scheduler/client setup works end-to-end.
- [ ] job timeout/cancel/failure maps to useful Lightcone states.
- [ ] stale registration does not appear `ready`.
- [ ] JupyterLab submission can be performed using Contents API + `jupyterlab-slurm`.

### Dask Gateway

- [ ] `Gateway.list_clusters()` sees a cluster created from the Jupyter environment.
- [ ] `Gateway.connect(name)` reconnects from a separate CLI process.
- [ ] `shutdown_on_close=False` prevents accidental teardown.
- [ ] site-specific cluster options can be passed through.
- [ ] test whether `dask-labextension` can use `GatewayCluster` as its configured factory on the target deployment.
- [ ] determine whether the target JupyterHub exposes any browser-safe direct Gateway authentication; do not assume it.

### Local

- [ ] scheduler survives the initiating `lc compute start` process.
- [ ] dead process/scheduler is reconciled correctly.
- [ ] stale local registrations are harmless.
- [ ] scheduler is loopback-only by default.

---

## 15. Non-goals for v1

- no Lightcone daemon;
- no warm-pool/autoscaling policy owned by Lightcone;
- no notifications when queued compute becomes ready;
- no laptop-to-HPC remote execution or code synchronization;
- no cross-user/shared allocation coordination;
- no requirement that all providers expose identical resource-option schemas.

A daemon/control plane can be introduced later if Lightcone needs autonomous policy such as maintaining warm capacity, renewing leases, retrying allocations, or scaling without an active CLI process. The provider interface above should remain usable if that happens.

---

## 16. Verified implementation notes and references

Research performed 2026-09-27.

- `lightcone-cli` current main: commit `3aa823b46ec016a1b7a52515f4c0ee6eb35d3b8d`.
  - Current materialization venue selection is centralized in `materialize.cluster_for_run()`.
  - The scheduler seam is intentionally limited to submission/completion.
  - https://github.com/LightconeResearch/lightcone-cli

- NERSC/Jupyter investigation supplied with this proposal:
  - browser-only Lightcone extension goal;
  - `jupyterlab-slurm` authenticated routes;
  - self-registration under `~/.lightcone/compute`;
  - Slurm as liveness authority;
  - TLS requirement for Dask on the shared HPC network.

- Dask Gateway current documentation:
  - `Gateway.list_clusters()`, `new_cluster()`, `connect()`, and persistent clusters using `shutdown_on_close=False`;
  - authenticated cluster lifecycle managed by Gateway;
  - https://gateway.dask.org/usage.html

- Dask Gateway server routes currently expose `/api/v1/clusters/...` for list/create/get/delete/scale/adapt:
  - https://github.com/dask/dask-gateway/blob/main/dask-gateway-server/dask_gateway_server/routes.py

- Standard Dask Gateway JupyterHub client authentication obtains `JUPYTERHUB_API_TOKEN` from the process environment:
  - https://github.com/dask/dask-gateway/blob/main/dask-gateway/dask_gateway/auth.py
  - https://gateway.dask.org/authentication.html

- `dask-labextension` currently includes a Jupyter server extension with authenticated `/dask/clusters` routes and a configurable cluster factory:
  - https://github.com/dask/dask-labextension
  - https://github.com/dask/dask-labextension/blob/main/dask_labextension/clusterhandler.py
  - https://github.com/dask/dask-labextension/blob/main/dask_labextension/manager.py
