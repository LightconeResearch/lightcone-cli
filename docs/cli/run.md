# lc run

Materialize outputs declared in `astra.yaml`. Generates a Snakefile
and dispatches through Snakemake on a Dask cluster.

## Synopsis

```text
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
4. **Pre-flight the images.** Every distinct `container:` in the spec is
   built (Containerfile) or pulled (registry ref) before the DAG starts,
   so the first run after a Containerfile edit doesn't die mid-DAG on a
   missing image. This is the same pass `lc build` runs — already-present
   images are skipped.
5. Generate `.lightcone/Snakefile` and
   `.lightcone/snakefile-config.json` for the selected universes.
6. Translate any explicit `OUTPUTS` into Snakemake target paths
   (`<output_dir>/.lightcone-manifest.json`) — this is what tells
   Snakemake "build that specific output."
7. Open a Dask cluster context. Four branches, checked in this order:

    | Condition | Cluster |
    |---|---|
    | `DASK_SCHEDULER_ADDRESS` set | Attach to that scheduler; never torn down. |
    | `DASK_GATEWAY__ADDRESS` set | Create a **run-scoped Dask Gateway cluster** with the project's image, adaptive from 1 to `--jobs` workers, culled on exit. |
    | `SLURM_JOB_ID` set | In-process scheduler + one `dask worker` per allocated node via `srun`. |
    | none of the above | `LocalCluster` sized to the machine. |

8. Spawn `snakemake -s … -d … --cores N --jobs N --executor dask
   --shared-fs-usage persistence input-output sources storage-local-copies
   source-cache --rerun-triggers …`, with the cluster's connection info
   in the environment (`DASK_SCHEDULER_ADDRESS`, or the cluster name for
   the Gateway branch, which is rejoined through the authenticated
   Gateway API rather than dialled by address).
9. In the default (non-verbose) path, filter the executor's banner
   chatter so the output reads as lightcone's, not Snakemake's. Real
   error content always passes through; on failure the last of
   snakemake's stderr is written to `snakemake-stderr-<pid>.log` under
   the scratch root.

`--shared-fs-usage` deliberately omits `software-deployment`. With it,
spawned job commands would embed the *driver's* `sys.executable` — a
path that doesn't exist inside a Dask Gateway worker image. Without it,
workers invoke plain `python` from their own environment, which is
correct on every backend.

## Output qualification

When the same `output_id` appears in multiple sub-analyses, you must
qualify it as `<analysis_id>.<output_id>`:

```bash
lc run inference                    # error if 'inference' is ambiguous
lc run hod_fitting.inference        # disambiguated
```

## How recipes get containerized

On `docker`, `podman`, and `podman-hpc`, each rule's body wraps the
recipe in a `<runtime> run --rm --pull=never -v "$PWD":"$PWD" -w "$PWD"
<image> bash -c '<recipe>'` shell.

On the `kubernetes` runtime this wrapping is a **passthrough**: the Dask
worker pod executing the recipe was already started *from* the project's
image, so wrapping would containerize twice. The image still flows into
`code_version` and into the manifest — and the pod reports back the
image it actually ran as the manifest's `worker_image` field. With
`runtime: none`, or a recipe with no `container:`, the recipe also runs
unwrapped.

After the recipe shell exits, the Snakefile calls `write_manifest()`
host-side and the validation snippet emits warnings for empty /
all-NaN / wrong-extension outputs.

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

## Inside a JupyterHub deployment

On a lightcone JupyterHub (Kubernetes + Dask Gateway), `lc run` needs no
arguments beyond the usual — the deployment injects
`DASK_GATEWAY__ADDRESS`, `LIGHTCONE_REGISTRY`, and
`LIGHTCONE_BUILD_BUCKET` into your session and `lc` picks them up:

```bash
lc run --universe baseline -j 8
```

- The pre-flight image pass builds through **GCP Cloud Build** (there is
  no local OCI runtime in your session) and pushes
  `<registry>/lc-<project>:<hash>`. Unchanged files never rebuild —
  freshness is a single registry check.
- A run-scoped Gateway cluster is created **with that image**, adaptive
  from 1 to `--jobs` workers, and culled when the run finishes. A
  Gateway cluster's image is fixed at creation, so create-per-run is
  what makes image updates seamless.
- `lc run` waits for the first worker before dispatching, bounded by
  `LIGHTCONE_GATEWAY_WORKER_TIMEOUT` (seconds, default `600`) — an
  unpullable image would otherwise hang at zero workers forever. It
  then asserts that workers advertise the `cpus`+`memory` resource
  contract, since Dask would silently never schedule a rule otherwise.

!!! warning "One image per run on this backend"
    The worker pod *is* the container, so the whole run executes in a
    single image. If `astra.yaml` resolves to more than one distinct
    container image, `lc run` refuses to start and asks you to
    consolidate on a single Containerfile (or one shared prebuilt
    image). Every other backend wraps per rule and is unaffected.

## Provenance gotcha

If `~/.lightcone/config.yaml` says `runtime: auto` and no runtime is
on PATH, `lc run` falls back to running recipes on the host. Because
each manifest still records the *declared* `container_image`, this is a
provenance lie. `lc run` prints a yellow warning telling you to either
install a runtime or set `container.runtime: none` explicitly.

See [api/dask_cluster](../api/dask_cluster.md) for the cluster-shape
decision and [Architecture](../architecture.md) for the full execution
flow.
