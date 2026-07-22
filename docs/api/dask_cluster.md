# lightcone.engine.dask_cluster

Cluster lifecycle for `lc run`. One context manager (`cluster_for_run`),
four branches, no service to manage.

Source: `src/lightcone/engine/dask_cluster.py`.

## `cluster_for_run(*, verbose=False, worker_image=None, max_workers=None) → Iterator[dict[str, str]]`

Yields the env overlay the child snakemake needs to reach the cluster
(the executor plugin lives in a different process, so connection info
travels via environment variables). Four branches in priority order:

1. **`DASK_SCHEDULER_ADDRESS` already set** → yield it as-is. We don't
   own the cluster, so we don't tear it down.
2. **`DASK_GATEWAY__ADDRESS` set** (a JupyterHub deployment) →
   **create** a run-scoped Dask Gateway cluster with `worker_image` as
   its `image` cluster option, scale it adaptively `1..max_workers`,
   wait (bounded by `LIGHTCONE_GATEWAY_WORKER_TIMEOUT`, default 600 s)
   for the first worker, and shut the cluster down on exit — success
   or failure. Yields `{LIGHTCONE_GATEWAY_CLUSTER: <name>}`: Gateway
   schedulers speak a `gateway://` comm scheme a bare `Client` cannot
   dial, so the executor rejoins by name through the Gateway API.
3. **`SLURM_JOB_ID` set** → start an in-process scheduler bound to the
   driver's SLURM hostname (`SLURMD_NODENAME` or `gethostname()`),
   then `srun` one `dask worker` per node across the allocation.
4. **None of the above** → `LocalCluster()` sized to the local machine.

Outside the Gateway branch the scheduler is always in-process, so its
lifetime equals the run's lifetime: no orphaned schedulers if the
driver crashes. On the Gateway branch the same contract is enforced
server-side — create per run, cull on exit (the deployment's idle
timeout is the backstop). Create-per-run is also what makes image
updates seamless: a Gateway cluster's image is fixed at creation.

The Gateway branch fails fast on two silent-hang failure modes: zero
workers within the timeout (unpullable image, unschedulable pool), and
workers that don't advertise the `cpus`/`memory` resource contract
(a deployment that forgot `DASK_DISTRIBUTED__WORKER__RESOURCES__*`).

## Resource keys

These string constants form a contract with the executor plugin:

```python
RESOURCE_CPUS = "cpus"
RESOURCE_MEMORY = "memory"
RESOURCE_GPUS = "gpus"
```

Workers must advertise every key the executor may request — Dask
matches by exact key presence. The local-cluster path includes all
three even when the executor doesn't ask, so per-rule
`mem_mb`/`gpus_per_task` rules still schedule on a workstation.

## Node-shape detection

`_detect_node_shape()` reads SLURM env vars with sane fallbacks:

| Resource | Env var | Fallback |
|----------|---------|----------|
| CPUs | `SLURM_CPUS_ON_NODE` | `os.cpu_count()` |
| Memory | `SLURM_MEM_PER_NODE` (MB) | `psutil.virtual_memory().total` if installed; otherwise 0 (advisory; workers won't enforce caps) |
| GPUs | `SLURM_GPUS_ON_NODE` | `0` |

## SLURM-backed cluster details

```python
srun --ntasks=$SLURM_NNODES --ntasks-per-node=1 \
     dask worker <addr> --nthreads $cpus --nworkers 1 \
                        --resources "cpus=N memory=B gpus=G" --no-dashboard
```

The `--ntasks-per-node=1` is important: we want one worker per node,
not per CPU. The worker uses `--nthreads` to advertise its parallelism
within the node.

After spawning workers, the manager opens a temporary `Client(addr)` to
`wait_for_workers(n_workers=nnodes, timeout=120)`. If the workers
haven't connected within two minutes, raise.

On exit, the manager `terminate()`s the worker subprocess group, waits
up to 10s, then `kill()`s anything still alive.

## Why no `dask-jobqueue`?

`dask-jobqueue` would `sbatch` workers from inside an existing job —
fine, but adds dependency and indirection. Since we already require the
user to be inside an allocation (`salloc` / `sbatch`), `srun` is enough
and keeps everything in one process tree.

## Tests

`tests/test_dask_cluster.py` covers the three branches and the
resource-advertising contract. The SLURM branch is tested with mocked
`subprocess.Popen` plus a stubbed `Client.wait_for_workers`.
