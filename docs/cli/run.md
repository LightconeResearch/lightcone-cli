# lc run

Materialize outputs declared in `astra.yaml`. Generates a Snakefile
and dispatches through Snakemake on a Dask cluster.

## Synopsis

```text
lc run [OPTIONS] [OUTPUTS]...
```

`OUTPUTS` is zero or more output ids. With no arguments, a synchronous run
materializes everything (Snakemake's `rule all`). An asynchronous run requires
at least one explicit output.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--universe`, `-u NAME` | all universes in `universes/*.yaml` (or `["default"]` if none exist) | Restrict to one universe. |
| `--jobs`, `-j N` | `os.cpu_count()` | Parallel jobs / Dask submission concurrency. Passed as both `--cores` and `--jobs` to Snakemake. |
| `--rerun-triggers TRIGGERS` | `code,input,mtime,params` | Comma-separated rerun triggers (forwarded to Snakemake). |
| `--force`, `-f` | off | `--force` when targets are named, `--forceall` otherwise. |
| `--verbose`, `-v` | off | Show the underlying Snakemake / executor chatter and the spawned `snakemake` invocation. |
| `--async` | off | Submit explicit output(s) in one coarse SLURM job per selected universe instead of executing immediately. Requires at least one `OUTPUT`; Perlmutter only in v1. |
| `--account NAME` | `slurm.account` in `~/.lightcone/config.yaml` | Override the SLURM account for this async submission. Requires `--async`. |

## What happens, step by step

1. Find the project (walk up looking for `astra.yaml`).
2. Discover universes from `universes/*.yaml` (default to `["default"]`).
3. Resolve the container runtime via
   `lightcone.engine.container.load_runtime`. If `auto` falls back to
   `none` while the spec declares containers, print a loud provenance
   warning.
4. Generate `.lightcone/Snakefile` and
   `.lightcone/snakefile-config.json` for the selected universes.
5. Translate any explicit `OUTPUTS` into Snakemake target paths
   (`<output_dir>/.lightcone-manifest.json`) — this is what tells
   Snakemake "build that specific output."
6. Open a Dask cluster context (`local`, `srun`-backed inside
   `SLURM_JOB_ID`, or external if `DASK_SCHEDULER_ADDRESS` is set).
7. Spawn `snakemake -s … -d … --cores N --jobs N --executor dask
   --rerun-triggers …` with `DASK_SCHEDULER_ADDRESS` in the environment.
8. In the default (non-verbose) path, filter the executor's banner
   chatter so the output reads as lightcone's, not Snakemake's. Real
   error content always passes through.

With `--async`, steps 3–8 happen later inside the batch allocation. The
submission process instead resolves the requested sub-DAG, removes recipes
whose manifests and input provenance are already current, validates resources
for the remaining work, chooses `shared` or `regular`, renders an sbatch
script, submits it, and writes `.lightcone/jobs/<job-id>.json`.

## Output qualification

When the same `output_id` appears in multiple sub-analyses, you must
qualify it as `<analysis_id>.<output_id>`:

```bash
lc run inference                    # error if 'inference' is ambiguous
lc run hod_fitting.inference        # disambiguated
```

Each rule's body wraps the recipe in a `<runtime> run --rm --pull=never
-v "$PWD":"$PWD" -w "$PWD" <image> bash -c '<recipe>'` shell when a
container is configured. After the recipe shell exits, the Snakefile
calls `write_manifest()` host-side and the validation snippet emits
warnings for empty / all-NaN / wrong-extension outputs.

## Examples

```bash
lc run                                         # all outputs, all universes
lc run --universe baseline                     # one universe
lc run accuracy                                # one output
lc run accuracy precision --universe baseline  # several
lc run --jobs 4 --verbose                      # parallel, with stack noise
lc run --force --universe baseline             # rebuild everything
lc run --rerun-triggers params,input           # tighter staleness
lc run --async accuracy -u baseline            # submit one queued batch job
```

## Asynchronous SLURM jobs

First set the account once:

```yaml
# ~/.lightcone/config.yaml
container:
  runtime: podman-hpc
slurm:
  account: m1234
  time_padding: 1.5
```

Every recipe in the unresolved portion of the selected output's sub-DAG must
declare a `time_limit`. Already-current upstream recipes do not contribute to
the allocation. Other resources default to one CPU, no requested memory, and
no GPU:

```yaml
recipe:
  command: python scripts/fit.py --output {output}
  resources:
    cpus: 16
    memory: 64GB
    gpus: 1
    time_limit: 2h
```

The allocation shape is the element-wise maximum across those pending recipes.
The walltime is their serial sum multiplied by `slurm.time_padding`. On
Perlmutter, a job fitting the shared CPU/GPU profile uses `shared`; anything
larger uses `regular`, up to the 48-hour cap. A recipe larger than one node or
a padded walltime beyond the cap is rejected.

Submission is allowed from both login and compute nodes. Parent-allocation
`SLURM_*` variables are removed from the environment passed to `sbatch`, so
the child allocation supplies a consistent CPU/TRES environment for its own
`srun` workers. If `-u` is omitted, Lightcone submits one job per discovered
universe. The generated script activates the environment containing the
current Python executable and runs plain `lc run ...` inside the allocation.
It therefore uses the same Dask, Snakemake, validation, manifest, and container
path as a synchronous run, including `podman-hpc`; it never installs packages
on a compute node.

Use async execution for an explicit expensive materialized boundary, for
example `lc run --async expensive_fit -u baseline`. Once it finishes, run any
cheap downstream outputs normally. Bare `lc run --async` is rejected rather
than submitting the whole analysis implicitly. If every selected output is
already current, Lightcone reports that there is nothing to submit.

Use `lc status` to poll and [`lc cancel`](cancel.md) to cancel a recorded job.

## Inside SLURM

```bash
salloc -N 4 ...
lc run --universe baseline -j 16
```

`lc run` detects `SLURM_JOB_ID`, binds the Dask scheduler to the
driver's hostname, and launches one `dask worker` per node via `srun`.
Workers advertise `cpus`, `memory`, and `gpus` resources. ASTRA recipe
resources are translated to Snakemake's `cpus_per_task`, `mem_mb`,
`gpus_per_task`, and `runtime` names, constraining which workers can pick up
which jobs. Before starting Dask, Lightcone checks that every selected rule
fits one worker in the current allocation; an impossible shape fails with a
larger-allocation / `--async` hint instead of waiting indefinitely.

## Provenance gotcha

If `~/.lightcone/config.yaml` says `runtime: auto` and no runtime is
on PATH, `lc run` falls back to running recipes on the host. Because
each manifest still records the *declared* `container_image`, this is a
provenance lie. `lc run` prints a yellow warning telling you to either
install a runtime or set `container.runtime: none` explicitly.

See [api/dask_cluster](../api/dask_cluster.md) for the cluster-shape
decision and [Architecture](../architecture.md) for the full execution
flow.
