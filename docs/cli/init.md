# lc init

Scaffold a new ASTRA project.

## Synopsis

```text
lc init [OPTIONS] [DIRECTORY]
```

`DIRECTORY` defaults to `.` (the current directory).

## What it creates

`lc init` delegates the spec scaffold to `astra init`
(`astra.yaml`, `universes/baseline.yaml`, base `.gitignore`, `src/`),
then layers on the lightcone-specific pieces. Inside `DIRECTORY`
(creating it if needed):

```text
astra.yaml                    # tiny boilerplate spec with one example output
universes/
  baseline.yaml               # the default universe
src/                          # analysis code lives here
Containerfile                 # project image; referenced by `container:` in astra.yaml
requirements.txt              # numpy, pandas, and a pinned lightcone-cli
.gitignore                    # Python + lightcone state + MyST build output
.lightcone/
  lightcone.yaml              # project config: { target: local } (+ scratch_root if --scratch)
results/                      # placeholder; populated by `lc run`
myst.yml                      # MyST report configuration (MySTRA plugin)
index.md                      # template report referencing astra.yaml elements
.venv/                        # Python venv with lightcone-cli installed (skipped with --no-venv)
```

`lc init` refuses to run if `DIRECTORY/astra.yaml` already exists.

The boilerplate `container: python:3.12-slim` written by `astra init`
is rewritten to `container: Containerfile`, so the project builds its
own content-addressed image and dependencies can evolve under
`lc build`.

On a known site (NERSC Perlmutter, a lightcone JupyterHub), `lc init`
also prints the detected site and the scratch root that `lc run` will
use for its operational state.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--no-git` | off | Skip `git init`. |
| `--no-venv` | off | Skip venv creation (`uv venv` if available, else `python -m venv`). |
| `--scratch <path>` | site default | Scratch root for snakemake state, dask spill, and run locks. Shell expressions like `$SCRATCH` are kept verbatim and expanded at run time. |

> The historical `--target`, `--existing-project`, `--sub-analysis`,
> and `--permissions` flags have been removed; today's `lc init` only
> knows the three flags above.

## Examples

```bash
lc init                                # scaffold in cwd
lc init my-analysis                    # scaffold in ./my-analysis
lc init my-analysis --no-git --no-venv # bare bones
lc init . --scratch '$SCRATCH'         # pin the scratch root explicitly
```

## Next steps

```bash
cd my-analysis
# Describe your analysis in astra.yaml — inputs, outputs, recipes,
# decisions. ASTRA specs are plain YAML; write them by hand or draft
# them with your AI coding assistant of choice.
lc run           # materialize the outputs
lc status        # check what's ok / stale / missing
myst start       # preview the report (requires: npm i -g mystmd)
```
