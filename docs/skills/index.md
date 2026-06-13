# Skills

Skills are Claude Code slash commands bundled in the lightcone-cli
plugin. Each shapes the agent's workflow around a recurring research
operation: scoping an analysis, wrapping existing code, reproducing
a paper.

If you want to *use* these, start with
[The Agentic Workflow](../user/agent-workflow.md) in the user guide.
This page is for maintainers.

## Available skills

`/lightcone` is the entry point — activate it first whenever an
`astra.yaml` project is in play (see [Entry point](#entry-point) below).
The `/lc-from-*` family is parallel in what you start from: a question,
code, or a paper. `/lc-from-paper` is the entry point of a five-skill
paper-reproduction bundle; the four siblings stand alone and are
user-invokable directly.

### Entry point

| Skill | Command | Purpose |
|-------|---------|---------|
| [lightcone](#references-bundled-under-lightcone) | `/lightcone` | **Start here.** Carries the lay of the land (what ASTRA is, the Spec-Code Invariant, the core `lc` loop), bundles the two references at `references/{astra,lc-cli}.md`, and routes to the task skill for the job. The PreToolUse skill-gate opens once it's activated. |

### Project lifecycle

| Skill | Command | Purpose |
|-------|---------|---------|
| [lc-new](lc-new.md) | `/lc-new` | Scope a research question into an `astra.yaml`, with optional literature extraction. |
| [lc-from-code](lc-from-code.md) | `/lc-from-code` | Wrap an existing codebase in ASTRA: scan, generate spec, parameterize, run. |
| [lc-from-paper](lc-from-paper.md) | `/lc-from-paper` | Reproduce a published paper in ASTRA — two interactive bookends in your main session wrapping one autonomous Workflow that carries the heavy middle. |
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

### References (bundled under `/lightcone`)

Not skills. These are reference documents bundled with the `/lightcone` entry point at `lightcone/references/`. Activating `/lightcone` is enough — read the section you need when you need it; the references stay available throughout the session.

| Reference | Path | Purpose |
|-----------|------|---------|
| `astra` | `references/astra.md` | Reference for the `astra.yaml` spec: structure, decisions, options, prior insights, findings, evidence, sub-analyses, narrative anchors, composition mechanics. |
| `lc-cli` | `references/lc-cli.md` | Reference for `lc` workflow: commands, the Spec-Code Invariant, status interpretation, failure diagnosis, multiverse runs, publishing via WRROC. |

These intentionally stay out of the top-level README. Researchers use the project-lifecycle skills directly; the references are infrastructure carried by `/lightcone`.

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

The frontmatter tells Claude Code which tools the skill may invoke
and what the slash command's argument hint looks like. The body is the
prompt itself: phase definitions, rules, references to guide files,
anti-patterns. Skills bundle their own helper scripts under `scripts/`
and longer prompt fragments under `assets/` when relevant.

## Plugin layout

```text
claude/lightcone/
├── skills/
│   ├── lightcone/{SKILL.md, references/{astra,lc-cli}.md}  # entry point + bundled references
│   ├── lc-new/{SKILL.md, references/*.md}
│   ├── lc-from-code/SKILL.md
│   ├── lc-from-paper/{SKILL.md, reproduce_workflow.js, references/*.md, templates/{plan.md, CLAUDE.md}}
│   ├── lc-feedback/SKILL.md
│   ├── paper-extraction/{SKILL.md, scripts/*.py}
│   ├── narrative/{SKILL.md, references/*.md}
│   ├── figure-comparison/{SKILL.md, scripts/*.py}
│   └── check-sentence-by-sentence/SKILL.md
├── agents/lc-extractor.md             # literature subagent for /lc-new
├── templates/CLAUDE.md                # the project CLAUDE.md template
└── scripts/*.sh                       # session lifecycle hooks (incl. session-start primer)
```

The plugin is force-included into the wheel via
`pyproject.toml::tool.hatch.build.targets.wheel.force-include`, so
`lc init` finds it whether you're running from source or PyPI.

## Other plugin files

The two references (`astra` and `lc-cli`) are now bundled under `/lightcone` at `lightcone/references/{astra,lc-cli}.md` and are listed in the [References (bundled under `/lightcone`)](#references-bundled-under-lightcone) section above. Remaining plugin files:

| File | Purpose |
|------|---------|
| `claude/lightcone/agents/lc-extractor.md` | Literature extraction subagent invoked by `/lc-new`. |
| `claude/lightcone/scripts/session-start.sh` | Session-start hook — surfaces validation + materialization status and primes Claude with the substrate CLIs and the `/lightcone` entry point. |

## Authoring a new skill

See [Authoring Skills](authoring.md).
