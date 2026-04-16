# Authoring Skills

Skills are markdown files with YAML frontmatter. They live in `claude/prism/skills/{name}/SKILL.md`.

## File structure

```
claude/prism/skills/
└── my-skill/
    └── SKILL.md
```

## Frontmatter

```yaml
---
name: my-skill
description: One-line description shown to Claude Code
allowed-tools: Read, Write, Edit, Bash, WebSearch, WebFetch
---
```

| Field | Description |
|-------|-------------|
| `name` | Skill identifier (matches directory name) |
| `description` | What Claude sees when listing available skills |
| `allowed-tools` | Comma-separated list of Claude Code tools the skill may use |

## Body conventions

Follow the `ui-brand.md` conventions for skill output:

- Use `##` headings for phases.
- Use `✓ / ○ / ✗` symbols for status.
- Use bold text for action prompts, never boxes or emojis.
- Include a "Rules" section at the end with hard constraints.

## Referencing guides

Skills can reference guide files from `.claude/guides/`:

```markdown
Before starting, read `.claude/guides/astra-reference.md` for the full ASTRA spec.
```

## Installing into projects

New skills in `claude/prism/skills/` are installed automatically by `prism init`. To push skills to existing projects, run `prism update --sync`.

## Testing skills

The `evals/` directory contains test fixtures. Run evals with:

```bash
pip install -e ".[eval]"
prism eval run
```

See `contributing/testing.md` for more details.
