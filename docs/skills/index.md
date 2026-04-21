# Skills

Skills are Claude Code slash commands bundled in the lightcone-cli plugin. They give Claude Code a structured, phase-by-phase workflow for the most common research operations.

## Available skills

| Skill | Command | Purpose |
|-------|---------|---------|
| [lc-new](lc-new.md) | `/lc-new` | Create a new ASTRA analysis from a research question |
| [lc-build](lc-build.md) | `/lc-build` | Implement, run, and debug an analysis |
| [lc-verify](lc-verify.md) | `/lc-verify` | Check `astra.yaml`, code, and results for consistency |
| [lc-migrate](lc-migrate.md) | `/lc-migrate` | Migrate existing code into ASTRA format |
| [lc-feedback](lc-feedback.md) | `/lc-feedback` | File a bug report on GitHub |

## How skills work

Each skill is a markdown file (`SKILL.md`) in `.claude/skills/{skill-name}/`. Claude Code discovers skills by scanning the `.claude/skills/` directory. The frontmatter configures the skill's metadata and allowed tools:

```yaml
---
name: lc-build
description: Build, run, and debug an ASTRA analysis
allowed-tools: Read, Write, Edit, Bash, WebSearch, WebFetch
---
```

The body of the file is a structured prompt that tells Claude exactly how to proceed, including phase definitions, rules, and references to guide files.

## Plugin installation

Skills are installed by `lc init` (copying the plugin to `.claude/`) and updated by `lc update --sync`. They live at:

- **Bundled (installed package)**: `{site-packages}/lightcone/cli/claude/lightcone/skills/`
- **Development**: `{repo}/claude/lightcone/skills/`

## Related files

| File | Purpose |
|------|---------|
| `claude/lightcone/guides/lightcone-cli-reference.md` | CLI and workflow reference loaded by build/verify skills |
| `claude/lightcone/guides/astra-reference.md` | Full ASTRA spec reference loaded by all skills |
| `claude/lightcone/guides/ui-brand.md` | Visual formatting conventions for skill output |
| `claude/lightcone/agents/lc-extractor.md` | Literature extraction subagent used by `/lc-new` |
