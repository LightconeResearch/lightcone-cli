# lc init

Scaffold a new ASTRA project with Claude Code integration.

## Synopsis

```text
lc init [OPTIONS] [DIRECTORY]
```

`DIRECTORY` defaults to `.` (the current directory).

## What it creates

Inside `DIRECTORY` (creating it if needed):

```text
astra.yaml                    # tiny boilerplate spec with one example output
CLAUDE.md                     # short note pointing future agents at the project
.gitignore                    # Python + lightcone state
.lightcone/
  lightcone.yaml              # currently a stub: { target: local }
results/                      # placeholder; populated by `lc run`
universes/                    # placeholder; populate via `astra universe generate -n …`
.claude/
  settings.json               # the chosen permission tier (project-scoped)
.venv/                        # Python venv (skipped with --no-venv)
```

`lc init` then shells out to the `claude` CLI (if found on PATH) to install
the `lightcone` plugin into the user's Claude Code config:

```
claude plugin marketplace add <wheel-installed marketplace root>
claude plugin install lightcone@lightcone-cli
```

The plugin (skills, agents, hooks) lives user-scoped under `~/.claude/`, not
per-project. Both commands are idempotent, so subsequent `lc init` calls are
no-ops for the plugin. When `claude` isn't on PATH (Codex users, etc.), the
plugin install is skipped and the manual commands above are printed — `lc init`
still succeeds.

`lc init` refuses to run if `DIRECTORY/astra.yaml` already exists.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--no-git` | off | Skip `git init`. |
| `--no-venv` | off | Skip `python -m venv .venv`. |
| `--permissions {yolo,recommended,minimal}` | `recommended` | Which `.claude/settings.json` permission tier to install. |

> The historical `--target`, `--existing-project`, and `--sub-analysis`
> flags have been removed; today's `lc init` only knows the three flags
> above. For migrating an existing project, run `lc init` in a fresh
> directory and use the `/lc-from-code` skill from inside Claude Code.

## Permission tiers

| Tier | Allowed | Denied |
|------|---------|--------|
| `yolo` | `Bash(*)`, `Edit`, `Read`, `Write`, `WebSearch`, `WebFetch`, `mcp__*` | — |
| `recommended` | `Read`, `Edit`, `Write`, `Bash(*)`, `WebSearch`, `WebFetch` | Edits to `~/.ssh`, `~/.aws`, `~/.gnupg`, `/scratch`, `/pscratch`; `sudo`, `rm -rf`, `git push`. |
| `minimal` | `Read` | Everything else. |

The tiers are defined as `PERMISSION_TIERS` in
`src/lightcone/cli/commands.py` — adjust there if you want to add a tier
or change defaults.

## Examples

```bash
lc init                                # scaffold in cwd, recommended tier
lc init my-analysis                    # scaffold in ./my-analysis
lc init my-analysis --no-git --no-venv # bare bones
lc init . --permissions yolo           # for autonomous loops you trust
```

## Next steps

```bash
cd my-analysis
claude           # open Claude Code
# Inside Claude Code:
/lc-new  # scope a research question into astra.yaml
# Then ask the agent to implement the spec.
# It will run lc run, watch lc status, then validate and verify.
```
