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
   manager picks the right shape (local / SLURM / external) on the fly.

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
        shell('printf "▶ <rule_key> [%s]\\n" "{wildcards.universe}" >&2')
        shell(params.cfg["shell_command"])           # the recipe (already container-wrapped)
        write_manifest(output_dir=Path(output.data), inputs={...}, cfg=params.cfg)
        for w in validate_output(...): print(f"⚠ {w}", file=sys.stderr)
```

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
  "recipe": "python scripts/compute.py",
  "decisions": {...},
  "input_versions": { "<inp_id>": "sha256:…" },
  "git_sha": "...",
  "lc_version": "...",
  "host": "...",
  "slurm_job_id": "...",
  "finished_at": 1700000000.0
}
```

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

`cluster_for_run()` is the only entry point. It is a context manager
that yields a Dask scheduler address valid for the duration of the run,
across three branches:

1. `DASK_SCHEDULER_ADDRESS` already set → yield as-is. We don't own the
   cluster, we don't tear it down.
2. `SLURM_JOB_ID` set → start an in-process scheduler bound to the
   driver hostname (`SLURMD_NODENAME` or `gethostname()`), then `srun`
   one `dask worker` per node across the allocation. Workers advertise
   the node's resources via Dask abstract resources (`cpus`, `memory`,
   `gpus`). The Snakemake executor plugin maps per-rule
   `cpus_per_task` / `mem_mb` / `gpus_per_task` to per-task constraints.
3. Neither → `LocalCluster()` sized to the local machine.

The scheduler is always in-process so its lifetime equals the run's
lifetime: no service to manage, no orphaned schedulers.

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

## Container layer

Module: [`lightcone.engine.container`](api/container.md).

Two surfaces:

- **Build** — `compute_image_tag()` + `build_image()`. Tags are
  `lc-<project>-<sha256[:12]>` over the Containerfile and dependency
  files (`requirements.txt`, `pyproject.toml`, `poetry.lock`,
  `Pipfile.lock`, …). Rebuilds happen only when the hash changes.
- **Run-time wrap** — `wrap_recipe()` produces the command string that
  the Snakefile generator embeds into each rule.

Runtime resolution: `~/.lightcone/config.yaml` carries
`container.runtime` (`auto | docker | podman | podman-hpc | none`).
`auto` picks the first usable in `(podman, docker, podman-hpc)`,
skipping docker if its daemon is unreachable. `none` is an explicit
opt-out — recipes run on the host. When `auto` falls back to `none`
silently, `lc run` warns that the manifest's `container_image` field
will misrepresent what actually executed.

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
│   ├── container.py            # docker/podman/podman-hpc build + recipe wrap
│   ├── dask_cluster.py         # cluster lifecycle (local/SLURM/external)
│   ├── status.py               # manifest-driven status walker (no Snakemake)
│   ├── verify.py               # recompute hashes, walk the chain
│   ├── tree.py                 # sub-analysis tree helpers
│   ├── validation.py           # post-recipe output sanity checks
│   └── site_registry.py        # vestigial; not imported by active code
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
                                                                  external)
                       │
                       └─► results/<u>/<o>/data
                           results/<u>/<o>/.lightcone-manifest.json
```

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
| `.lightcone/lightcone.yaml` | Project | Tiny scratchpad — currently writes only `target: local`. Not consumed by today's code. |
| `~/.lightcone/config.yaml` | User | `container.runtime`. |
| `.claude/settings.json` | Project | Claude Code permissions. |

The `dagster.yaml` and `~/.lightcone/targets/*.yaml` files referenced in
older docs are no longer used — historical residue.
