# Architecture

The whole story in one sentence: **lightcone-cli is a thin shim over
Snakemake that owns provenance.** This page expands that sentence.

## Three subsystems

1. **Snakefile generation** — translate `astra.yaml` into a
   `.lightcone/Snakefile` and a sidecar `snakefile-config.json` keyed by
   `(rule, universe)`. Snakemake handles the rest of execution.
2. **Manifest layer** — a per-output sidecar JSON written *by us* on the
   host immediately after each rule's recipe shell exits. The integrity
   contract lives here.
3. **Cluster management** — `lc run` always dispatches through a Dask
   scheduler whose lifetime equals the run's lifetime. The cluster
   manager picks the right shape (external scheduler / Dask Gateway /
   SLURM / local) on the fly.

The Claude Code plugin (skills + hooks + agents) is the agentic surface
layered on top.

---

## 1. Snakefile generation

Generator: [`lightcone.engine.snakefile.generate`](api/snakefile.md).

For each output in the resolved analysis tree (root + sub-analyses,
expanded by `astra.helpers.resolve_analysis_tree`), the generator emits
one Snakemake rule per output. The rule body is a `run:` block:

```python
rule <name>:
    input:  ...                     # from upstream outputs (sibling rules)
    output:
        data=directory("results/{universe}/<output_id>"),
        manifest="results/{universe}/<output_id>/.lightcone-manifest.json",
    params:
        cfg=lambda wc: CFG["<rule_key>"][wc.universe],
    run:
        run_rule(                                   # engine.runner
            rule_key="<rule_key>",
            universe=wildcards.universe,
            output_dir=Path(output.data),
            inputs={...},
            cfg=dict(params.cfg),
        )
```

`run_rule` runs the pre-rendered (already container-wrapped) shell
command, emits the sentinel-framed narrative lines `lc run` forwards,
writes the manifest on success, and runs the validation hook.

### What goes in `cfg`

`snakefile-config.json` is keyed by `<rule_key> → <universe> → cfg` where
the inner dict carries:

- `shell_command` — the recipe pre-wrapped at generation time. When
  containers are configured, this looks like
  `<runtime> run --rm --pull=never -v "$PWD":"$PWD" -w "$PWD" <image> bash -c '<recipe>'`.
  Snakemake's own `container:` directive and `--sdm apptainer` are
  intentionally *not* used — we own the runtime end-to-end.
- `code_version` — `sha256(recipe + container_image + decisions)`.
  Embedded as a `: lc_code_version=…;` no-op prefix on the shell command
  so it lands in any shell trace.
- `recipe`, `container_image`, `decisions`, `output_id`, `output_type`,
  `universe_id`, `git_sha`, `lc_version`, resolved input paths.

### Why pre-wrap, not Snakemake's `container:`?

Two reasons. First, `--sdm apptainer` adds an extra container layer that
defeats podman-hpc's migrate workflow. Second, registry image resolution
on podman fails for our content-addressed `lc-<name>-<hash>` tags
because they trip `unqualified-search-registries` in `registries.conf`.
We pass `--pull=never` to skip the lookup entirely; that requires
images to be present locally, which is what `lc build` does.

### Staleness detection

The generator does *not* override Snakemake's rerun logic — it just
makes sure drift is visible to it. We default to
`--rerun-triggers code,input,mtime,params`. The `params` trigger is the
one that fires today: `cfg` is per-universe and contains
`code_version`, so any change to recipe / container image / decisions
flows through.

---

## 2. The manifest layer

Module: [`lightcone.engine.manifest`](api/manifest.md). Filename:
`.lightcone-manifest.json` (constant; `SCHEMA_VERSION = 1`).

Every successful rule writes a manifest to its output directory. The
write is atomic (`os.replace` rename); a missing or unparseable manifest
re-runs the rule on the next `lc run`.

### Fields

```json
{
  "schema_version": 1,
  "output_id": "...",
  "universe_id": "baseline",
  "code_version":  "sha256:…",
  "data_version":  "sha256:…",
  "container_image": "lc-myproject-abc123" ,
  "worker_image": null,
  "recipe": "python scripts/compute.py",
  "decisions": {...},
  "input_versions": { "<inp_id>": "sha256:…" },
  "git_sha": "...",
  "git_remote": "...",
  "lc_version": "...",
  "host": "...",
  "slurm_job_id": "...",
  "finished_at": 1700000000.0
}
```

`container_image` is what the spec *declared*. `worker_image` is the
image the Dask Gateway pod that actually ran the recipe was started from
(`LIGHTCONE_WORKER_IMAGE`, provisioned by `lc run`) — `null` on every
other backend. Both fields are additive; older manifests still parse.

### `data_version` exclusions

`sha256_dir()` skips two filenames: `.lightcone-manifest.json` (chicken
and egg) and `.snakemake_timestamp` (Snakemake touches the directory
*after* the rule body completes — including it would make every hash
unreproducible).

### `input_versions` semantics

For each declared recipe input:
- If the input is a sibling output (has its own manifest) →
  `data_version` from that manifest.
- Otherwise treated as external →
  `mtime-size:<ns>-<bytes>` for files, `sha256_dir(...)` for
  directories, `"missing"` for absent paths.

### What `lc verify` checks

- **`tampered_data`** — `sha256_dir()` of the on-disk output no longer
  matches the recorded `data_version`.
- **`broken_chain`** — a recorded `input_versions[id]` no longer matches
  the upstream output's current `data_version`.
- **`missing_manifest`** — the output directory exists but has no
  manifest, or the manifest fails to parse.

### What `lc status` checks

- **`ok`** — manifest present, recomputed `code_version` matches.
- **`stale`** — manifest present but `code_version` drifted (recipe,
  image, or decisions changed).
- **`missing`** — no manifest.
- **`alias`** — output declared without a recipe; materialized only as a
  side effect of an upstream.

`status` reads only manifests. No Snakemake import, no `.snakemake/`
directory required, works on a fresh clone or frozen archive.

---

## 3. Cluster management

Module: [`lightcone.engine.dask_cluster`](api/dask_cluster.md).

`cluster_for_run()` is the only entry point. It is a context manager that
yields the **env overlay** the child snakemake needs to reach the cluster
(the executor plugin lives in a different process), valid for the
duration of the run, across four branches:

1. `DASK_SCHEDULER_ADDRESS` already set → yield as-is. We don't own the
   cluster, we don't tear it down.
2. `DASK_GATEWAY__ADDRESS` set (a JupyterHub / Dask Gateway deployment)
   → **create** a run-scoped Gateway cluster running the project's
   image, scale it adaptively up to the job bound, and shut it down on
   exit. Create-per-run is what makes image updates seamless: a Gateway
   cluster's image is fixed at creation. Gateway schedulers speak a
   `gateway://` comm scheme a bare `Client` can't dial, so this branch
   yields the *cluster name* (`LIGHTCONE_GATEWAY_CLUSTER`) and the
   executor rejoins through the authenticated Gateway API.
3. `SLURM_JOB_ID` set → start an in-process scheduler bound to the
   driver hostname (`SLURMD_NODENAME` or `gethostname()`), then `srun`
   one `dask worker` per node across the allocation. Workers advertise
   the node's resources via Dask abstract resources (`cpus`, `memory`,
   `gpus`). The Snakemake executor plugin maps per-rule
   `cpus_per_task` / `mem_mb` / `gpus_per_task` to per-task constraints.
4. None of the above → `LocalCluster()` sized to the local machine.

Outside the Gateway branch the scheduler is always in-process so its
lifetime equals the run's lifetime: no service to manage, no orphaned
schedulers. The Gateway branch enforces the same contract server-side —
created per run, culled on exit, with the deployment's idle timeout as
the backstop.

Because the Gateway branch realizes containers as *pod images* rather
than by wrapping recipes, one run can honour only one image: `lc run`
rejects a spec resolving to more than one distinct container image there
(`gateway_branch_active()` is the pre-flight predicate).

### The Snakemake executor

Module: [`snakemake_executor_plugin_dask`](api/dask_executor.md).

Snakemake calls `run_job(job)`, we translate it to:

```python
client.submit(
    _run_shell, cmd,
    resources=_build_resources(job),
    pure=False,
    key=f"snakejob-{job.name}-{job.jobid}",
)
```

The worker shells out to the (already container-wrapped) command. There
is no per-rule "executor logic" to write — recipes are wrapped at
generation time, so the worker just runs them.

---

## Scratch & run locking

Module: [`lightcone.engine.scratch`](api/scratch.md).

Before anything else, `lc run` resolves a **scratch root** — where
Snakemake metadata, Dask spill, and the run lock live — in this order:
`LIGHTCONE_SCRATCH`, then `scratch_root` in
`.lightcone/lightcone.yaml`, then the detected site's declared
`scratch_root` (a shell expression like `$SCRATCH`), then the tempdir.

`<project>/.snakemake` is then repointed there by symlink. This matters
on NERSC, where `$HOME` and CFS are DVS-mounted and silently swallow
`flock`, and on a JupyterHub deployment, where a pod's `/tmp` is
pod-local while every worker pod mounts the same NFS `$HOME`.

A non-blocking `flock` on `<scratch>/.lightcone/locks/<project-hash>.run-lock`
is held for the whole run: a second `lc run` on the same project fails
cleanly instead of interleaving Snakemake state updates, and acquiring
it clears any workflow lock a previously crashed run left behind.

---

## Container layer

Module: [`lightcone.engine.container`](api/container.md).

Two surfaces:

- **Build** — `image_identity()` + `build_image()`. The 12-char sha256
  digest covers the Containerfile, every dependency file
  (`requirements.txt`, `pyproject.toml`, `poetry.lock`, `uv.lock`,
  `environment.yml`, …), **and the contents of every `COPY`/`ADD` source
  the Containerfile references** — files hashed directly, directories
  walked recursively (skipping `.git`, `.venv`, `results/`, caches, …).
  A `COPY .` therefore hashes the whole build context. Rebuilds happen
  only when the digest changes.
- **Run-time wrap** — `wrap_recipe()` produces the command string that
  the Snakefile generator embeds into each rule.

One identity, two spellings: `lc-<project>-<digest>` in a local image
store (`compute_image_tag()`), `<registry>/lc-<project>:<digest>` in a
registry (`registry_image_ref()`). `runtime_registry()` decides which
one a runtime uses, so the Snakefile generator and the status walker can
never disagree about an image identity.

Runtime resolution: `~/.lightcone/config.yaml` carries
`container.runtime`
(`auto | docker | podman | podman-hpc | kubernetes | none`). `auto` picks
the first usable in `(podman-hpc, podman, docker)`, skipping docker if
its daemon is unreachable, with the detected site's declared
`container_runtime` moved to the front. `none` is an explicit opt-out —
recipes run on the host. When `auto` falls back to `none` silently,
`lc run` warns that the manifest's `container_image` field will
misrepresent what actually executed.

`kubernetes` is the odd one out and never comes from PATH detection: it
is selected by the `jupyterhub` site (or pinned explicitly), the worker
pod *is* the container so `wrap_recipe()` is a passthrough, images
resolve to registry refs, and builds go through
[`engine.cloudbuild`](api/cloudbuild.md) — GCP Cloud Build, authenticated
by the pod's Workload Identity — instead of a local OCI CLI.

For `podman-hpc`, the build path also runs `podman-hpc migrate <tag>`
so compute nodes can read the image without a registry.

---

## Sub-analysis tree

`astra.yaml` can declare nested `analyses:` pointing to sub-directories
each with their own `astra.yaml`. The full tree is resolved by
`astra.helpers.resolve_analysis_tree()` before any operation.

Output paths follow the analysis layout:

- Root + inline sub-analyses: `results/<universe>/<output_id>/`
- Path-rooted sub-analyses: `<sub_path>/results/<universe>/<output_id>/`

`from:` references on inputs and decisions are resolved by helpers in
[`engine.tree`](api/tree.md). When an output id is ambiguous (the same
name appears in multiple sub-analyses), `lc run` errors and asks for
the qualified `<analysis_id>.<output_id>` form.

---

## Claude Code plugin

The plugin lives at `claude/lightcone/`. It is force-included into the
installed wheel via `pyproject.toml` so `lc init` can find it whether
you're running from source or from PyPI:

```toml
[tool.hatch.build.targets.wheel.force-include]
"claude/lightcone" = "lightcone/cli/claude/lightcone"
```

`lightcone.cli.plugin.get_plugin_source_dir()` does the lookup: bundled
location first, dev location (relative to the repo root) second.

### Permission tiers

`lc init --permissions {yolo,recommended,minimal}` writes a
`.claude/settings.json` from the matching tier in
`PERMISSION_TIERS`. `recommended` (the default) allows the agent to
edit, write, and shell out, but blocks edits to dotfiles, scratch
paths, and `git push`.

### Hooks

The plugin registers Claude Code hooks for venv activation,
auto-validation on save, and integrity-aware "did you forget `lc run`?"
warnings.

---

## Repository at a glance

```text
src/lightcone/                  # PEP 420 namespace package — NO __init__.py
├── cli/                        # Click surface
│   ├── __init__.py             # exposes main()
│   ├── commands.py             # init, run, status, verify, build, export
│   └── plugin.py               # plugin source-dir discovery
├── engine/                     # execution substrate
│   ├── manifest.py             # write_manifest, sha256_dir, code_version
│   ├── snakefile.py            # generate .lightcone/Snakefile from astra.yaml
│   ├── container.py            # image identity, local build + recipe wrap
│   ├── cloudbuild.py           # GCP Cloud Build backend (no local OCI runtime)
│   ├── dask_cluster.py         # cluster lifecycle (external/Gateway/SLURM/local)
│   ├── scratch.py              # scratch root, per-run dirs, run lock
│   ├── status.py               # manifest-driven status walker (no Snakemake)
│   ├── verify.py               # recompute hashes, walk the chain
│   ├── tree.py                 # sub-analysis tree helpers
│   ├── validation.py           # post-recipe output sanity checks
│   ├── wrroc.py                # Workflow Run RO-Crate exporter
│   └── site_registry.py        # known-site defaults; drives scratch + runtime
└── eval/                       # evaluation harness for the agent loop
    ├── cli.py harness.py sandbox.py graders.py build.py report.py models.py

src/snakemake_executor_plugin_dask/   # Snakemake executor → dask.distributed

claude/lightcone/               # Claude Code plugin (force-included into the wheel)
├── skills/                     # lc-new, lc-from-code, lc-from-paper,
│                                # lc-feedback, ralph (+ bundle siblings);
│                                # reference skills: astra, lc-cli
├── agents/                     # lc-extractor (literature subagent)
├── templates/                  # project CLAUDE.md template
└── scripts/                    # session hooks (bash): venv, validate-on-save, session-start primer

tests/                          # pytest, mirrors src/
pyproject.toml                  # hatchling + hatch-vcs; ASTRA + Snakemake as deps
```

The `lightcone.*` namespace is a PEP 420 implicit namespace package.
**Do not add `src/lightcone/__init__.py`** — that would turn it into a
regular package and break coexistence with future sibling distributions
(`lightcone-ui`, etc.). Any new `lightcone-*` package must live under
`src/lightcone/<name>/` and ship only its own subpackage.

---

## Execution flow

```text
astra.yaml ── snakefile.generate() ──► .lightcone/Snakefile + .lightcone/snakefile-config.json
                                              │
                                       snakemake -s … -d … --executor dask
                                              │
                       ┌──────────────────────┼──────────────────────┐
                       │                      │                      │
                  DAG resolution         per-rule run:           dask scheduler
                  (Snakemake)            shell(recipe)           (LocalCluster /
                                         + write_manifest()       SLURM-srun /
                                                                  Dask Gateway /
                                                                  external)
                       │
                       └─► results/<u>/<o>/data
                           results/<u>/<o>/.lightcone-manifest.json
```

The Gateway branch differs in where the container comes from: the worker
pod is started *from* the project image, so the recipe is not wrapped —
`lc run` builds the image (Cloud Build), creates the cluster with it, and
the pod executes the recipe natively.

What Snakemake owns (we don't write it): DAG construction, topological
execution, parallelism, dry-run, locking, retry, log capture,
per-rule resources, `--rerun-triggers` for staleness detection.

What we own: a Snakefile generator, the manifest layer (write/read/verify),
a status walker, a verify routine, the Dask cluster manager, the
container-runtime layer, and a Snakemake executor plugin that submits
each rule to a Dask scheduler.

--- 

## Configuration files

| File | Scope | Purpose |
|------|-------|---------|
| `astra.yaml` | Project | The spec. Inputs, outputs, recipes, decisions, sub-analyses. |
| `.lightcone/Snakefile` | Project (generated) | Auto-generated by `lc run`. Don't edit. |
| `.lightcone/snakefile-config.json` | Project (generated) | Per-`(rule, universe)` config. |
| `.lightcone/lightcone.yaml` | Project | Written by `lc init` (`target: local`, plus `scratch_root` when `--scratch` is passed). `scratch_root` is read by [`engine.scratch`](api/scratch.md) on every `lc run`; `target` is inert. |
| `~/.lightcone/config.yaml` | User | `container.runtime`. Auto-created with `auto` on the first `lc` invocation. |
| `.claude/settings.json` | Project | Claude Code permissions. |

The `dagster.yaml` and `~/.lightcone/targets/*.yaml` files referenced in
older docs are no longer used — historical residue.
