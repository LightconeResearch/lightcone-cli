# Skills

Skills are Claude Code slash commands bundled in the lightcone-cli
plugin. They give the agent a structured, phase-by-phase workflow for
the most common research operations.

If you're a researcher trying to *use* these, the
[Claude Code Workflow](../user/claude-workflow.md) page in the user
guide is the friendly version. This page is for maintainers.

## Available skills

The `/lc-from-*` family is parallel by what you start from: a question,
code, or a paper. `/lc-from-paper` is the entry point of a five-skill
paper-reproduction bundle; the four bundle siblings stand alone and are
user-invokable directly.

### Project lifecycle

| Skill | Command | Purpose |
|-------|---------|---------|
| [lc-new](lc-new.md) | `/lc-new` | Scope a research question into an `astra.yaml`, with optional literature extraction. |
| [lc-from-code](lc-from-code.md) | `/lc-from-code` | Wrap an existing codebase in ASTRA: scan, generate spec, parameterize, run. |
| [lc-from-paper](lc-from-paper.md) | `/lc-from-paper` | Reproduce a published paper in ASTRA — interview-first orchestrator that spawns named per-phase sub-agents. |
| [lc-feedback](lc-feedback.md) | `/lc-feedback` | File a GitHub issue against the right Lightcone repo with auto-collected context. |

### Paper-reproduction bundle (sibling skills)

Co-located with `lc-from-paper` so a single `lc init` brings the full
toolkit. Each stands alone and is user-invokable; `lc-from-paper`
dispatches them by role during the reproduction.

| Skill | Command | Purpose |
|-------|---------|---------|
| [paper-extraction](paper-extraction.md) | `/paper-extraction` | Turn an arXiv ID or DOI into a standardized `work/reference/` directory: substrate, figures, tables, citations (with resolved DOIs), and a stub `astra.yaml`. |
| [narrative](narrative.md) | `/narrative` | Author the `narrative:` prose and decision `rationale:` against an existing `astra.yaml`, in paper-reproduction, retrofit, or co-drafting mode. |
| [figure-comparison](figure-comparison.md) | `/figure-comparison` | Build a self-contained HTML side-by-side: paper figures, tables, and numerics vs reproduced artifacts. |
| [check-sentence-by-sentence](check-sentence-by-sentence.md) | `/check-sentence-by-sentence` | Static audit of paper claims against code locations (`file:line` or `NOT FOUND`). |

See the [bundle README](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/README.md) for the rationale behind co-location vs plugin install.

## How a skill is wired

Each skill is a `claude/lightcone/skills/<name>/SKILL.md` file with
YAML frontmatter:

```yaml
---
name: lc-new
description: >
  Scope a new ASTRA analysis from a research question...
allowed-tools: Read, Write(astra.yaml), Edit(astra.yaml), Glob, Grep, Bash(astra:*), ...
argument-hint: "[DESCRIPTION]"
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
│   ├── lc-new/{SKILL.md, references/*.md}
│   ├── lc-from-code/SKILL.md
│   ├── lc-from-paper/{SKILL.md, references/*.md, templates/CLAUDE.md}
│   ├── lc-feedback/SKILL.md
│   ├── paper-extraction/{SKILL.md, scripts/*.py}
│   ├── narrative/{SKILL.md, references/*.md}
│   ├── figure-comparison/{SKILL.md, scripts/*.py}
│   └── check-sentence-by-sentence/SKILL.md
├── agents/lc-extractor.md             # literature subagent for /lc-new
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
| `claude/lightcone/guides/astra-reference.md` | Full `astra.yaml` schema reference. Loaded by `lc-new` and `lc-from-code`. |
| `claude/lightcone/guides/lightcone-cli-reference.md` | CLI commands, status interpretation, failure diagnosis. Loaded by implementation and validation workflows. |
| `claude/lightcone/guides/ui-brand.md` | Visual formatting conventions for skill output. |
| `claude/lightcone/agents/lc-extractor.md` | Literature extraction subagent invoked by `/lc-new`. |

## Authoring a new skill

See [Authoring Skills](authoring.md).
