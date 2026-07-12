# lightcone.engine.dask_cluster

Cluster lifecycle for `lc run`. One context manager (`cluster_for_run`),
four branches, no service to manage.

Source: `src/lightcone/engine/dask_cluster.py`.

## `cluster_for_run(*, verbose=False, local_directory=None, max_workers=None) → Iterator[dict[str, str]]`

Yields the **env overlay** the child snakemake process needs to reach
the cluster — the parent and the executor plugin are separate
processes, so connection info travels via environment variables.
Four branches in priority order:

1. **`DASK_SCHEDULER_ADDRESS` already set** → yield
   `{"DASK_SCHEDULER_ADDRESS": addr}` as-is. We don't own the cluster,
   so we don't tear it down.
2. **Dask Gateway detected** (`LIGHTCONE_GATEWAY_CLUSTER` or
   `DASK_GATEWAY__ADDRESS` set, e.g. a JupyterHub pod) → create an
   adaptive Gateway cluster bounded by *max_workers*, or attach to the
   named one; yield `{"LIGHTCONE_GATEWAY_CLUSTER": name}`. Gateway
   scheduler addresses use a `gateway://` comm scheme a bare `Client`
   cannot dial, so the child rejoins **by name** through the
   authenticated Gateway API. Created clusters are shut down on exit;
   attached ones are left running. Startup fails fast if workers don't
   advertise the resource contract below. Requires the optional
   dependency: `pip install lightcone-cli[gateway]`.
3. **`SLURM_JOB_ID` set** → start an in-process scheduler bound to the
   driver's SLURM hostname (`SLURMD_NODENAME` or `gethostname()`),
   then `srun` one `dask worker` per node across the allocation.
4. **None of the above** → `LocalCluster()` sized to the local machine.

Outside the Gateway branch the scheduler is always in-process, so its
lifetime equals the run's lifetime: no orphaned schedulers if the
driver crashes.

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
