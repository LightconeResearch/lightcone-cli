# CLAUDE.md

## Project Overview

**lightcone-cli** is Lightcone Research's agentic layer for ASTRA (Agentic Schema for Transparent Research Analysis). It ships the `lc` executable and Claude Code skills/hooks used during interactive analysis work.

- **ASTRA** = pure specification: schema, validation, prior insights & findings, evidence verification, helpers, minimal CLI
- **lightcone-cli** = agentic layer: Claude Code skills, project scaffolding, **Snakemake-based execution**, container builds

lightcone-cli depends on ASTRA. The `astra` CLI handles spec operations; the `lc` CLI handles execution and agent operations.

### Namespace contract

`lightcone-cli` ships the `lightcone.*` namespace via PEP 420 implicit namespace packages. **`src/lightcone/` must not contain an `__init__.py`** — that would turn the namespace into a regular package and break coexistence with future sibling distributions (`lightcone-ui`, etc.).

Any new `lightcone-*` package must:

1. Use src-layout (`src/lightcone/<name>/…`).
2. Not create `src/lightcone/__init__.py`.
3. Ship only its own subpackage under `src/lightcone/<name>/`.

## Architecture

The execution layer is a thin shim over Snakemake. The integrity layer (per-output content-addressed manifests) is the only thing we own substantively.

```
astra.yaml ── snakefile generator ──> .lightcone/Snakefile
                                            │
                            snakemake (CLI subprocess)
                                            │
            ┌───────────────────────────────┼───────────────────────────────┐
            │              │                │                │              │
       DAG resolution  staleness     cluster submission  container exec   conda
       (Snakemake)     (mtime+code)  (slurm plugin)      (apptainer/docker)
            │
            └─── per-rule run: block: shell() recipe + write_manifest()
                                            │
                                  results/<u>/<o>/...
                                  results/<u>/<o>/.lightcone-manifest.json
```

**What Snakemake owns** (we do not write code for any of this): DAG construction, topological execution, parallelism (`--cores`, `--jobs`), cluster submission (`snakemake-executor-plugin-slurm`), per-rule resources, profiles, dry-run, DAG visualization, staleness detection (`--rerun-triggers`), locking, log capture, retry, container runtime invocation.

**What we own**: a Snakefile generator, the manifest layer (write/read/verify), a status walker, and a verify routine.

## Repository Structure

```
src/lightcone/              # namespace — NO __init__.py
├── cli/                    # Click surface
│   ├── __init__.py         # exposes main()
│   ├── commands.py         # init, run, status, verify, build
│   ├── plugin.py           # get_plugin_source_dir
│   └── claude/             # force-included Claude plugin bundle (in installed wheel only)
├── engine/                 # execution substrate — Snakemake-based
│   ├── __init__.py
│   ├── manifest.py         # write_manifest, sha256_dir, code_version — the integrity layer
│   ├── snakefile.py        # generates .lightcone/Snakefile from astra.yaml
│   ├── container.py        # Content-addressed container builds (Docker, podman-hpc, apptainer)
│   ├── status.py           # Manifest-driven status walker (no Snakemake import)
│   ├── verify.py           # Recompute hashes; validate provenance chain
│   ├── tree.py             # Sub-analysis tree traversal (kept from before)
│   ├── validation.py       # Post-materialization output shape checks
│   └── site_registry.py    # Known HPC site defaults (Perlmutter, etc.)
└── eval/                   # Quantitative eval harness (top-level; peer of cli/engine)
    ├── cli.py              # `lc eval` subcommand group
    ├── harness.py, sandbox.py, graders.py, build.py, report.py, models.py

claude/lightcone/           # Claude plugin source — force-included into the wheel
├── skills/                 # lc-new, lc-from-code, lc-from-paper,
│                            # lc-feedback, ralph;
│                            # paper-reproduction bundle: lc-from-paper (entry),
│                            # ralph (loop substrate), narrative,
│                            # paper-extraction, figure-comparison,
│                            # check-sentence-by-sentence
│                            # (see skills/README.md for the full bundle map)
├── agents/                 # lc-extractor
├── templates/              # Project CLAUDE.md template
└── scripts/                # Session hooks (bash): venv activation, validate-on-save, session-start primer

tests/                      # pytest — mirrors src/ structure
pyproject.toml              # hatchling + hatch-vcs, ASTRA + Snakemake as deps
```

## Development Commands

```bash
uv sync --group dev   # installs pytest, ruff, mypy
uv run pytest
uv run ruff check src/ tests/
uv run mypy src/
```

A `justfile` is available for common tasks — run `just` to see all recipes:

```bash
just test          # run pytest
just lint          # ruff + mypy
just docs          # build the documentation site
```

## Architecture & Data Flow

```
astra.yaml ── snakefile.generate() ──> .lightcone/Snakefile + .lightcone/snakefile-config.json
                                            │
                                    snakemake -s ... -d ...
                                            │
                                      per-rule run:
                                            │
                       shell(recipe)  ────────────────► write_manifest()
                       (in container if container: set;     (host-side)
                        Snakemake handles invocation)
                                            │
                                  results/<u>/<o>/data.txt
                                  results/<u>/<o>/.lightcone-manifest.json
```

- `snakefile.generate(project, universes=[...])` reads `astra.yaml`, writes `.lightcone/Snakefile`, and writes a sidecar JSON keyed by `(rule, universe)` containing the recipe text, container image, decisions, and precomputed `code_version`.
- The Snakefile body for each rule is a `run:` block: `shell(params.cfg["recipe"])` then `write_manifest(...)`.
- `code_version = sha256(recipe + container_image + decisions)`. Embedded in the rule's shell command literally so Snakemake's built-in `code` rerun-trigger detects drift.
- `data_version = sha256_dir(output_dir)`. Written into the manifest after the recipe completes; used by `lc verify` to detect tampering. Excludes the manifest file itself and `.snakemake_timestamp`.
- The manifest is a *declared output* of every rule. A missing manifest causes Snakemake to re-run the rule, blocking the agent-faked-file scenario.

## Key Invariants

**Spec & execution:**
- `astra.yaml` is the single source of truth — all inputs, outputs, recipes, decisions, containers
- Output paths are always `results/<universe>/<output>/` for root and inline sub-analyses; `<sub_path>/results/<universe>/<output>/` for path-rooted sub-analyses
- Container image hashes are deterministic: SHA256(Containerfile + dependency files) → `lc-<name>-<hash>`
- The Snakefile and snakefile-config.json are regenerated on every `lc run` — never edit them by hand

**Integrity:**
- Every materialized output has `<output_dir>/.lightcone-manifest.json` recording code_version, data_version, container, recipe, decisions, input_versions, git_sha, lc_version, host
- `lc verify` recomputes data_version and walks the chain; failures surface as `tampered_data`, `broken_chain`, or `missing_manifest`
- `lc status` reads only manifests — works offline, no Snakemake or DB needed

**CLI surface:**
- `lc init` — scaffold project with .claude/, CLAUDE.md, .gitignore, .lightcone/, results/, universes/
- `lc run [outputs...]` — generate Snakefile, invoke snakemake
- `lc status` — manifest-driven status report
- `lc verify` — chain integrity check
- `lc build` — pre-build container images from Containerfiles

Global config (`~/.lightcone/config.yaml`) is auto-created with defaults on first invocation.

## Extending the Codebase

| To... | Read | Key patterns |
|---|---|---|
| Add a CLI command | `src/lightcone/cli/commands.py` | `@main.command()`, project discovery via `_project_root()` |
| Change manifest semantics | `src/lightcone/engine/manifest.py` + `tests/test_manifest.py` | Bump `SCHEMA_VERSION`; add a test |
| Change Snakefile shape | `src/lightcone/engine/snakefile.py` + `tests/test_snakefile.py` | Includes a `snakemake -n` parse test |
| Add container features | `src/lightcone/engine/container.py` | `compute_image_tag()`, build/resolve functions |
| Create a skill | `claude/lightcone/skills/` | SKILL.md with YAML frontmatter (`name`, `description`, `allowed-tools`) |

## Test Patterns

- `tests/test_manifest.py` — pure-function tests for the integrity layer
- `tests/test_snakefile.py` — generator tests; final test runs `snakemake -n` on the output
- `tests/test_status.py` / `tests/test_verify.py` — end-to-end against a tmp project
- `tests/test_cli.py` — Click `CliRunner().invoke(main, [...])` patterns

## Conventions

- Ruff for linting (E, F, I, N, W, UP), line length 100, target Python 3.11
- mypy strict mode with `namespace_packages = true`, `explicit_package_bases = true`
- Manifest filename is fixed: `.lightcone-manifest.json` (don't change without bumping `SCHEMA_VERSION`)
- Snakemake's `directory()` outputs require excluding `.snakemake_timestamp` from the hash
