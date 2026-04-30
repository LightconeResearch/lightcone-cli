# Redesign: lightcone-cli on Snakemake

> Companion to `design_review.md`. Specs out a minimalist redesign of the execution layer using Snakemake as the orchestrator. The goal is to deliver every requirement from `design_review.md §1` while writing as little code as possible and relying on existing Snakemake infrastructure for everything it already does well.

---

## Design philosophy

Four rules guide every decision below.

1. **Snakemake does what Snakemake does.** The DAG, container execution, cluster submission, parallelism, dry-run, retries, staleness, profiles — these already exist, they're battle-tested in scientific computing, and we should not reimplement any of them. If a feature exists in Snakemake, we use it. If it doesn't, we ask whether we actually need it.

2. **We own only the integrity layer.** Snakemake gives ~90% of the requirements in `design_review.md` for free. The remaining 10% — the cryptographic provenance chain that makes outputs unforgeable — is the one thing we have to build, and it is the one thing worth building well.

3. **The user-facing surface does not change.** `lc run`, `lc status`, `lc verify` keep their semantics. The fact that Snakemake is underneath is an implementation detail. Users should never have to write a Snakefile by hand or know that one exists.

4. **This is a clean-slate replacement, executed in one go.** No backward compatibility, no dual-engine flag, no migration command. If we commit to this redesign, we delete the entire Dagster + Postgres engine and ship the Snakemake-based one as the new baseline. There are no existing production projects whose state we need to preserve; reasoning about migration paths is wasted complexity.

---

## Architecture at a glance

```
astra.yaml ── Snakefile generator ──> .lightcone/Snakefile
                                            │
                            snakemake (CLI subprocess)
                                            │
            ┌───────────────────────────────┼──────────────────────────────┐
            │              │                │                              │
       DAG resolution  staleness     task dispatch (Dask executor plugin)  │
       (Snakemake)     (mtime+code)  ── client.submit per rule ──┐         │
            │                                                    │         │
            │                            ┌───────────────────────┴───┐     │
            │                            │ in-process scheduler in   │     │
            │                            │ `lc run`; LocalCluster on │     │
            │                            │ workstation, srun-launched│     │
            │                            │ workers across SLURM nodes│     │
            │                            └───────────┬───────────────┘     │
            │                                        │                     │
            └─────────────────── per-rule run: block ┘                     │
                                            │                              │
                       ┌────────────────────┴────────────────────┐         │
                       │                                          │        │
              shell() the recipe                       write_manifest()    │
              (containerized via lc container runtime) (host-side Python)  │
                       │                                                   │
                       ▼                                                   │
               results/<u>/<o>/...                                         │
               results/<u>/<o>/.lightcone-manifest.json ──────────────────┘
```

**What Snakemake owns** (we do not write code for any of this):

- DAG construction from rule input/output declarations
- Topological execution, dependency resolution, parallelism (`--cores`, `--jobs`)
- Per-rule resource requests (`mem_mb`, `threads`, `gpus_per_task`)
- Dry-run (`-n`), DAG visualization (`--dag`, `--rulegraph`)
- Built-in staleness detection (`--rerun-triggers code params input mtime`)
- Locking, log capture, retry logic (`--retries`)
- Conda env management (if a project ever needs it)

**What we own** (this is the entire `lightcone-engine` package after the redesign):

1. `lightcone.engine.snakefile` — generates `.lightcone/Snakefile` from `astra.yaml`
2. `lightcone.engine.manifest` — `write_manifest()` plus the read/verify schema
3. `lightcone.engine.container` — builds and invokes container images from Containerfiles with deterministic content-addressed hashes
4. `lightcone.engine.status` — walks `results/` and reads manifests, no Snakemake dependency
5. `lightcone.engine.verify` — recomputes hashes and validates the provenance chain
6. `lightcone.engine.dask_cluster` — `cluster_for_run()` context manager: detects environment (workstation / inside `salloc` / pre-existing scheduler) and yields a Dask scheduler address for the run's lifetime
7. `snakemake_executor_plugin_dask` — vendored Snakemake executor plugin: translates each rule into `client.submit(_run_shell, …)` with per-task resources

Seven focused modules. No execution backend dispatch beyond Dask (Snakemake hands rules to the executor plugin; the plugin hands them to Dask), no IO manager (filesystem paths are conventional), no cluster lifecycle daemon (the scheduler is in-process inside `lc run`).

---

## The Snakefile

Generated on every `lc run` (cheap; pure function of `astra.yaml`). Lives at `.lightcone/Snakefile`. Users do not edit it. Concrete example:

```python
# .lightcone/Snakefile — auto-generated, do not edit
# Source: /path/to/project/astra.yaml @ git sha 7f3a9c2

import json
from lightcone.engine.manifest import write_manifest

UNIVERSES = ["fiducial", "high_z"]

# Per-output config — code_version, recipe, decisions per universe
CFG = json.load(open(".lightcone/snakefile-config.json"))


rule all:
    input:
        expand(
            "results/{u}/{o}/.lightcone-manifest.json",
            u=UNIVERSES,
            o=["clean_catalog", "power_spectrum", "summary"],
        )


rule clean_catalog:
    input:
        catalog="data/raw_catalog.fits",
    output:
        data=directory("results/{universe}/clean_catalog/"),
        manifest="results/{universe}/clean_catalog/.lightcone-manifest.json",
    params:
        cfg=lambda wc: CFG["clean_catalog"][wc.universe],
    resources:
        mem_mb=8000, cpus_per_task=4,
    run:
        # cfg["shell_command"] is the recipe pre-wrapped at generation
        # time, e.g. `podman run --rm -v "$PWD":"$PWD" -w "$PWD"
        # lc-clean-a1b2c3d4 bash -c '<recipe>'`. We do not use
        # Snakemake's ``container:`` directive — see below.
        shell(params.cfg["shell_command"])
        write_manifest(                         # runs on the HOST
            path=output.manifest,
            output_dir=output.data,
            inputs=input,
            cfg=params.cfg,
        )
```

**Key design notes** about the generated Snakefile:

- **One rule per `output_id`**, with `{universe}` as the only wildcard. This avoids ambiguity (one output_id ↔ one rule) and makes the Snakefile readable.
- **The manifest is a declared output of every rule.** Snakemake re-runs any rule whose manifest is missing — agents cannot "fake" materialization by dropping just the data file. Snakemake's existence check enforces it.
- **`directory()` for the output dir.** Recipes write multiple files; we model the whole directory as the output. Snakemake wipes it before each rule run.
- **`run:` block, not `shell:`.** The `run:` body is plain Python executing on a Dask worker. The `shell()` call inside it spawns the recipe; `write_manifest()` runs in the same Python process immediately after the recipe exits. One atomic rule body, no CLI subcommand.
- **We invoke the container runtime ourselves**, not via `container:`. The pre-wrapped `shell_command` literally contains the `podman run …` / `docker run …` / `apptainer exec …` call. This keeps the manifest's `container_image` field as the strong evidence of what actually ran (we own the wrap), and lets us pick `podman-hpc` on Perlmutter, `podman` elsewhere, all with one `lc build` artifact.
- **`code_version` is embedded in the shell command** as a leading no-op (`: lc_code_version=<sha>;`). Snakemake's `code` rerun-trigger hashes the rule body, so a recipe / container / decision change propagates a new sha into the rule body and the rule re-runs.
- **All provenance details live in a sidecar JSON** (`.lightcone/snakefile-config.json`) referenced via `params.cfg`. The Snakefile itself stays small. The cfg blob holds recipe text, decisions, and code_version per (output, universe) pair.
- **`resources:` come from astra.yaml.** Each output declares its needs (`mem_mb`, `cpus_per_task`, `gpus_per_task`); the Dask executor plugin translates them 1:1 into per-task Dask resource constraints.

That's the whole template. ~30 lines of Snakefile per output, generated from a Jinja template.

---

## The `write_manifest()` function

The integrity layer is one Python function imported by the generated Snakefile. It runs on the host, after Snakemake has finished executing the containerized recipe. There is no `lc _materialize` CLI subcommand — Snakemake's `run:` block calls our function directly.

```python
# lightcone/engine/manifest.py — sketch, ~100 lines

def write_manifest(*, path, output_dir, inputs, cfg):
    # 1. Resolve input data versions from upstream manifests.
    input_versions = {}
    for inp in inputs:
        m = read_upstream_manifest(inp)
        input_versions[inp] = m["data_version"] if m else fingerprint_external(inp)

    # 2. Hash the output directory deterministically
    #    (sorted file list, sha256 each file, sha256 of the concatenation).
    data_version = sha256_dir(output_dir)

    # 3. Write the manifest. Self-describing, content-addressed.
    manifest = {
        "schema_version": 1,
        "output_id": cfg["output_id"],
        "universe_id": cfg["universe_id"],
        "code_version": cfg["code_version"],   # sha256(recipe + image hash + decisions)
        "data_version": data_version,          # sha256 of output dir contents
        "container_image": cfg["container_image"],
        "recipe": cfg["recipe"],
        "decisions": cfg["decisions"],
        "input_versions": input_versions,      # {input_path: data_version}
        "git_sha": cfg["git_sha"],
        "lc_version": cfg["lc_version"],
        "finished_at": time.time(),
        "host": socket.gethostname(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    }
    write_atomic(path, json.dumps(manifest, sort_keys=True, indent=2))
```

That's the integrity layer in one function. We don't need a runner, a CLI subcommand, or a process-spawn step — the recipe ran in the container under Snakemake's control, then the function writes the sidecar.

Failure semantics fall out cleanly: if the `shell()` call inside the rule's `run:` block fails, Snakemake aborts the rule before `write_manifest()` is reached, so no manifest is written. Snakemake then sees the manifest output is missing and the rule is correctly marked as not done.

---

## Manifest schema

One JSON file per output, at `results/<universe>/<output>/.lightcone-manifest.json`:

```json
{
  "schema_version": 1,
  "output_id": "power_spectrum",
  "universe_id": "fiducial",
  "code_version": "sha256:f4a2...",
  "data_version": "sha256:9c1e...",
  "container_image": "lc-ps-d0e1234...",
  "recipe": "python scripts/compute_pk.py --input {inputs} --output {output} --kmax {decisions.kmax}",
  "decisions": {"kmax": 0.5, "binning": "log"},
  "input_versions": {
    "results/fiducial/clean_catalog/": "sha256:7b3d..."
  },
  "git_sha": "7f3a9c2...",
  "lc_version": "0.4.1",
  "started_at": 1714134200.123,
  "finished_at": 1714134462.871,
  "exit_code": 0,
  "host": "nid001234",
  "slurm_job_id": "29384712"
}
```

**Properties this gives us:**

- **`code_version`** is the sha256 of `(recipe || container_image || canonical(decisions))`. A change in any of those three propagates to a new code_version, and (since `code_version` is embedded in the rule's shell command at generation time) Snakemake's `code` rerun-trigger fires automatically. We get free re-run-on-code-change without any custom staleness logic.
- **`data_version`** is the sha256 of the output directory's content (sorted file list, sha256 each file, sha256 the concatenation). It is what `lc verify` recomputes.
- **`input_versions`** records the data_version of each upstream output the recipe consumed. This makes the chain transitively content-addressed: every output's identity depends on every byte of every upstream input.
- **External inputs** (raw data files declared in astra.yaml as inputs but not produced by any recipe) get a `(mtime, size)` fingerprint by default, and a real sha256 with `lc run --strict-inputs`.
- **The manifest is signed by content addressing.** Recompute `data_version` from disk and compare to the recorded value: a mismatch means the data on disk is not what the manifest claims it is (either the file changed after materialization, or the manifest was forged).

---

## User-facing commands

### `lc run [outputs...] [--universe U] [--target T]`

```python
def run(outputs, universe, target):
    # 1. Generate Snakefile + per-rule cfg JSONs from astra.yaml.
    snakefile.generate(astra_yaml, project_root)

    # 2. Resolve target → snakemake profile.
    profile = target_to_profile(target)  # generates .lightcone/profiles/<target>/

    # 3. Compute the requested target paths.
    targets = resolve_target_paths(outputs, universe)  # list of manifest paths

    # 4. Shell out to snakemake.
    subprocess.run([
        "snakemake",
        *targets,
        "--profile", profile,
        "--rerun-triggers", "code", "input", "mtime",
        "--cores", str(cores),
    ], check=True)
```

That's it. `lc run` is ~80 lines; the bulk is target path resolution and exec wiring.

### `lc status [--universe U]`

```python
def status(universe):
    spec = load_astra(astra_yaml)
    for output in spec.outputs_for_universe(universe):
        manifest_path = results_dir / output.universe / output.id / ".lightcone-manifest.json"
        if not manifest_path.exists():
            yield (output, "missing")
            continue
        manifest = read_manifest(manifest_path)
        current_code_version = compute_code_version(spec, output)
        if manifest["code_version"] != current_code_version:
            yield (output, "stale")
        else:
            yield (output, "ok")
```

`lc status` is ~80 lines and **does not import or invoke Snakemake at all**. The manifests are the source of truth for "what is materialized." Snakemake's metadata directory is irrelevant to this command. This is by design: `lc status` works offline, on a fresh clone with no `.snakemake/` directory, on a frozen archive of results.

### `lc verify [--universe U] [--strict]`

```python
def verify(universe, strict):
    for output in spec.outputs_for_universe(universe):
        manifest = read_manifest(...)
        actual = sha256_dir(output_dir)
        if actual != manifest["data_version"]:
            yield (output, "TAMPERED", manifest["data_version"], actual)
        # Verify upstream chain.
        for inp_path, recorded in manifest["input_versions"].items():
            up = read_manifest(... for inp_path)
            if up and up["data_version"] != recorded:
                yield (output, "BROKEN_CHAIN", inp_path)
        if strict:
            # Optional: re-run and compare. Expensive.
            ...
```

`lc verify` is ~100 lines, pure offline check, no orchestrator dependency.

### Cluster execution

There is **no scheduler daemon, no Postgres, and no `lc cluster` command.** The Dask scheduler is in-process inside `lc run`; its lifetime equals the run's lifetime. No service to keep alive across runs, no orphaned schedulers if the driver crashes, no `lc cluster start/attach/stop` lifecycle.

Users who want a single allocation with many tasks inside it (today's `attach` mode) run `salloc` themselves and invoke `lc run` inside the allocation. `lc run` then auto-detects the environment via `lightcone.engine.dask_cluster.cluster_for_run`:

| Trigger | Behavior |
|---|---|
| `DASK_SCHEDULER_ADDRESS` set | Connect to the existing scheduler. We don't own its lifecycle. |
| `SLURM_JOB_ID` set | Start an in-process `LocalCluster(n_workers=0)` bound to the driver's hostname; `srun --ntasks=$NNODES --ntasks-per-node=1 dask worker $ADDR` launches one persistent worker per node, each advertising the node's full `cpus`/`memory`/`gpus` as Dask resources. |
| Neither | `LocalCluster()` sized to the local machine (one worker, all cpus). |

Snakemake dispatches each rule via our vendored executor plugin at `src/snakemake_executor_plugin_dask/`: `client.submit(_run_shell, cmd, resources={cpus, memory, gpus}, pure=False)`. Per-rule `cpus_per_task` / `mem_mb` / `gpus_per_task` translate 1:1 to per-task Dask resources, and the scheduler bin-packs tasks into workers up to each worker's advertised budget. Listing or canceling running jobs is `squeue` / `scancel` directly; a Dask dashboard is exposed on a random port for live introspection.

The same plugin and bootstrap path covers laptop → workstation → multi-node SLURM allocation, so there is one execution code path everywhere.

#### Why Dask, not `slurm-jobstep` or `slurm`

The reasonable Snakemake-native alternatives for the multi-node-within-one-allocation case are `--executor slurm` (per-rule `sbatch` from a head node) and `--executor slurm-jobstep` (per-rule `srun --jobid=$SLURM_JOB_ID …` inside an existing allocation). We considered both:

- **`--executor slurm` is wrong shape for our HPC story.** It assumes a head node submitting sbatch jobs that flow through the queue independently. The Snakemake docs themselves warn that running it inside an active SLURM job leads to unpredictable behavior. Our users want pilot-job semantics: one big `salloc`, many tasks dispatched within it, no per-task queue wait.
- **`--executor slurm-jobstep` is the right *shape* but the wrong *contract*.** The plugin's repo description states it is "meant for internal use by snakemake-executor-plugin-slurm" — i.e. it's officially a helper invoked by the main slurm plugin's sbatch wrappers, not a user-facing standalone executor. Using it as a standalone pilot-job executor is an off-label code path the maintainers do not commit to keeping working. It also inherits two known footguns we'd then have to defend against in user docs: SLURM 22.05+'s `--cpus-per-task` non-inheritance (`snakemake-executor-plugin-slurm#41`) and the long-standing one-core dispatch issue (`snakemake#2447`).
- **Dask gives us a stable, vendored substrate we control.** ~155 lines of executor plugin + ~200 lines of cluster bootstrap is a small, bounded amount of code. Workers are persistent within a run (sub-second task dispatch, useful when the DAG fans out to many short tasks); resource accounting is exposed via the Dask dashboard; failure modes are well-understood. The trade we accept is that resource matching is advisory rather than cgroup-enforced — for a workload of minutes-to-hours recipes whose `mem_mb` is declared in `astra.yaml`, this is acceptable, and SLURM's per-allocation memory cgroup is the backstop.
- **Flux was evaluated and rejected.** Richer hierarchical scheduling and sub-allocation packing, but `module load flux` (or building flux-core from source) on every host is install friction we don't want outside Perlmutter. Dask is `pip install` everywhere with no PMI dependency.

If standalone `slurm-jobstep` becomes a maintained, user-facing executor in the future, this decision is worth revisiting — the win would be cgroup-enforced per-rule resources and zero bespoke code. Until then, owning ~350 LOC of well-scoped Dask plumbing is the better trade.

The entire `engine/clusters/` directory (~1000 LOC of cluster lifecycle, Postgres bootstrap, scheduler management) is **deleted**, not replaced.

---

## Code footprint

| Module | Purpose | LOC |
|---|---|---:|
| `engine/snakefile.py` | Jinja template + generator from `astra.yaml` | 333 |
| `engine/manifest.py` | `write_manifest()` + read/verify schema | 205 |
| `engine/container.py` | Deterministic image build, hashing, runtime wrap | 689 |
| `engine/dask_cluster.py` | `cluster_for_run()` — workstation / SLURM / pre-existing | 197 |
| `engine/status.py` | Walk results, compute status from manifests | 140 |
| `engine/verify.py` | Recompute hashes, validate chain | 139 |
| `engine/validation.py` | Post-materialization output shape checks | 180 |
| `engine/tree.py` | Sub-analysis tree traversal | 317 |
| `engine/site_registry.py` | Known HPC site defaults (Perlmutter, etc.) | 106 |
| `snakemake_executor_plugin_dask/` | Vendored Snakemake executor plugin | 154 |
| `cli/commands.py` | Click surface; subprocess.run + argument plumbing | 685 |
| **Total** | | **~3145** |

For comparison, the previous `engine/` (Dagster + Dask + Postgres + cluster lifecycle + runner + targets + slurm-info) was roughly **5500+ LOC** before counting tests. The redesign cuts it substantially while delivering more of `design_review.md`.

What gets **deleted**:

- `engine/assets.py` (Dagster asset factory) — gone
- `engine/io_manager.py` (the misnamed pass-through) — gone, was never a real IO manager
- `engine/runner.py` (docker/podman/venv/local dispatch) — gone, runtime invocation is now wrapped into the rule's `shell_command` at generation time
- `engine/dask_entrypoint.py` (Dagster-Dask reconstructable bootstrap) — gone, replaced by direct Dask client use in the executor plugin
- `engine/clusters/_pg.py` (Postgres lifecycle) — gone
- `engine/clusters/_local.py`, `_slurm.py`, `_slurm_info.py` — gone; the in-allocation pilot-job pattern is handled by `dask_cluster.py`'s srun-launched workers
- `engine/targets.py` (the old `lc target` config) — gone
- The `dagster`, `dagster-dask`, `dagster-postgres`, `dagster-webserver`, `dagster-docker`, `pixeltable-pgserver` dependencies — dropped from `pyproject.toml`

What gets **kept** from the previous engine:

- `dask` and `distributed` — re-purposed: no longer behind Dagster's `dagster-dask` shim, now driven directly by our vendored Snakemake executor plugin and `cluster_for_run()` bootstrap.

What gets **added**:

- `snakemake>=9.0` (core), `snakemake-interface-executor-plugins`, `snakemake-interface-common`, `jinja2` — Snakemake itself plus the SDKs we need to ship the Dask executor plugin.

---

## How requirements from `design_review.md` are met

| Requirement | How |
|---|---|
| **§1.1 Verifiable execution** | Manifest is content-addressed; `data_version` = sha256 of output dir; chain links upstream `data_versions`. Agents cannot fake outputs without producing a valid manifest, and a forged manifest fails `lc verify`. |
| **§1.2 Reproducibility** | `code_version` embedded in rule shell command + Snakemake's `code` rerun-trigger = automatic re-execute on code/container/decision change. External inputs tracked via mtime (default) or sha256 (`--strict-inputs`). |
| **§1.3 Transparent CLI usability** | `lc run`, `lc status`, `lc verify` keep their semantics. The Snakefile is implementation detail. Users who want it can `cat .lightcone/Snakefile` or run `snakemake --dag` directly. |
| **§1.4 Frictionless local + HPC** | One executor plugin (`--executor dask`) and one bootstrap (`cluster_for_run`) cover laptop, workstation, and multi-node `salloc`. No daemons to manage; pilot-job semantics inside an allocation; no per-rule sbatch wait. |
| **§1.5 `astra.yaml` invariant** | Snakefile is regenerated from `astra.yaml` on every run. There is no parallel state to drift. |
| **§1.6 Offline auditability** | `lc verify` and `lc status` walk manifests directly; no Snakemake or database needed for either. A frozen archive of `results/` plus the `astra.yaml` is fully auditable. |

---

## Why `data_version`-as-output works (the key trick)

The cleanest design choice in this redesign — the one that does the most work for the fewest lines — is treating the manifest as a declared Snakemake output.

```python
output:
    data=directory("results/{u}/{o}/"),
    manifest="results/{u}/{o}/.lightcone-manifest.json",
```

Consequences:

1. **Atomic materialization.** Snakemake removes both outputs before running the rule. The recipe writes data; the `run:` block writes the manifest only on success. Either both exist (materialized + valid) or neither does (Snakemake re-runs).
2. **Agent-faked-file detection by absence.** If an agent drops files into `results/<u>/<o>/` without going through `lc run`, the manifest is absent; `lc status` reports "missing" and Snakemake will re-run the rule. There is no way to fool the system without writing both data *and* a manifest with a self-consistent content hash — which requires going through the same `run:` block that runs the recipe under Snakemake's control.
3. **No reliance on Snakemake's metadata.** We never read `.snakemake/metadata/`. Whether Snakemake's metadata DB grows, breaks, or gets deleted is irrelevant to provenance. The manifests on disk are the truth.
4. **Free staleness on code change.** The `code_version` is a parameter passed into the rule's shell command. Snakemake hashes the rule body (which now includes the code_version literal). Change a recipe → new code_version → new shell command → Snakemake's `code` trigger fires.

This single design choice replaces what would otherwise be a complex bidirectional sync between our manifest layer and Snakemake's metadata layer.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| **`.snakemake/` on shared HPC filesystems.** Default file-based metadata (one file per output) hits inode pressure; SQLite metadata has lock contention on Lustre. | We don't depend on `.snakemake/metadata/` for anything user-facing. Treat it as a cache, mount on local node scratch via env var, accept that it may need to be rebuilt. |
| **Container exec is ours, not Snakemake's.** We pre-wrap each recipe with the explicit container runtime call at generation time and leave Snakemake's `container:` directive unused. | Trade is deliberate: we keep the manifest's `container_image` field as authoritative evidence of what ran, and we get to pick `podman-hpc` on Perlmutter and `podman` elsewhere from one declared image tag. |
| **Loss of multi-backend fallback.** The previous runner fell back from container → venv → local on failure. The new wrap does not. | This is a feature, not a regression. Silent backend changes destroy reproducibility; we want failures to fail. |
| **Snakemake locks the workdir during a run.** Two `lc run` invocations from two terminals will block each other. | Acceptable. The previous Dagster engine didn't isolate that case meaningfully either, and concurrent project runs are rare. If needed, generate per-invocation Snakefile in a tmp workdir. |
| **No unified UI.** Lose Dagster's webserver. | `snakemake --report report.html` plus the Dask dashboard cover most of what the UI was used for. `lc status` is the daily driver. |
| **Dask resource matching is advisory, not cgroup-enforced.** A rule that exceeds its declared `mem_mb` won't be killed by Dask itself. | SLURM's per-allocation memory cgroup is the backstop on HPC; `mem_mb` declarations in `astra.yaml` are the user's commitment. If a recipe blows past it, the kernel OOM-kills the worker and Snakemake retries. |
| **Driver-on-worker-node contention.** Inside `salloc`, `lc run` and the in-process scheduler share their node with one worker. | One process worth of CPU on one node out of N; negligible at our recipe granularity. |
| **Snakemake API instability.** | We shell out to the CLI, not the Python API. CLI is stable. |
| **Recipes that need Python data (not files) between steps.** | Out of scope by design (and was never supported by current architecture either). Recipes communicate via files. |

---

## What we explicitly do *not* build

- **Per-rule on-success hook.** Snakemake doesn't have one; we don't need one because the `run:` block *is* the rule body — `write_manifest()` runs after `shell()` on the host.
- **A custom DAG executor.** Snakemake is the executor.
- **A persistent metadata database.** Manifests on disk are sufficient and survive everything.
- **A scheduler daemon.** The Dask scheduler is in-process inside `lc run`; no service to keep alive across runs.
- **A Snakemake API integration in Python.** We shell out. CLI is stable; Python API is documented as internal.
- **Universe-as-partition machinery.** Universes are a wildcard dimension in rules. `expand()` over `UNIVERSES` in `rule all` is the entire fan-out logic.

---

## Open questions

These are real design questions that need a small spike or decision before implementation:

1. **External input hashing default.** sha256 of multi-GB raw data is expensive. Default `(mtime, size)` fingerprint is cheap but defeatable. Recommendation: `(mtime, size)` by default, `--strict-inputs` opt-in for paranoid mode, and document that `lc verify --strict` always recomputes.

2. **Recipe text in the manifest.** We can store the post-substitution recipe (with all `{params}` resolved) which is the most useful for a human reading the manifest, OR the pre-substitution template plus the params dict. Recommendation: both — they're both small.

3. **Where the per-rule cfg JSONs live.** Inside `.lightcone/`? This adds files. Alternative: serialize them inline in the Snakefile as Python literals. Recommendation: start with sidecar JSONs (cleaner separation); revisit if file count gets noisy.

4. **`alias` outputs (no recipe, just reference another output).** Rendered as a trivial rule with a `cp -r` shell, OR as a symlink, OR not rendered at all and the consumer references the upstream directly. Recommendation: don't render — let the dependency graph in `astra.yaml` resolve aliases at Snakefile-generation time.

5. **Container provenance** — record the SIF path, the Containerfile hash, and the resolved apptainer image hash (after pull) in the manifest? The image hash is the strongest evidence of what actually ran. Recommendation: record all three; the cost is a few extra fields.

---

## Bottom line

We replace Dagster + Postgres with Snakemake + a small content-addressed manifest layer + a vendored Dask executor plugin. The user-facing surface stays identical. The engine LOC roughly halves. Provenance becomes a real cryptographic property, not a process-boundary policy. The HPC story stops being about bundling services and starts being about a single in-process scheduler whose lifetime equals the run.

The minimum we have to write ourselves is: a Snakefile generator, a manifest module, a container build/hash/wrap module, a status walker, a verify routine, a `cluster_for_run` bootstrap, and a Snakemake-to-Dask executor plugin. Everything DAG-shaped — topological execution, parallelism, staleness detection, retries, dry-run, reports — is Snakemake's job. Everything task-dispatch-shaped — submitting work, matching to resources, bin-packing into worker capacity — is Dask's job. We own the seams between them and the integrity layer that hangs off each rule.

The strongest single argument for this design is not the code reduction. It is that the integrity property we care about (the agent cannot fake an output) becomes a *consequence of how the system is built* — manifests are required Snakemake outputs, manifests are content-addressed, content addresses chain — rather than a policy enforced by a process boundary. That is the property `design_review.md` calls out as the headline requirement, and this is the cleanest way I see to deliver it.
