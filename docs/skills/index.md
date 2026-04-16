# Skills

Skills are Claude Code slash commands bundled in the Prism plugin. They give Claude Code a structured, phase-by-phase workflow for the most common research operations.

## Available skills

| Skill | Command | Purpose |
|-------|---------|---------|
| [prism-new](prism-new.md) | `/prism-new` | Create a new ASTRA analysis from a research question |
| [prism-build](prism-build.md) | `/prism-build` | Implement, run, and debug an analysis |
| [prism-verify](prism-verify.md) | `/prism-verify` | Check `astra.yaml`, code, and results for consistency |
| [prism-migrate](prism-migrate.md) | `/prism-migrate` | Migrate existing code into ASTRA format |
| [prism-feedback](prism-feedback.md) | `/prism-feedback` | File a bug report on GitHub |

## How skills work

Each skill is a markdown file (`SKILL.md`) in `.claude/skills/{skill-name}/`. Claude Code discovers skills by scanning the `.claude/skills/` directory. The frontmatter configures the skill's metadata and allowed tools:

```yaml
---
name: prism-build
description: Build, run, and debug an ASTRA analysis
allowed-tools: Read, Write, Edit, Bash, WebSearch, WebFetch
---
```

The body of the file is a structured prompt that tells Claude exactly how to proceed, including phase definitions, rules, and references to guide files.

## Plugin installation

Skills are installed by `prism init` (copying the plugin to `.claude/`) and updated by `prism update --sync`. They live at:

- **Bundled (installed package)**: `{site-packages}/prism/claude/prism/skills/`
- **Development**: `{repo}/claude/prism/skills/`

## Related files

| File | Purpose |
|------|---------|
| `claude/prism/guides/prism-reference.md` | CLI and workflow reference loaded by build/verify skills |
| `claude/prism/guides/astra-reference.md` | Full ASTRA spec reference loaded by all skills |
| `claude/prism/guides/ui-brand.md` | Visual formatting conventions for skill output |
| `claude/prism/agents/prism-extractor.md` | Literature extraction subagent used by `/prism-new` |
