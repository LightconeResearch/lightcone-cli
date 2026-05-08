# Skills

Skills are Claude Code slash commands bundled in the lightcone-cli
plugin. They give the agent a structured, phase-by-phase workflow for
the most common research operations.

If you're a researcher trying to *use* these, the
[Claude Code Workflow](../user/claude-workflow.md) page in the user
guide is the friendly version. This page is for maintainers.

## Available skills

| Skill | Command | Purpose |
|-------|---------|---------|
| [lc-new](lc-new.md) | `/lc-new` | Scope a research question into an `astra.yaml`, with optional literature extraction. |
| [lc-build](lc-build.md) | `/lc-build` | Plan + autonomous loop until all outputs in a universe materialize. |
| [lc-verify](lc-verify.md) | `/lc-verify` | Read-only audit: spec validity, materialization status, decision-code alignment, result file shapes. |
| [lc-migrate](lc-migrate.md) | `/lc-migrate` | Wrap an existing codebase in ASTRA: scan, generate spec, parameterize, run. |
| [lc-feedback](lc-feedback.md) | `/lc-feedback` | File a GitHub issue against the right Lightcone repo with auto-collected context. |

## How a skill is wired

Each skill is a `claude/lightcone/skills/<name>/SKILL.md` file with
YAML frontmatter:

```yaml
---
name: lc-build
description: >
  Build an ASTRA analysis from spec to materialized results...
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(lc:*), ...
argument-hint: "[DESCRIPTION] [--universe NAME] [--max-iterations N]"
---
```

The frontmatter configures Claude Code: which tools the skill may
invoke, and what the slash command's argument hint looks like. The
body is the prompt — phase definitions, rules, references to guide
files, anti-patterns. The skill bundles its own helper scripts under
`scripts/` and its loop prompt template under `assets/` when relevant.

## Plugin layout

```
claude/lightcone/
├── skills/
│   ├── lc-new/SKILL.md
│   ├── lc-build/{SKILL.md, assets/loop-prompt.md, scripts/setup-lc-build.sh}
│   ├── lc-verify/SKILL.md
│   ├── lc-migrate/SKILL.md
│   └── lc-feedback/SKILL.md
├── agents/lc-extractor.md             # subagent definition
├── guides/                            # reference docs loaded by skills
├── templates/CLAUDE.md                # the project CLAUDE.md template
└── scripts/*.sh                       # session lifecycle hooks
```

The plugin is force-included into the wheel via
`pyproject.toml::tool.hatch.build.targets.wheel.force-include`, so
`lc init` finds it whether you're running from source or PyPI.

## Reference guides loaded by skills

| File | Purpose |
|------|---------|
| `claude/lightcone/guides/astra-reference.md` | Full `astra.yaml` schema reference. Loaded by `lc-new`, `lc-build`, `lc-migrate`. |
| `claude/lightcone/guides/lightcone-cli-reference.md` | CLI commands, status interpretation, failure diagnosis. Loaded by build/verify skills. |
| `claude/lightcone/guides/ui-brand.md` | Visual formatting conventions for skill output. |
| `claude/lightcone/agents/lc-extractor.md` | Literature extraction subagent invoked by `/lc-new`. |

## Authoring a new skill

See [Authoring Skills](authoring.md).
