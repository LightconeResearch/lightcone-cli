# Prism

Part of [Lightcone Research](https://github.com/LightconeResearch/lightcone.dev) — install all tools with:
```bash
curl -fsSL https://lightconeresearch.github.io/lightcone.dev/install.sh | bash
```

---

## What is Prism?

Prism is the agentic layer for ASTRA (Agentic Schema for Transparent Research Analysis). While ASTRA provides the core specification format — schemas, validation, insights, and evidence verification — Prism adds the full agent experience:

- **Project scaffolding** with Claude Code integration (`prism init`)
- **Claude Code skills** for analysis creation, insight extraction, and verification
- **HPC/remote target management** for running on compute clusters
- **Visual editors** (Canvas, Navigator)

## Quick Start

```bash
# Create a new analysis project
prism init my-analysis
cd my-analysis

# Open in Claude Code
claude

# Scope your research question
/prism-new
```

## CLI Commands

```bash
# Project setup (full scaffolding with Claude Code config)
prism init my-analysis
prism init my-analysis --no-git
prism init my-analysis --target perlmutter

# HPC/remote targets
prism remote setup perlmutter
prism remote setup --list
prism remote show perlmutter

# Visual editors
prism canvas
prism navigator

# Spec operations (via ASTRA CLI):
astra validate astra.yaml
astra info
astra universe generate -n baseline
astra viz
astra paper add <doi>
```

## Claude Code Skills

| Skill | Purpose |
|-------|---------|
| `/prism` | Reference guide for working with ASTRA analyses |
| `/prism-new` | Scope a research question and structure the analysis |
| `/prism-verify` | Verify implementation matches specification |

## Architecture

```
ASTRA (core)        →  Schema, validation, insights, verification, minimal CLI
Prism (agent layer) →  Skills, scaffolding, remote config, visual editors
Spectrum (future)   →  UI layer
```

Prism depends on ASTRA. All spec operations (`astra validate`, `astra info`, etc.) are provided by ASTRA.

## License

Apache 2.0
