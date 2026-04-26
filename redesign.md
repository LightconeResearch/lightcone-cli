# Redesign: lightcone-cli on Snakemake

> Companion to `design_review.md`. Specs out a minimalist redesign of the execution layer using Snakemake as the orchestrator. The goal is to deliver every requirement from `design_review.md §1` while writing as little code as possible and relying on existing Snakemake infrastructure for everything it already does well.

---

## Design philosophy

Four rules guide every decision below.

1. **Snakemake does what Snakemake does.** The DAG, container execution, cluster submission, parallelism, dry-run, retries, staleness, profiles — these already exist, they're battle-tested in scientific computing, and we should not reimplement any of them. If a feature exists in Snakemake, we use it. If it doesn't, we ask whether we actually need it.

2. **We own only the integrity layer.** Snakemake gives ~90% of the requirements in `design_review.md` for free. The remaining 10% — the cryptographic provenance chain that makes outputs unforgeable — is the one thing we have to build, and it is the one thing worth building well.

3. **The user-facing surface does not change.** `lc run`, `lc status`, `lc verify` keep their semantics. The fact that Snakemake is underneath is an implementation detail. Users should never have to write a Snakefile by hand or know that one exists.

4. **This is a clean-slate replacement, executed in one go.** No backward compatibility, no dual-engine flag, no migration command. If we commit to this redesign, we delete the entire Dagster + Dask + Postgres engine and ship the Snakemake-based one as the new baseline. There are no existing production projects whose state we need to preserve; reasoning about migration paths is wasted complexity.

---

## Architecture at a glance

```
astra.yaml ── Snakefile generator ──> .lightcone/Snakefile
                                            │
                            snakemake (CLI subprocess)
                                            │
            ┌───────────────────────────────┼───────────────────────────────┐
            │              │                │                │              │
       DAG resolution  staleness     cluster submission  container exec   conda
       (Snakemake)     (mtime+code)  (slurm plugin)      (apptainer/docker)│
            │                                                              │
            └─────────────────── per-rule run: block ──────────────────────┘
                                            │
                       ┌────────────────────┴────────────────────┐
                       │                                          │
              shell() the recipe                       write_manifest()
              (containerized by Snakemake)             (host-side Python)
                       │
                       ▼
               results/<u>/<o>/...
               results/<u>/<o>/.lightcone-manifest.json
```

**What Snakemake owns** (we do not write code for any of this):

- DAG construction from rule input/output declarations
- Topological execution, dependency resolution, parallelism (`--cores`, `--jobs`)
- Cluster submission via `snakemake-executor-plugin-slurm` (sbatch + status polling)
- Per-rule resource requests (`mem_mb`, `runtime`, `slurm_partition`, `slurm_account`, `gpu`)
- Profiles for site-specific config (`--profile`, `--workflow-profile`)
- Dry-run (`-n`), DAG visualization (`--dag`, `--rulegraph`)
- Built-in staleness detection (`--rerun-triggers code params input mtime software-env`)
- Locking, log capture, retry logic (`--retries`)
- **Container runtime invocation** — `--use-apptainer` pulls/caches SIFs, mounts the cwd, runs each rule's `shell()` calls inside the configured container
- Conda env management (if a project ever needs it)

**What we own** (this is the entire `lightcone-engine` package after the redesign):

1. `lightcone.engine.snakefile` — generates `.lightcone/Snakefile` from `astra.yaml`
2. `lightcone.engine.manifest` — `write_manifest()` plus the read/verify schema
3. `lightcone.engine.container` — builds container images from Containerfiles with deterministic content-addressed hashes (kept from today)
4. `lightcone.engine.status` — walks `results/` and reads manifests, no Snakemake dependency
5. `lightcone.engine.verify` — recomputes hashes and validates the provenance chain
6. `lightcone.engine.profile` — translates `lc target` config into a snakemake profile YAML

Six focused modules. No execution backend dispatch (Snakemake does it), no IO manager (filesystem paths are conventional), no cluster lifecycle (no service to manage).

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
    container:
        ".lightcone/images/lc-clean-a1b2c3d4.sif"
    params:
        cfg=lambda wc: CFG["clean_catalog"][wc.universe],
    resources:
        mem_mb=8000, runtime=60,
    run:
        shell("{params.cfg[recipe]}")          # runs INSIDE the container
        write_manifest(                         # runs on the HOST
            path=output.manifest,
            output_dir=output.data,
            inputs=input,
            cfg=params.cfg,
        )


rule power_spectrum:
    input:
        catalog="results/{universe}/clean_catalog/",
    output:
        data=directory("results/{universe}/power_spectrum/"),
        manifest="results/{universe}/power_spectrum/.lightcone-manifest.json",
    container:
        ".lightcone/images/lc-ps-d0e1234f.sif"
    params:
        cfg=lambda wc: CFG["power_spectrum"][wc.universe],
    resources:
        mem_mb=32000, runtime=240, slurm_partition="gpu", gpu=1,
    run:
        shell("{params.cfg[recipe]}")
        write_manifest(
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
- **`run:` block, not `shell:`.** The `run:` body is plain Python executing on the host. `shell()` calls inside it are containerized by Snakemake when `container:` is set. This lets the recipe run in the container and `write_manifest()` run on the host, in one atomic rule body, with no CLI subcommand.
- **`container:` references a local SIF** built by `lc build`. The path includes the deterministic content hash (`engine/container.py`), so the rule's code includes that hash literally — Snakemake's `code` rerun-trigger detects image rebuilds for free.
- **All provenance details live in a sidecar JSON** (`.lightcone/snakefile-config.json`) referenced via `params.cfg`. The Snakefile itself stays small. The cfg blob holds recipe text, decisions, and code_version per (output, universe) pair.
- **`resources:` come from astra.yaml.** Each output declares its needs (mem, time, partition, gpu); we translate to Snakemake's resource keys at generation time.

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

There is **no scheduler daemon, no Postgres, no Dask, and no `lc cluster` command.** Snakemake's slurm plugin submits sbatch jobs directly from the head node; staleness state is just files in `.snakemake/`. The `lc cluster start/attach/stop` lifecycle goes away entirely because there is no service to keep alive.

Users who want a single allocation with many job-steps inside it (today's `attach` mode) run `salloc` themselves and invoke `lc run` inside the allocation. `lc run` then auto-detects the environment:

- `FLUX_URI` set → snakemake dispatches to the running Flux instance via our vendored executor (`--executor lightconeflux`).
- `SLURM_JOB_ID` set, no `FLUX_URI` → wrap the snakemake call in `srun --mpi=pmi2 flux start --`, which bootstraps a Flux instance across the allocation; snakemake then dispatches to it.
- Neither → run locally as before.

The vendored executor lives at `src/snakemake_executor_plugin_lightconeflux/` (~100 lines, adapted from `snakemake-executor-plugin-flux`) and adds GPU + multi-node resource mapping that upstream lacks. Listing or canceling running jobs is `squeue` / `scancel` / `flux jobs` / `flux cancel` directly — those are not a `lc` concern.

The entire `engine/clusters/` directory (~1000 LOC of cluster lifecycle, Postgres bootstrap, scheduler management) is **deleted**, not replaced.

---

## Code footprint

| Module | Purpose | Estimated LOC |
|---|---|---:|
| `engine/snakefile.py` | Jinja template + generator from `astra.yaml` | 200 |
| `engine/manifest.py` | `write_manifest()` + read/verify schema | 100 |
| `engine/container.py` | Deterministic image build + hashing (kept) | 200 |
| `engine/status.py` | Walk results, compute status from manifests | 80 |
| `engine/verify.py` | Recompute hashes, validate chain | 100 |
| `engine/profile.py` | Translate `lc target` → snakemake profile YAML | 100 |
| `cli/commands.py` | Click commands; mostly subprocess.run + argument plumbing | 350 |
| **Total** | | **~1130** |

For comparison, today's `engine/` (Dagster + Dask + Postgres + cluster lifecycle + runner) is roughly **3000–3500 LOC**. The redesign cuts it by roughly two thirds.

What gets **deleted**:

- `engine/assets.py` (Dagster asset factory) — gone
- `engine/io_manager.py` (the misnamed pass-through) — gone, was never a real IO manager
- `engine/runner.py` (docker/podman/venv/local dispatch) — gone, Snakemake's `container:` directive handles runtime invocation
- `engine/dask_entrypoint.py` (Dask reconstructable bootstrap) — gone
- `engine/clusters/_pg.py` (Postgres lifecycle) — gone
- `engine/clusters/_local.py`, `_slurm.py`, `_slurm_info.py` — gone; site config lives in snakemake profiles via `engine/profile.py`
- The `dagster`, `dagster-dask`, `dagster-postgres`, `dagster-webserver`, `dagster-docker`, `pixeltable-pgserver`, `dask`, `distributed` dependencies — all dropped from `pyproject.toml`

What gets **added**:

- `snakemake` (core), `snakemake-executor-plugin-slurm`, optionally `snakemake-executor-plugin-kubernetes` — three deps replacing seven.

---

## How requirements from `design_review.md` are met

| Requirement | How |
|---|---|
| **§1.1 Verifiable execution** | Manifest is content-addressed; `data_version` = sha256 of output dir; chain links upstream `data_versions`. Agents cannot fake outputs without producing a valid manifest, and a forged manifest fails `lc verify`. |
| **§1.2 Reproducibility** | `code_version` embedded in rule shell command + Snakemake's `code` rerun-trigger = automatic re-execute on code/container/decision change. External inputs tracked via mtime (default) or sha256 (`--strict-inputs`). |
| **§1.3 Transparent CLI usability** | `lc run`, `lc status`, `lc verify` keep their semantics. The Snakefile is implementation detail. Users who want it can `cat .lightcone/Snakefile` or run `snakemake --dag` directly. |
| **§1.4 Frictionless local + HPC + k8s** | `--executor slurm` / `--executor kubernetes` are existing Snakemake plugins. No services to manage. Profile YAML per target replaces ad-hoc `~/.lightcone/targets/`. |
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
| **Container exec semantics in `run:` blocks.** We rely on Snakemake's documented behavior that `shell()` calls inside a `run:` block are containerized when `container:` is set, while the surrounding Python runs on the host. | Verified in the Snakemake docs and used widely in the wild. If it ever regresses, fallback is to invoke `apptainer exec` explicitly inside the `run:` block — same module, ~5 line change. |
| **Loss of multi-backend fallback.** Today's runner falls back from container → venv → local on failure. Snakemake does not. | This is a feature, not a regression. Silent backend changes destroy reproducibility; we want failures to fail. |
| **Snakemake locks the workdir during a run.** Two `lc run` invocations from two terminals will block each other. | Acceptable. Today's Dagster doesn't isolate that case meaningfully either, and concurrent project runs are rare. If needed, generate per-invocation Snakefile in a tmp workdir. |
| **No unified UI.** Lose Dagster's webserver. | `snakemake --report report.html` covers most of what the UI was used for. `lc status` is the daily driver. The webserver was rarely the right tool anyway. |
| **Slurm plugin polling latency** (40s → 180s backoff). | Acceptable for our workload (recipes are minutes to hours). Tunable per profile. |
| **Snakemake API instability.** | We shell out to the CLI, not the Python API. CLI is stable. |
| **Recipes that need Python data (not files) between steps.** | Out of scope by design (and was never supported by current architecture either). Recipes communicate via files. |

---

## What we explicitly do *not* build

- **Per-rule on-success hook.** Snakemake doesn't have one; we don't need one because the `run:` block *is* the rule body — `write_manifest()` runs after `shell()` on the host.
- **A custom DAG executor.** Snakemake is the executor.
- **A persistent metadata database.** Manifests on disk are sufficient and survive everything.
- **A scheduler daemon.** Snakemake's slurm plugin submits sbatch directly; no service to keep alive.
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

We replace Dagster + Dask + Postgres with Snakemake + a small content-addressed manifest layer. The user-facing surface stays identical. The engine LOC roughly halves. Provenance becomes a real cryptographic property, not a process-boundary policy. The HPC story stops being about bundling services and starts being about generating a profile YAML.

The minimum we have to write ourselves is: a Snakefile generator (~200 lines), a manifest module (~100 lines), a container build/hash module (~200 lines, kept from today), a status walker (~80 lines), a verify routine (~100 lines), and a profile generator (~100 lines). Everything else — orchestration, scheduling, parallelism, cluster submission, container runtime invocation, staleness detection, retries, dry-run, reports — is Snakemake's job, and Snakemake is good at it.

The strongest single argument for this design is not the code reduction. It is that the integrity property we care about (the agent cannot fake an output) becomes a *consequence of how the system is built* — manifests are required Snakemake outputs, manifests are content-addressed, content addresses chain — rather than a policy enforced by a process boundary. That is the property `design_review.md` calls out as the headline requirement, and this is the cleanest way I see to deliver it.
