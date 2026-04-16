# prism update

Update Prism and sync plugin files to projects.

## Synopsis

```
prism update [OPTIONS]
```

## Description

`prism update` upgrades `lightcone-prism` from PyPI, then offers to sync updated skills, hooks, scripts, and `CLAUDE.md` into existing projects.

## Options

| Option | Description |
|--------|-------------|
| `--sync` | Only sync plugin files to projects (skip upgrade) |

## What gets synced

When syncing to a project, the following are updated in `.claude/`:

- `skills/` — all skill directories
- `hooks/` — all Python hook scripts
- `scripts/` — all bash hook scripts
- `agents/` — subagent definitions (extraction model config reapplied)
- `guides/` — reference documentation

For `CLAUDE.md`, only the managed portion (everything above `## Analysis Context`) is updated. User content below that separator is preserved.

## Examples

```bash
prism update             # upgrade + offer to sync
prism update --sync      # just sync plugin files (no upgrade)
```

## Notes

The sync prompt asks for a comma-separated list of project paths. Enter `skip` or press Enter to skip syncing.

After upgrading, always sync any active projects to ensure they have the latest skills and hook behaviour.
