# Skills

Skills are Claude Code slash commands bundled in the lightcone-cli
plugin. They give the agent a structured, phase-by-phase workflow for
the most common research operations.

If you're a researcher trying to *use* these, the
[Claude Code Workflow](../user/claude-workflow.md) page in the user
guide is the friendly version. This page is for maintainers.

## Available skills

The `/lc-from-*` family is parallel by what you start from: a question,
code, or a paper. `/lc-from-paper` is the entry point of a six-skill
paper-reproduction bundle; the five bundle siblings stand alone and are
user-invokable directly.

### Project lifecycle

| Skill | Command | Purpose |
|-------|---------|---------|
| [lc-new](lc-new.md) | `/lc-new` | Scope a research question into an `astra.yaml`, with optional literature extraction. |
| [lc-from-code](lc-from-code.md) | `/lc-from-code` | Wrap an existing codebase in ASTRA: scan, generate spec, parameterize, run. |
| [lc-from-paper](lc-from-paper.md) | `/lc-from-paper` | Reproduce a published paper in ASTRA — interview-first driver that hands off to a ralph loop for the long middle. |
| [lc-feedback](lc-feedback.md) | `/lc-feedback` | File a GitHub issue against the right Lightcone repo with auto-collected context. |
| [ralph](ralph.md) | `/ralph` | Author a constitution and run a ralph loop against it. Used by `lc-from-paper` for the long middle; standalone for any other long-running work. |

### Paper-reproduction bundle (sibling skills)

Co-located with `lc-from-paper` so a single `lc init` brings the full
toolkit. Each stands alone and is user-invokable; `lc-from-paper`
dispatches them by role during the reproduction.

| Skill | Command | Purpose |
|-------|---------|---------|
| [ralph](ralph.md) | `/ralph` | Loop substrate. `lc-from-paper`'s INTERVIEW invokes ralph's Authoring mode to draft the per-paper constitution; ACQUIRE's hand-off invokes the launcher; each iteration runs ralph's Loop protocol. Also user-invokable standalone (see the Project lifecycle row above). |
| [paper-extraction](paper-extraction.md) | `/paper-extraction` | Turn an arXiv ID or DOI into a standardized `work/reference/` directory: substrate, figures, tables, citations (with resolved DOIs), and a stub `astra.yaml`. |
| [narrative](narrative.md) | `/narrative` | Author the `narrative:` prose and decision `rationale:` against an existing `astra.yaml`, in paper-reproduction, retrofit, or co-drafting mode. |
| [figure-comparison](figure-comparison.md) | `/figure-comparison` | Build a self-contained HTML side-by-side: paper figures, tables, and numerics vs reproduced artifacts. |
| [check-sentence-by-sentence](check-sentence-by-sentence.md) | `/check-sentence-by-sentence` | Static audit of paper claims against code locations (`file:line` or `NOT FOUND`). |

See the [bundle README](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/README.md) for the rationale behind co-location vs plugin install.

### Reference skills (auto-primed via session-start)

Not direct entry points — invoked by other skills (or by Claude directly when relevant) to load reference content into the working session. The session-start hook names both in its primer, so Claude is aware they exist from the first turn.

| Skill | Command | Purpose |
|-------|---------|---------|
| `astra` | `/astra` | Reference for the `astra.yaml` spec: structure, decisions, options, prior insights, findings, evidence, sub-analyses, narrative anchors, composition mechanics. |
| `lc-cli` | `/lc-cli` | Reference for `lc` workflow: commands, the Spec-Code Invariant, status interpretation, failure diagnosis, multiverse runs, publishing via WRROC. |

These intentionally don't appear in the top-level README — researchers use the project-lifecycle skills directly; the reference skills are infrastructure.

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
│   ├── lc-from-paper/{SKILL.md, references/*.md, templates/{constitution.md, CLAUDE.md}}
│   ├── lc-feedback/SKILL.md
│   ├── ralph/{SKILL.md, references/*.md, scripts/ralph}
│   ├── paper-extraction/{SKILL.md, scripts/*.py}
│   ├── narrative/{SKILL.md, references/*.md}
│   ├── figure-comparison/{SKILL.md, scripts/*.py}
│   ├── check-sentence-by-sentence/SKILL.md
│   ├── astra/SKILL.md                  # reference: astra.yaml spec
│   └── lc-cli/SKILL.md                 # reference: lc workflow
├── agents/lc-extractor.md             # literature subagent for /lc-new
├── guides/ui-brand.md                 # visual formatting conventions
├── templates/CLAUDE.md                # the project CLAUDE.md template
└── scripts/*.sh                       # session lifecycle hooks (incl. session-start primer)
```

The plugin is force-included into the wheel via
`pyproject.toml::tool.hatch.build.targets.wheel.force-include`, so
`lc init` finds it whether you're running from source or PyPI.

## Other plugin files

The two reference *skills* (`/astra` and `/lc-cli`) live under `skills/` and are listed in the [Reference skills](#reference-skills-auto-primed-via-session-start) section above. Remaining plugin files:

| File | Purpose |
|------|---------|
| `claude/lightcone/guides/ui-brand.md` | Visual formatting conventions for skill output. |
| `claude/lightcone/agents/lc-extractor.md` | Literature extraction subagent invoked by `/lc-new`. |
| `claude/lightcone/scripts/session-start.sh` | Session-start hook — surfaces validation + materialization status and primes Claude with the substrate CLIs and reference skill names. |

## Authoring a new skill

See [Authoring Skills](authoring.md).
