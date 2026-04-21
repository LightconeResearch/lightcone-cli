# lightcone-cli — Maintainer Documentation

**lightcone-cli** is Lightcone Research's agentic execution layer for **ASTRA** (Agentic Schema for Transparent Research Analysis).

## Separation of concerns

| Layer | Package | Role |
|-------|---------|------|
| **ASTRA** | `astra-tools` | Pure specification: schema, validation, prior insights, evidence helpers, minimal CLI |
| **lightcone-cli** | `lightcone-cli` | Agentic layer: Claude Code skills, project scaffolding, Dagster execution, HPC targets, telemetry |

lightcone-cli depends on ASTRA. The `astra` CLI handles spec operations; the `lc` CLI handles execution and agent operations.

## Quick orientation for maintainers

```
src/lightcone/
├── cli.py              # Click CLI entry point (init, run, build, status, dev, target, setup, update)
├── container.py        # Content-addressed container builds (Docker, podman-hpc)
└── dagster/
    ├── assets.py        # Asset factory — turns astra.yaml recipes into Dagster assets
    ├── io_manager.py    # Maps (output, universe) → results/{universe}/{output}/
    ├── runner.py        # Execution backends: Docker, local, venv, SLURM
    ├── site_registry.py # Known HPC site defaults (Perlmutter, etc.)
    ├── status.py        # Materialization status queries (SQLite via Dagster)
    ├── targets.py       # Target config management (~/.lightcone/targets/)
    └── tree.py          # Sub-analysis tree traversal helpers

claude/lightcone/            # Claude Code plugin (bundled into wheel via hatch force-include)
├── skills/             # lc-new, lc-build, lc-verify, lc-migrate, lc-feedback
├── templates/          # Project CLAUDE.md template
├── agents/             # lc-extractor (literature extraction subagent)
├── guides/             # astra-reference.md, lightcone-cli-reference.md, ui-brand.md
├── hooks/              # Langfuse telemetry hooks (Python)
└── scripts/            # Session hooks (bash): venv activation, validate-on-save, status display
```

## Key data flow

```
astra.yaml → build_definitions() → Dagster assets → ASTRAContainerRunner → results/{universe}/{output}/
                                         ↑                    ↑
                                    ASTRAIOManager        Docker / local / venv / SLURM
```

1. `build_definitions()` (assets.py) reads `astra.yaml`, resolves the full sub-analysis tree, and creates one Dagster asset per output that has a `recipe` block.
2. Asset dependencies come from `recipe.inputs` — Dagster resolves execution order automatically.
3. `ASTRAContainerRunner` (runner.py) dispatches to Docker, venv, local subprocess, or SLURM based on the target config.
4. Results are always written to `results/{universe_id}/{output_id}/` — enforced by the IO manager.

## Development setup

```bash
uv sync --group dev
uv run pytest
uv run ruff check src/ tests/
uv run mypy src/
```

## Key invariants

- `astra.yaml` is the single source of truth — all inputs, outputs, recipes, decisions, containers.
- Output paths are always `results/{universe_id}/{output_id}/` — not customizable.
- Container spec is a single string: an image name (e.g. `python:3.9`) is pulled; a file path (e.g. `Containerfile`) is built.
- Container image tags are deterministic: `SHA256(Containerfile + dependency files)` → `lc-{name}-{hash}`.
- Universe decision parameters are injected as CLI args: `--key value` passed to recipe commands.
- Per-recipe container specs override the analysis-level default.

## Config resolution order

Used by all commands:

| Setting | Priority |
|---------|----------|
| Target | `--target` flag › `.lightcone/lightcone.yaml` › `~/.lightcone/config.yaml` › `"local"` |
| Permission tier | `--permissions` flag › `~/.lightcone/config.yaml` › interactive prompt |
