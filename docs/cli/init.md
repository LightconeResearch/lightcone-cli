# lc init

Scaffold a new ASTRA project with agent integration.

## Synopsis

```text
lc init [OPTIONS] [DIRECTORY]
```

`DIRECTORY` defaults to `.` (the current directory).

## What it creates

`DIRECTORY` must not exist yet, or must be empty — `lc init` scaffolds new
projects only. Running it against a non-empty directory (including one that
already has an `astra.yaml`) fails cleanly before writing anything;
re-running init or adopting an existing project is not supported.

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
.venv/                        # empty Python venv (skipped with --no-venv)
```

`lc init` writes this scaffold in a single pass onto an empty directory.
There is no re-run or adoption mode — running it a second time, or against
a directory that already has an `astra.yaml`, fails with a clean error
before touching anything.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--no-git` | off | Skip `git init`. |
| `--no-venv` | off | Skip Python venv creation. |

> Both `lc` and `astra` install globally from the `lightcone-cli` wheel —
> neither goes into the project venv. `lc init` creates `.venv` empty (`uv
> venv --python 3.12 .venv`, falling back to `python -m venv .venv` when `uv`
> isn't on PATH); it's the analysis environment the agent populates as the
> project's dependencies come into focus. `requirements.txt` feeds the
> container build, not this venv.
>
> The historical `--target`, `--existing-project`, `--sub-analysis`, and
> `--permissions` flags have been removed; today's `lc init` only knows
> `--no-git`, `--no-venv`, and `--scratch`. To bring an existing codebase
> under ASTRA, scaffold a fresh project with `lc init` and use the
> `/lightcone-experimental:from-code` skill from inside your agent to port
> the code in; `lc init` cannot be run against a directory the codebase
> already occupies.

## Permissions

`lc init` writes no permission policy. Permissions belong to the harness. You
choose the trust level your agent runs under. See
[Troubleshooting](../user/troubleshooting.md#recommended-permissions-for-cluster-work)
for a copy-paste ruleset for cluster work.

## Examples

```bash
lc init                                # scaffold in cwd
lc init my-analysis                    # scaffold in ./my-analysis
lc init my-analysis --no-git           # skip git init
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
