# CLAUDE.md

## Project Overview

Prism is Lightcone Research's ASTRA-compliant agentic layer. It provides the full agent experience on top of the ASTRA (Agentic Schema for Transparent Research Analysis) core specification.

**ASTRA** = pure specification: schema, validation, insights, verification, helpers, minimal CLI
**Prism** = agentic layer: Claude Code skills/plugin, project scaffolding, remote/HPC config, execution framework

Prism depends on ASTRA. `pip install prism` gives the full agent experience with ASTRA as a dependency.

## Repository Structure

```
Prism/
├── extern/ASTRA            # Symlink to ASTRA repo
├── src/prism/
│   ├── __init__.py
│   ├── cli.py              # Prism CLI (init, remote, canvas, navigator)
│   └── remote.py           # HPC/remote target config
├── claude/prism/            # Claude Code plugin
│   ├── skills/             # Skills for Claude Code
│   ├── templates/          # Project templates (CLAUDE.md)
│   └── scripts/            # Hook scripts
├── tests/
├── pyproject.toml
└── CLAUDE.md               # This file
```

## Development Commands

```bash
# Install for development
pip install -e ".[dev]"

# Also install ASTRA in dev mode
cd extern/ASTRA && pip install -e ".[dev]"

# Run tests
pytest

# Lint and type check
ruff check src/ tests/
mypy src/
```

## Key Conventions

- Prism depends on ASTRA for all spec operations (validation, schemas, helpers)
- The `prism` CLI handles agent/execution operations (init with scaffolding, remote, canvas)
- The `astra` CLI handles spec operations (validate, info, universe, viz, schema, paper)
- Skills are branded as `/prism-new`, `/prism-build`, `/prism-verify`
- Target configs are stored in `~/.prism/targets/`
