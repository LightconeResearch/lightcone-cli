# CLAUDE.md

## Project Overview

Prism is Lightcone Research's agentic layer for ASTRA (Agentic Schema for Transparent Research Analysis).

- **ASTRA** = pure specification: schema, validation, insights, evidence verification, helpers, minimal CLI
- **Prism** = agentic layer: Claude Code skills, project scaffolding, Dagster execution, HPC targets, container builds, telemetry

Prism depends on ASTRA. The `astra` CLI handles spec operations; the `prism` CLI handles execution and agent operations.

## Repository Structure

```
src/prism/
├── cli.py              # Click CLI (init, run, build, status, dev, target, setup)
├── container.py         # Content-addressed container builds (Docker, podman-hpc)
└── dagster/
    ├── assets.py        # Asset factory — turns astra.yaml recipes into Dagster assets
    ├── io_manager.py    # Maps (output, universe) → results/{universe}/{output}/
    ├── runner.py         # Execution backends: Docker, local, SLURM
    ├── site_registry.py # Known HPC site defaults (Perlmutter, etc.)
    ├── status.py        # Materialization status queries
    └── targets.py       # Target config management (~/.prism/targets/)

claude/prism/            # Claude Code plugin (bundled into wheel via hatch force-include)
├── skills/             # prism-new, prism-build, prism-verify, prism-feedback
├── templates/          # Project CLAUDE.md template
├── hooks/              # Langfuse telemetry hooks (Python)
├── scripts/            # Session hooks (bash): venv activation, validate-on-save, status display
└── ui-brand.md         # Shared visual formatting conventions for skills

tests/                   # pytest — mirrors src/ structure
pyproject.toml           # hatchling + hatch-vcs, ASTRA as git dep
```

## Development Commands

```bash
pip install -e ".[dev]"
pytest
ruff check src/ tests/
mypy src/
```

## Architecture & Data Flow

```
astra.yaml → build_definitions() → Dagster assets → ASTRAContainerRunner → results/{universe}/{output}/
                                         ↑                    ↑
                                    ASTRAIOManager        Docker / local / SLURM
```

- `build_definitions()` (assets.py) loads astra.yaml, creates one Dagster asset per output with a recipe
- Asset dependencies come from `recipe.inputs` — Dagster resolves execution order
- `ASTRAContainerRunner` (runner.py) dispatches to Docker, local subprocess, or SLURM based on target config
- Docker backend falls back to local execution on failure (with warning)
- SLURM backend generates sbatch scripts, submits via `sbatch`, polls via `sacct`/`squeue`

## Key Invariants

**Spec & execution:**
- `astra.yaml` is the single source of truth — all inputs, outputs, recipes, decisions, containers
- Output paths are always `results/{universe_id}/{output_id}/` — enforced by IO manager, no customization
- Container image tags are deterministic: SHA256(Containerfile + dependency files) → `prism-{name}-{hash}`
- Universe decision parameters are injected as CLI args: `--key value` passed to recipe commands
- Per-recipe container specs override analysis-level defaults

**Config resolution (used everywhere):**
- Target: `--target` flag > `prism.yaml` > `~/.prism/config.yaml` > `"local"`
- Permission tier: `--permissions` flag > saved default in `~/.prism/config.yaml` > interactive prompt
- Most commands require `astra.yaml` in cwd; exceptions: `setup`, `target`

**Plugin system:**
- Skills, hooks, and scripts are bundled in the wheel (`claude/prism/` → `prism/claude/prism/`)
- `prism init` copies them into each project's `.claude/` directory
- Plugin source discovery: tries bundled location first, falls back to dev location (`../claude/prism`)
- Bash scripts must be chmod +x

## CLI Patterns

All commands use Click. Key patterns:
- `@main.command()` for top-level commands, `@main.group()` for subgroups (`target`)
- Target/config resolution is shared logic, not per-command
- `prism setup` auto-triggers if `~/.prism/config.yaml` doesn't exist when running other commands
- Three permission tiers: `yolo` (all allowed), `recommended` (workflow allowed), `minimal` (read-only)

## Extending the Codebase

| To... | Read | Key patterns |
|---|---|---|
| Add a CLI command | `cli.py` | `@main.command()`, config resolution, `click.echo` with Rich |
| Add an HPC site | `site_registry.py` | Add to `SITE_DEFAULTS` dict with hostname_patterns, node_types, qos_options |
| Add an execution backend | `runner.py` | Add `_run_{backend}()` method, update `execute()` dispatch |
| Add container features | `container.py` | `DEPENDENCY_FILES` tuple, `compute_image_tag()`, build/resolve functions |
| Create a skill | `claude/prism/skills/` | SKILL.md with YAML frontmatter (`name`, `description`, `allowed-tools`) |
| Add a telemetry hook | `claude/prism/hooks/` | Follow `langfuse_hook.py` pattern: read JSON payload, emit to Langfuse |

## Test Patterns

- CLI tests: `CliRunner().invoke(main, ["command", ...])` — check exit code, output, file side effects
- Asset tests: call `build_asset_definitions(spec, runner=mock_runner)` — verify keys, deps, metadata
- Runner tests: create runner with tmp project root, call `execute()` — verify exit code and metadata
- Common fixture: `_fake_config` monkeypatches `get_config_path` to prevent auto-setup wizard in tests
- Integration tests in `test_integration.py` and `test_cli_run.py` cover end-to-end flows

## Conventions

- Ruff for linting (E, F, I, N, W, UP), line length 100, target Python 3.11
- mypy strict mode
- Status states: `"ok"` (materialized), `"pending"` (has recipe, not run), `"no_recipe"` (declared, no recipe)
- SLURM scripts/output stored in `results/.slurm/`
- Dagster instance storage at `results/.dagster/` (SQLite)
- Telemetry opt-out: `TRACE_TO_LANGFUSE=false`
