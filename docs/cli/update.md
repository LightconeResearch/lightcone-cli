# lc update

Upgrade lightcone-cli and sync plugin files to projects.

## Synopsis

```
lc update [OPTIONS]
```

## Description

`lc update` upgrades `lightcone-cli` from PyPI, then offers to sync updated skills, agents, guides, and `CLAUDE.md` into existing projects.

If the pip upgrade fails (e.g. inside a project venv), a warning is printed and the sync step still runs.

## Options

| Option | Description |
|--------|-------------|
| `--sync` | Only sync plugin files to projects (skip upgrade) |
| `--tools` | Harness(es) to sync (repeat for multiple; default: `claude`) |

## What gets synced

For each selected harness, the following directories are updated in `.<prefix>/`:

- `skills/` — all skill directories
- `agents/` — subagent definitions (extraction model config reapplied)
- `guides/` — reference documentation

Hooks (`hooks/`) and scripts (`scripts/`) are **not** synced — they are written once at `lc init` time. Re-run `lc init` in an existing project to update them.

For `CLAUDE.md` (Claude Code harness only), the managed portion (everything above `## Analysis Context`) is refreshed from the template. User content below that separator is preserved.

## Examples

```bash
lc update                            # upgrade + offer to sync (claude harness)
lc update --sync                     # sync only, no upgrade
lc update --sync --tools codex       # sync Codex harness only
lc update --sync --tools claude --tools codex   # sync both harnesses
```

## Notes

The sync prompt asks for a comma-separated list of project paths. Enter `skip` or press Enter to skip syncing.

After upgrading, always sync active projects to ensure they have the latest skills and agents.
