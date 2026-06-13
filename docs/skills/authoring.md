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

- `##` for phase headings; lead with a "Stage banner" line that the
  skill prints to the chat.
- `✓ / ○ / ✗` for status; never emojis except inside the agent's own
  branded output.
- Action prompts in bold sentences (`> "What are you trying to learn?"`).
- A `## Restrictions` (or `## Hard rules`) section at the end listing
  invariants Claude must not break.

## Referencing the bundled references

Spec and CLI reference content are bundled under the `/lightcone` entry
point as `references/astra.md` (the `astra.yaml` spec) and
`references/lc-cli.md` (the `lc` workflow), so any skill needing depth
can point the agent at them directly:

```markdown
Read the Decisions section of the ASTRA reference (`references/astra.md`,
bundled with `/lightcone`) before classifying candidate decisions, and the
lc-cli reference (`references/lc-cli.md`) for the Spec-Code Invariant rules.
```

`/lightcone` is named in the session-start primer so it's discoverable
from the first turn; pointing at a specific reference section in a skill
body is the right call when that section is load-bearing for the skill's
work.

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
in `lightcone.eval.cli` and registered as `lc eval run|report|compare`
when the optional `eval` extra is installed (the registration is
gated on `ImportError` in `lightcone.cli.commands`). To run evals
programmatically:

```python
from pathlib import Path
from lightcone.eval.cli import run_cmd
# or invoke `lightcone.eval.harness.run_eval(...)` directly
```

## Picking up edits during development

The `lightcone` plugin lives user-scoped under `~/.claude/plugins/`, installed
via `claude plugin install lightcone@lightcone-cli`. When you're working
inside a `lightcone-cli` checkout and want Claude Code to pick up your
edits, register the marketplace from the repo root (it takes precedence
over any other registration of the same name):

```bash
claude plugin marketplace add /path/to/lightcone-cli
claude plugin install lightcone@lightcone-cli
```

Restart Claude Code; the plugin now loads from your checkout's
`claude/lightcone/` directly. Edits to `SKILL.md` files take effect on
the next session start.

(See [Install](../user/install.md#updating) for the upgrade-from-PyPI story.)
