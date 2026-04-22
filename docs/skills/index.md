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

Each skill is a markdown file (`SKILL.md`) in `.<harness>/skills/{skill-name}/`. The agent tool discovers skills by scanning that directory. The frontmatter configures the skill's metadata and allowed tools:

```yaml
---
name: lc-build
description: Build, run, and debug an ASTRA analysis
allowed-tools: Read, Write, Edit, Bash, WebSearch, WebFetch
---
```

The body of the file is a structured prompt that tells the agent exactly how to proceed, including phase definitions, rules, and references to guide files.

## Plugin installation

Skills are installed by `lc init` and updated by `lc update --sync`. `lc init` copies skills (and agents, guides) to every harness selected via `--tools`; the default is `claude`. Hooks and scripts are installed for Claude Code only.

Canonical source locations:

- **Bundled (installed package)**: `{site-packages}/lightcone/cli/plugin/lightcone/skills/`
- **Development**: `{repo}/plugin/lightcone/skills/`

## Multi-harness distribution

Skills are harness-agnostic — the same `SKILL.md` files are installed into each selected harness's `skills/` directory:

| Harness | Install path | `--tools` value |
|---------|-------------|-----------------|
| Claude Code | `.claude/skills/` | `claude` |
| Codex | `.codex/skills/` | `codex` |
| Cursor | `.cursor/skills/` | `cursor` |
| GitHub Copilot | `.github/skills/` | `github-copilot` |
| OpenCode | `.opencode/skills/` | `opencode` |

## Related files

| File | Purpose |
|------|---------|
| `plugin/lightcone/guides/lightcone-cli-reference.md` | CLI and workflow reference loaded by build/verify skills |
| `plugin/lightcone/guides/astra-reference.md` | Full ASTRA spec reference loaded by all skills |
| `plugin/lightcone/guides/ui-brand.md` | Visual formatting conventions for skill output |
| `plugin/lightcone/agents/lc-extractor.md` | Literature extraction subagent used by `/lc-new` |
