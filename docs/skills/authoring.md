# Authoring Skills

Skills are markdown files with YAML frontmatter. Each one lives in
`claude/lightcone/skills/<name>/SKILL.md`. Add helper scripts under
`scripts/` and any longer prompts under `assets/` next to the
`SKILL.md`.

## File layout

```
claude/lightcone/skills/
└── my-skill/
    ├── SKILL.md
    ├── assets/                # optional — long prompt fragments, templates
    └── scripts/               # optional — bash helpers invoked by the skill
```

## Frontmatter

```yaml
---
name: my-skill
description: >
  One- to three-sentence description. Mention concrete trigger phrases
  ("starts a new project", "scope this", …) so Claude Code's skill
  router fires reliably.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(astra:*), Bash(lc:*), AskUserQuestion
argument-hint: "[OPTIONAL ARG] [--flag VALUE]"
---
```

| Field | Notes |
|-------|-------|
| `name` | Slash command without leading `/`. Matches the directory name. |
| `description` | What Claude Code sees when deciding whether to invoke the skill. Specific trigger phrases help. |
| `allowed-tools` | Comma-separated tool list. Use scoped Bash patterns (`Bash(lc:*)`, not bare `Bash`) where you can — it cuts the false-positive surface for the agent dramatically. |
| `argument-hint` | Optional. Shown in slash-command auto-completion. |

## Body conventions

Follow [`claude/lightcone/guides/ui-brand.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/guides/ui-brand.md):

- `##` for phase headings; lead with a "Stage banner" line that the
  skill prints to the chat.
- `✓ / ○ / ✗` for status; never emojis except inside the agent's own
  branded output.
- Action prompts in bold sentences (`> "What are you trying to learn?"`).
- A `## Restrictions` (or `## Hard rules`) section at the end listing
  invariants Claude must not break.

## Referencing guide files

Guides live alongside the skills:

```markdown
Before starting, read `.claude/guides/astra-reference.md` for the
spec, and `.claude/guides/lightcone-cli-reference.md` for the CLI.
```

The plugin layout means these paths are stable across both bundled
(installed-package) and dev (in-repo) modes.

## Spawning subagents

Use `Task` with `subagent_type` to delegate work. The
`lc-extractor` subagent in `agents/` is the canonical example:

```python
Agent(subagent_type="lc-extractor", prompt="""
Analysis context: ...
Paper details: DOI ..., PDF path ...
Target decisions: ...
""")
```

Spawn agents in parallel by issuing them in a single tool-use block.

## Testing

The `evals/` tree has fixtures (currently `evals/tasks/snae/`) and the
runner lives at `lightcone.eval.harness`. Eval CLI commands are defined
in `lightcone.eval.cli` (`lc eval run|report|compare`), but **note that
this group is currently not wired into the top-level `lc` CLI** — see
the [maintainer summary](../index.md) for status. To run evals
programmatically:

```python
from pathlib import Path
from lightcone.eval.cli import run_cmd
# or invoke `lightcone.eval.harness.run_eval(...)` directly
```

## Installing changes into an existing project

`lc init` copies the plugin once. To pull updated skills into an
existing project after editing them:

```bash
python - <<'PY'
import shutil
from pathlib import Path
from lightcone.cli.plugin import get_plugin_source_dir
src = get_plugin_source_dir()
dst = Path(".claude/skills")
if dst.exists(): shutil.rmtree(dst)
shutil.copytree(src / "skills", dst)
PY
```

(See [`lc update`](../cli/update.md) for the longer story.)
