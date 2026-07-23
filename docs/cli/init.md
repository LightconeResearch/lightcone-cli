# lc init

Scaffold a new ASTRA project with agent integration.

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
  settings.json               # enabled plugin activation (no marketplace source, no permission policy)
.venv/                        # Python venv (skipped with --no-venv)
```

`lc init` refuses to run if `DIRECTORY/astra.yaml` already exists.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--no-git` | off | Skip `git init`. |
| `--no-venv` | off | Skip `python -m venv .venv`. |

> The historical `--target`, `--existing-project`, `--sub-analysis`, and
> `--permissions` flags have been removed; today's `lc init` only knows the two
> flags above. For migrating an existing project, run `lc init` in a fresh
> directory and use the `/lightcone-experimental:from-code` skill from inside your agent.

## Permissions

`lc init` writes no permission policy. Permissions belong to the harness. You
choose the trust level your agent runs under. See
[Troubleshooting](../user/troubleshooting.md#recommended-permissions-for-cluster-work)
for a copy-paste ruleset for cluster work.

## Examples

```bash
lc init                                # scaffold in cwd
lc init my-analysis                    # scaffold in ./my-analysis
lc init my-analysis --no-git --no-venv # bare bones
```

## Next steps

```bash
cd my-analysis
# open your agent CLI in the project, e.g. Claude Code:
claude
# Inside the session:
/lightcone:new  # scope a research question into astra.yaml
# Then ask the agent to implement the spec.
# It will run lc run, watch lc status, then validate and verify.
```
