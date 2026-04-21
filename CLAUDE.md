# CLAUDE.md

## Project Overview

**lightcone-cli** is Lightcone Research's agentic layer for ASTRA (Agentic Schema for Transparent Research Analysis). It ships the `lc` executable and Claude Code skills/hooks used during interactive analysis work.

- **ASTRA** = pure specification: schema, validation, prior insights & findings, evidence verification, helpers, minimal CLI
- **lightcone-cli** = agentic layer: Claude Code skills, project scaffolding, Dagster execution, HPC targets, container builds, telemetry

lightcone-cli depends on ASTRA. The `astra` CLI handles spec operations; the `lc` CLI handles execution and agent operations.

### Namespace contract

`lightcone-cli` ships the `lightcone.*` namespace via PEP 420 implicit namespace packages. **`src/lightcone/` must not contain an `__init__.py`** — that would turn the namespace into a regular package and break coexistence with future sibling distributions (`lightcone-ui`, etc.).

Any new `lightcone-*` package must:

1. Use src-layout (`src/lightcone/<name>/…`).
2. Not create `src/lightcone/__init__.py`.
3. Ship only its own subpackage under `src/lightcone/<name>/`.

## Repository Structure

```
src/lightcone/              # namespace — NO __init__.py
├── cli/                    # Click surface
│   ├── __init__.py         # exposes main()
│   ├── commands.py         # all Click commands (init, run, build, status, dev, target, setup, update)
│   ├── plugin.py           # get_plugin_source_dir — leaf module (no imports from commands.py)
│   └── claude/             # force-included Claude plugin bundle (in installed wheel only)
├── engine/                 # execution substrate — Dagster + HPC + containers
│   ├── __init__.py
│   ├── assets.py           # Asset factory — turns astra.yaml recipes into Dagster assets
│   ├── container.py        # Content-addressed container builds (Docker, podman-hpc)
│   ├── io_manager.py       # Maps (output, universe) → results/{universe}/{output}/
│   ├── runner.py           # Execution backends: Docker, local, SLURM
│   ├── site_registry.py    # Known HPC site defaults (Perlmutter, etc.)
│   ├── status.py           # Materialization status queries
│   ├── targets.py          # Target config management (~/.lightcone/targets/)
│   └── tree.py             # Sub-analysis tree traversal
└── eval/                   # Quantitative eval harness (top-level; peer of cli/engine)
    ├── cli.py              # `lc eval` subcommand group (registered by lightcone.cli.commands)
    ├── harness.py, sandbox.py, graders.py, build.py, report.py, models.py

claude/lightcone/           # Claude plugin source — force-included into the wheel
├── skills/                 # lc-new, lc-build, lc-verify, lc-migrate, lc-feedback
├── agents/                 # lc-extractor
├── guides/                 # astra-reference, lightcone-cli-reference, ui-brand
├── templates/              # Project CLAUDE.md template
├── hooks/                  # Langfuse telemetry hooks (Python)
└── scripts/                # Session hooks (bash): venv activation, validate-on-save, status display

tests/                      # pytest — mirrors src/ structure
pyproject.toml              # hatchling + hatch-vcs, ASTRA as git dep
```

### Engine/CLI dependency contract

`lightcone.engine` must import nothing from `lightcone.cli` or `lightcone.eval`. This keeps the door open to carving `lightcone-engine` into its own PyPI dist when a future `lightcone-ui` needs it — no code changes required.

## Development Commands

```bash
uv sync --group dev   # installs pytest, ruff, mypy into the uv env
uv run pytest
uv run ruff check src/ tests/
uv run mypy src/
```

A `justfile` is available for common tasks — run `just` to see all recipes:

```bash
just test          # run pytest
just lint          # ruff + mypy
just docs          # build the documentation site
just docs-serve    # live preview at http://127.0.0.1:8000
just install       # uv sync --all-groups
```

## Documentation

Maintainer documentation lives in `docs/` and is built with [Zensical](https://zensical.org). Configuration is in `zensical.toml`.

```
docs/
├── index.md           # Overview, repo structure, key invariants
├── architecture.md    # Dagster integration, container mgmt, plugin system
├── cli/               # One page per lc command
├── api/               # One page per Python module
├── skills/            # Skill reference + authoring guide
├── telemetry/         # Langfuse hooks, session lifecycle, opt-out
├── hpc/               # SLURM, site registry, targets, container builds
└── contributing/      # Dev setup, adding backends/sites, testing
```

Dependencies are declared in `pyproject.toml` under `[dependency-groups].docs` and managed with `uv`:

```bash
just docs-serve     # syncs docs group then serves with live reload at http://127.0.0.1:8000
just docs-strict    # build with --strict (accepted flag, not yet enforced by zensical)
```

## Architecture & Data Flow

```
astra.yaml → build_definitions() → Dagster assets → ASTRAContainerRunner → results/{universe}/{output}/
                                         ↑                    ↑
                                    ASTRAIOManager        Docker / local / SLURM
```

- `build_definitions()` (`lightcone.engine.assets`) loads astra.yaml, creates one Dagster asset per output with a recipe
- Asset dependencies come from `recipe.inputs` — Dagster resolves execution order
- `ASTRAContainerRunner` (`lightcone.engine.runner`) dispatches to Docker, local subprocess, or SLURM based on target config
- Docker backend falls back to local execution on failure (with warning)
- SLURM backend generates sbatch scripts, submits via `sbatch`, polls via `sacct`/`squeue`

## Key Invariants

**Spec & execution:**
- `astra.yaml` is the single source of truth — all inputs, outputs, recipes, decisions, containers
- Output paths are always `results/{universe_id}/{output_id}/` — enforced by IO manager, no customization
- Container is a single string: image name (e.g., `python:3.9`) is pulled; file path (e.g., `Containerfile`) is built. No `container_build` dict — runtime detects via file existence.
- Container image tags are deterministic: SHA256(Containerfile + dependency files) → `lc-{name}-{hash}`
- Universe decision parameters are injected as CLI args: `--key value` passed to recipe commands
- Per-recipe container specs override analysis-level defaults

**Config resolution (used everywhere):**
- Target: `--target` flag > `.lightcone/lightcone.yaml` > `~/.lightcone/config.yaml` > `"local"`
- Permission tier: `--permissions` flag > saved default in `~/.lightcone/config.yaml` > interactive prompt
- Most commands require `astra.yaml` in cwd; exceptions: `setup`, `target`

**Plugin system:**
- Skills, hooks, and scripts are bundled in the wheel (`claude/lightcone/` → `lightcone/cli/claude/lightcone/`)
- `lc init` copies them into each project's `.claude/` directory
- Plugin source discovery lives in `lightcone.cli.plugin.get_plugin_source_dir` — tries bundled location first, falls back to dev location (`claude/lightcone/` at repo root)
- Bash scripts must be chmod +x

## CLI Patterns

All commands use Click. Key patterns:
- `@main.command()` for top-level commands, `@main.group()` for subgroups (`target`)
- Target/config resolution is shared logic, not per-command
- `lc setup` auto-triggers if `~/.lightcone/config.yaml` doesn't exist when running other commands
- Three permission tiers: `yolo` (all allowed), `recommended` (workflow allowed), `minimal` (read-only)

## Extending the Codebase

| To... | Read | Key patterns |
|---|---|---|
| Add a CLI command | `src/lightcone/cli/commands.py` | `@main.command()`, config resolution, `click.echo` with Rich |
| Add an HPC site | `src/lightcone/engine/site_registry.py` | Add to `SITE_DEFAULTS` dict with hostname_patterns, node_types, qos_options |
| Add an execution backend | `src/lightcone/engine/runner.py` | Add `_run_{backend}()` method, update `execute()` dispatch |
| Add container features | `src/lightcone/engine/container.py` | `DEPENDENCY_FILES` tuple, `compute_image_tag()`, build/resolve functions |
| Create a skill | `claude/lightcone/skills/` | SKILL.md with YAML frontmatter (`name`, `description`, `allowed-tools`) |
| Add a telemetry hook | `claude/lightcone/hooks/` | Follow `langfuse_hook.py` pattern: read JSON payload, emit to Langfuse |

## Test Patterns

- CLI tests: `CliRunner().invoke(main, ["command", ...])` — check exit code, output, file side effects
- Asset tests: call `build_asset_definitions(spec, runner=mock_runner)` — verify keys, deps, metadata
- Runner tests: create runner with tmp project root, call `execute()` — verify exit code and metadata
- Common fixture: `_fake_config` monkeypatches `get_config_path` to prevent auto-setup wizard in tests
- Integration tests in `test_integration.py` and `test_cli_run.py` cover end-to-end flows

## Conventions

- Ruff for linting (E, F, I, N, W, UP), line length 100, target Python 3.11
- mypy strict mode with `namespace_packages = true`, `explicit_package_bases = true`
- Status states: `"ok"` (materialized), `"pending"` (has recipe, not run), `"no_recipe"` (declared, no recipe)
- SLURM scripts/output stored in `results/.slurm/`
- Dagster instance storage at `results/.dagster/` (SQLite)
- Telemetry opt-out: `TRACE_TO_LANGFUSE=false`
