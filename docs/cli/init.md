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
astra.yaml                    # tiny boilerplate spec with two example outputs
CLAUDE.md                     # short note pointing future agents at the project
myst.yml                      # MyST config for the report (MySTRA plugin, book theme)
index.md                      # template report, TODO-driven
Containerfile                 # build instructions for the analysis image
requirements.txt              # Python dependencies baked into that image
.gitignore                    # Python + lightcone state + `_build/`
.lightcone/
  lightcone.yaml              # project state (scratch root, …)
results/                      # placeholder; populated by `lc run`
src/                          # placeholder; where recipe scripts go
universes/
  baseline.yaml               # the default universe; add more via `astra universe generate -n …`
.claude/                      # bundled Claude Code plugin
  skills/, agents/, scripts/, templates/
  settings.json               # the chosen permission tier + session hooks
.venv/                        # Python venv (skipped with --no-venv)
.git/                         # git repository (skipped with --no-git)
```

The spec scaffold (`astra.yaml`, `universes/baseline.yaml`, the base
`.gitignore`, `src/`) is delegated to `astra init`; everything else is
layered on by `lc init`.

`lc init` refuses to run if `DIRECTORY/astra.yaml` already exists.

## The report scaffold

`myst.yml` + `index.md` are a template [MyST](https://mystmd.org/)
report, pre-wired to the **MySTRA** plugin. The plugin resolves
references *against `astra.yaml`*, so the prose never hard-codes a
measured value:

- `{astra}` as a role — an inline mention, e.g.
  ``{astra}`decisions.example_method` ``.
- `{astra}` as a directive — a block embed, e.g. `:::{astra} outputs`
  to render the analysis's outputs.
- `{astra:value}` — a live number pulled from a materialized output,
  e.g. ``{astra:value}`outputs.main_result` ``.

The skeleton has Introduction / Methods / Results sections marked
`TODO:`, referencing the boilerplate ids `example_method` and
`main_result`. Preview it with:

```bash
npm i -g mystmd   # once
myst start
```

Builds land in `_build/`, which the scaffolded `.gitignore` already
excludes.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--no-git` | off | Skip `git init`. |
| `--no-venv` | off | Skip `python -m venv .venv`. |
| `--permissions {yolo,recommended,minimal}` | `recommended` | Which `.claude/settings.json` permission tier to install. |
| `--scratch PATH` | the site default (e.g. `$SCRATCH` on Perlmutter) | Scratch root for Snakemake state, Dask spill, and run locks. Shell expressions like `$SCRATCH` are kept verbatim in `.lightcone/lightcone.yaml` and expanded at run time. |

> The historical `--target`, `--existing-project`, and `--sub-analysis`
> flags have been removed. For migrating an existing project, run
> `lc init` in a fresh directory and use the `/lc-from-code` skill from
> inside Claude Code.

## Permission tiers

| Tier | Allowed | Ask first | Denied |
|------|---------|-----------|--------|
| `yolo` | `Bash(*)`, `Edit`, `Read`, `Write`, `WebSearch`, `WebFetch`, `mcp__*` | — | — |
| `recommended` | `Read`, `Edit`, `Write`, `Bash(*)`, `WebSearch`, `WebFetch` | `Edit`/`Write` under `/scratch` and `/pscratch` | Edits to `~/.ssh`, `~/.aws`, `~/.gnupg`; `sudo`, `rm -rf`, `rm -fr`, `git push`. |
| `minimal` | `Read` | — | Everything else. |

Scratch paths are `ask`, not `deny`: on HPC the project itself often
lives in `$SCRATCH`, so the agent legitimately needs to write there —
you just get a prompt first.

The tiers are defined as `PERMISSION_TIERS` in
`src/lightcone/cli/commands.py` — adjust there if you want to add a tier
or change defaults.

## Examples

```bash
lc init                                # scaffold in cwd, recommended tier
lc init my-analysis                    # scaffold in ./my-analysis
lc init my-analysis --no-git --no-venv # bare bones
lc init . --permissions yolo           # for autonomous loops you trust
lc init my-analysis --scratch '$SCRATCH'  # pin the scratch root (HPC)
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

To write up the result, edit `index.md` and preview it live:

```bash
myst start       # requires the MyST CLI: npm i -g mystmd
```
