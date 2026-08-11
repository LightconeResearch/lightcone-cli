# lc init

Converge a directory into an ASTRA project. Idempotent — safe to run
at any time, on an empty directory, a half-scaffolded one, or an
existing project.

## Synopsis

```text
lc init [OPTIONS] [DIRECTORY]
```

`DIRECTORY` defaults to `.` (the current directory).

## Convergence semantics

Each run creates whatever is missing, repairs the pieces lightcone
manages, and never overwrites files you own:

- **Created if missing** — every item in the tree below. A directory
  that already holds an `astra.yaml` is *adopted*: the spec is left
  untouched and only the missing lightcone pieces are added.
- **Repaired** — content lightcone itself wrote that later releases
  superseded: a Containerfile matching an old scaffold template verbatim
  is upgraded to the current one, the legacy `lightcone-cli` pin is
  stripped from `requirements.txt`, the legacy blanket `results/`
  gitignore rule is narrowed so `results/README.md` stays tracked, the
  managed `.gitignore` block is appended exactly once (keyed on its
  `# lightcone-cli` marker), and the stored scratch root is converged
  when `--scratch` differs from the project config.
- **Warned about** — problems `lc` can see but must not fix, reported
  in the `warnings` list: an unsupported directory `COPY` in a
  hand-edited Containerfile, an unparseable `.lightcone/lightcone.yaml`.
  Warnings don't affect the exit code.
- **Never touched** — anything you authored. Repairs are conservative
  by construction: templates are replaced only verbatim-for-verbatim,
  known lines are filtered, everything else is left alone.

`--check` reports what a run *would* create or repair, writes nothing,
and exits `1` when the project is not converged. `--json` prints the
report as machine-readable JSON:

```json
{
  "converged": false,
  "created": ["Containerfile"],
  "repaired": [".gitignore"],
  "unchanged": ["astra.yaml", "..."],
  "warnings": []
}
```

Agents driving a project should run `lc init` (or `lc init --check
--json`) at the start of a session to make sure the directory is
workable.

## What it creates

The spec scaffold follows the `astra init` boilerplate
(`astra.yaml`, `universes/baseline.yaml`), with the
lightcone-specific pieces layered on top. Inside `DIRECTORY`
(creating it if needed):

```text
astra.yaml                    # tiny boilerplate spec with one example output
universes/
  baseline.yaml               # the default universe
Containerfile                 # project image; referenced by `container:` in astra.yaml
requirements.txt              # analysis dependencies (numpy, pandas to start)
.gitignore                    # Python + lightcone state + MyST build output
.lightcone/
  lightcone.yaml              # project config: { target: local } (+ scratch_root if --scratch)
results/
  README.md                   # the materialization contract; outputs land here via `lc run`
myst.yml                      # MyST report configuration (MySTRA plugin)
index.md                      # template report referencing astra.yaml elements
.venv/                        # Python venv with the analysis dependencies (skipped with --no-venv)
```

The boilerplate `container: python:3.12-slim` from the astra
boilerplate is rewritten to `container: Containerfile`, so the project
builds its own content-addressed image and dependencies can evolve
under `lc build`.

On a known site (NERSC Perlmutter, a lightcone JupyterHub), `lc init`
also prints the detected site and the scratch root that `lc run` will
use for its operational state.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--check` | off | Report drift without writing; exit 1 if not converged. |
| `--json` | off | Emit the convergence report as JSON on stdout. |
| `--no-git` | off | Skip `git init`. |
| `--no-venv` | off | Skip venv creation (`uv venv` if available, else `python -m venv`). |
| `--scratch <path>` | site default | Scratch root for snakemake state, dask spill, and run locks. Shell expressions like `$SCRATCH` are kept verbatim and expanded at run time. |

> The historical `--target`, `--existing-project`, `--sub-analysis`,
> and `--permissions` flags have been removed.

## Examples

```bash
lc init                                # converge cwd
lc init my-analysis                    # scaffold/converge ./my-analysis
lc init my-analysis --no-git --no-venv # bare bones
lc init . --scratch '$SCRATCH'         # pin the scratch root explicitly
lc init --check --json                 # is this directory workable? (for scripts/agents)
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
