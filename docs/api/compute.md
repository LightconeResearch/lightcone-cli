# lightcone.engine.compute

The allocation boundary shared by CLI lifecycle operations and execution.
`Compute` reads the canonical catalog and obtains fresh native observations.
It owns no service, registry, or saved current-cluster selection.

| Symbol | Contract |
|---|---|
| `Request.parse(...)` | Common exact/minimum CPU and memory requests, node count, walltime, startup class. |
| `Catalog.load(path)` | Ordered fixed shapes and stable connection namespaces, separate from running allocations. |
| `Compute.plan(request)` | Select an eligible offer and freeze its native launch settings without allocation. |
| `Compute.launch(plan)` | Submit once and return a self-contained `Identity`. |
| `Compute.discover()` | Snapshots and per-connection errors, querying each authority once. |
| `Compute.status(id, wait=False, timeout=300)` | Native allocation state plus authenticated Dask readiness. |
| `Compute.down(id)` | Native termination independent of scheduler health. |
| `connect(id, timeout=10, config_path=None)` | Context manager borrowing a standard Dask client; closes the client, never the allocation. |
| `Provider` | `plan`, `launch`, `discover`, `inspect`, `connect`, `terminate`. |

`local.py` and `slurm.py` implement the provider protocol. Adding an adapter means
adding one provider factory and its native mapping; `run` and `materialize` only
borrow clients through the common API. Provider settings stay behind that seam.
`runtime.py` owns private files, standard TLS material, and authenticated scheduler
identity checks. `local_runtime.py` and `slurm_bootstrap.py` compose stock Dask
components; they do not define custom workers or membership protocols.

`Snapshot` distinguishes native allocation evidence from scheduler observations.
No live allocation size is filled from today's catalog. Connection namespaces
persist independently of offers, and IDs encode native incarnation evidence
without a UUID-to-job lookup database. Exceptions retain known cluster IDs and
submission tokens for partial/ambiguous acceptance.

`execution.py` validates worker placement, code/version compatibility, prepared
runtime and shared project storage, then pins invocation-unique tasks to the
validated workers. Cancellation and concurrent project writers are not made safe
by allocation management; callers must respect the documented execution limits.

Tests cover deterministic selection, malformed identities and catalogs, partial
native failures, acceptance ambiguity, PID reuse, detached local lifetime, standard
Dask bootstrap, and explicit execution through borrowed clients. Slurm command
contracts are simulated; a real NERSC submission remains a deployment check.
