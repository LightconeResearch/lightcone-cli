# Architecture

## Overview

Prism bridges an ASTRA specification (`astra.yaml`) and actual execution on heterogeneous compute backends (local, Docker, SLURM). It does this through three main subsystems:

1. **Dagster integration** — translates the ASTRA spec into a directed-acyclic graph (DAG) of assets, then materialises them in dependency order.
2. **Container management** — resolves and builds content-addressed Docker/Podman images from `Containerfile` specs.
3. **Claude Code plugin** — injects skills, hooks, and scripts into each project's `.claude/` directory so that Claude Code can operate as a research agent.

---

## Dagster integration

### Asset factory (`dagster/assets.py`)

`build_definitions()` is the main entry point. It:

1. Loads and resolves the full `astra.yaml` (including sub-analyses).
2. Creates one `@dg.asset` per output that has a `recipe` block.
3. Wires dependencies: `recipe.inputs` → Dagster `deps`.
4. Attaches the configured `ASTRAContainerRunner` as execution logic.

Asset keys use a three-level hierarchy: `[universe_id, analysis_id, output_id]` for sub-analysis outputs, and `[universe_id, output_id]` for root-level outputs. This hierarchy is visible in the Dagster UI (`prism dev`).

### IO manager (`dagster/io_manager.py`)

`ASTRAIOManager` enforces the canonical output path convention:

```
results/{universe_id}/{output_id}/
```

Scripts are expected to write their outputs to `$ASTRA_OUTPUT_DIR`, which the runner sets to the appropriate path before execution. The IO manager never customises paths.

### Runner (`dagster/runner.py`)

`ASTRAContainerRunner.execute()` dispatches to one of four backends:

| Backend | When selected | Fallback |
|---------|--------------|---------|
| `docker` / `podman` | Container spec available, runtime detected | Falls back to `venv` on failure |
| `venv` | No container available, `.venv/` present | Falls back to `local` if no venv |
| `local` | Explicit `backend: local` in target config | — |
| `slurm` | Target config sets `backend: slurm` | — |

The Docker backend mounts the project root at `/workspace` so scripts can use relative paths. The SLURM backend generates a complete `sbatch` script, submits it, and polls `sacct`/`squeue` until the job finishes.

Universe decisions are always injected as CLI arguments: `--decision_key decision_value`. Scripts access them via `argparse` or equivalent.

---

## Container management (`container.py`)

Container specs in `astra.yaml` are a single string. The runtime distinguishes:

- **Pre-built image** — string does not correspond to an existing file (e.g. `python:3.9`). Pulled on demand.
- **Containerfile build** — string resolves to a file path (e.g. `Containerfile`, `containers/Dockerfile`).

Image tags are content-addressed:

```python
tag = f"prism-{project_name}-{sha256(Containerfile + dependency_files)[:12]}"
```

This means rebuilds only happen when the `Containerfile` or dependency files (`requirements.txt`, `pyproject.toml`, etc.) actually change.

For SLURM/`podman-hpc` targets, `resolve_container_for_slurm()` additionally migrates images to the site-specific container cache format.

---

## Claude Code plugin

### Structure

The plugin lives in `claude/prism/` and is bundled into the Python wheel via `hatch-vcs` force-include directives. When `prism init` runs, it copies the plugin into the project's `.claude/` directory.

```
.claude/
├── settings.json          # Permissions + hook registrations
├── settings.local.json    # Telemetry env vars (Langfuse keys)
├── skills/                # Claude Code slash commands
├── agents/                # prism-extractor subagent
├── guides/                # Reference docs loaded by skills
├── hooks/                 # Python hooks for Langfuse telemetry
└── scripts/               # Bash hooks for session lifecycle
```

### Permission tiers

Three tiers configure how much Claude can do autonomously:

| Tier | Allowed | Denied |
|------|---------|--------|
| `yolo` | Everything (Bash, Edit, Read, Write, MCP) | Nothing |
| `recommended` | Read, Edit, Write, Bash, WebSearch/Fetch | SSH/AWS dotfiles, HPC scratch paths, `sudo`, `git push` |
| `minimal` | Read only | Everything else |

Site-specific deny rules (e.g. Perlmutter scratch paths) are merged in automatically based on the target's hostname.

### Hooks lifecycle

Claude Code fires hooks at defined points in a session. Prism registers:

| Event | Script/Hook | Purpose |
|-------|-------------|---------|
| `SessionStart` | `activate-venv.sh` | Activate project `.venv` |
| `SessionStart` | `session-start.sh` | Show ASTRA summary, detect crash recovery |
| `PreToolUse` | `langfuse_session_init_hook.py` | Create Langfuse trace ID before first tool |
| `PostToolUse` (Write/Edit) | `validate-on-save.sh` | Run `astra validate` on every save |
| `PostToolUse` (Bash) | `check-prism-run.sh` | Warn if Python run directly instead of via `prism run` |
| `PostToolUse` (Bash) | `langfuse_git_commit_hook.py` | Attach git metadata to Langfuse spans |
| `Stop` / `SessionEnd` | `langfuse_hook.py` | Emit full session trace to Langfuse |

---

## Sub-analysis tree

A project can have nested `analyses:` entries in `astra.yaml`, each pointing to a sub-directory with its own `astra.yaml`. The full tree is resolved by `astra.helpers.resolve_analysis_tree()` before any asset or status operation.

Asset keys and status keys use a `analysis_id/output_id` qualified notation for sub-analysis outputs. The `prism status` command displays these as a Rich tree.

---

## Configuration files

| File | Scope | Purpose |
|------|-------|---------|
| `astra.yaml` | Project | Spec: inputs, outputs, recipes, decisions |
| `.prism/prism.yaml` | Project | Target name for this project |
| `.prism/dagster.yaml` | Project | Dagster SQLite instance path |
| `~/.prism/config.yaml` | User | Default target, permission tier, extraction model |
| `~/.prism/targets/{name}.yaml` | User | Connection + scheduler config for each target |
