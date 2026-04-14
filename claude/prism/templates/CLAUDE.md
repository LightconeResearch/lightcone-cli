# CLAUDE.md

## Project: {{name}}

ASTRA (Agentic Schema for Transparent Research Analysis) analysis project, built with Prism.

### Source of Truth

- `astra.yaml` -- The analysis specification. Read this first.
- `universes/` -- Decision selections (one YAML per universe).
- `.claude/guides/astra-reference.md` -- Full reference for astra.yaml structure, sub-analyses, decision parameterization, recipe format, prior insights, findings, and universe management. Read when you need spec syntax.
- `.claude/guides/prism-reference.md` -- Prism execution reference: CLI commands, status interpretation, development workflow, failure diagnosis.

### Project Layout

```
astra.yaml              # Specification: decisions, inputs, outputs
CLAUDE.md             # This file
Containerfile         # Container image for execution
requirements.txt      # Python deps (keep in sync with scripts)
.prism/               # Prism internals
  prism.yaml          # Prism config (default target, etc.)
  dagster.yaml        # Dagster instance config
universes/
  baseline.yaml       # Default decision selections
scripts/              # Implementation scripts
results/<universe>/   # Outputs by universe (produced by prism run)
```

## Working Notes

Use this section for context that doesn't belong in `astra.yaml` but matters across sessions: sub-analyses spawned and why, design decisions that shaped the spec, open questions, blockers, and anything you'd want to know if resuming this project cold.
