# lc run

Materialize outputs declared in `astra.yaml`. Generates a Snakefile
and dispatches through Snakemake on a Dask cluster.

## Synopsis

```
lc run [OPTIONS] [OUTPUTS]...
```

`OUTPUTS` is zero or more output ids. With no arguments, materializes
everything (Snakemake's `rule all`).

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--universe`, `-u NAME` | all universes in `universes/*.yaml` (or `["default"]` if none exist) | Restrict to one universe. |
| `--jobs`, `-j N` | `os.cpu_count()` | Parallel jobs / Dask submission concurrency. Passed as both `--cores` and `--jobs` to Snakemake. |
| `--rerun-triggers TRIGGERS` | `code,input,mtime,params` | Comma-separated rerun triggers (forwarded to Snakemake). |
| `--force`, `-f` | off | `--force` when targets are named, `--forceall` otherwise. |
| `--verbose`, `-v` | off | Show the underlying Snakemake / executor chatter and the spawned `snakemake` invocation. |

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
```

## Inside SLURM

```bash
salloc -N 4 ...
lc run --universe baseline -j 16
```

`lc run` detects `SLURM_JOB_ID`, binds the Dask scheduler to the
driver's hostname, and launches one `dask worker` per node via `srun`.
Workers advertise `cpus`, `memory`, and `gpus` resources. Per-rule
resource hints (`cpus_per_task`, `mem_mb`, `gpus_per_task`) constrain
which workers can pick up which jobs.

## Provenance gotcha

If `~/.lightcone/config.yaml` says `runtime: auto` and no runtime is
on PATH, `lc run` falls back to running recipes on the host. Because
each manifest still records the *declared* `container_image`, this is a
provenance lie. `lc run` prints a yellow warning telling you to either
install a runtime or set `container.runtime: none` explicitly.

See [api/dask_cluster](../api/dask_cluster.md) for the cluster-shape
decision and [Architecture](../architecture.md) for the full execution
flow.
