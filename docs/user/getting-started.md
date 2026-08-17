# Getting started

This walkthrough takes an analysis from empty directory to verified,
provenance-tracked outputs.

## 1. Scaffold a project

```bash
lc init my-analysis
cd my-analysis
```

`lc init` converges the directory into an ASTRA project (it is
idempotent — safe to re-run any time, it never overwrites files you
own). The scaffold:

| File | Role |
|---|---|
| `astra.yaml` | The analysis specification — inputs, outputs, recipes, decisions |
| `pyproject.toml` + `uv.lock` + `.python-version` | The environment — the *only* place dependencies live |
| `.venv/` | The materialized environment (derived from the lock; never edited by hand) |
| `universes/baseline.yaml` | The default decision universe |
| `results/` | Where outputs materialize, one directory per universe and output |
| `AGENTS.md` | Working notes for AI agents (the boundary rules below) |
| `myst.yml` + `index.md` | A template report that references the analysis by path |

## 2. Add dependencies

Dependencies are managed with uv, and only with uv:

```bash
uv add numpy astropy matplotlib
```

This edits `pyproject.toml`, updates `uv.lock`, and syncs `.venv`. The
lock is the environment's identity: every output's manifest records
which lock it was produced under, and changing the lock marks every
materialized output stale — visibly, at decision time.

!!! tip "The boundary rule"
    A `ModuleNotFoundError` under `lc run` or `lc materialize` always
    means the same thing: add the package with `uv add`. Never install
    into another environment by hand.

## 3. Describe the analysis

Edit `astra.yaml`. A recipe's `command` is a template over the declared
inputs, decisions, and output directory:

```yaml
outputs:
  - id: hubble_fit
    type: metric
    inputs: [supernovae]
    decisions: [cosmology]
    recipe:
      command: >
        python src/fit.py --data {inputs.supernovae}
        --model {decisions.cosmology} --out {output}

inputs:
  - id: supernovae
    type: data
    source: data/union2.1.txt

decisions:
  cosmology:
    label: "Cosmological model"
    default: flat_lcdm
    options:
      flat_lcdm: {label: "Flat ΛCDM"}
      wcdm: {label: "wCDM"}
```

## 4. Probe interactively

`lc run <cmd>` runs any command inside **exactly** the recipe
environment — same interpreter, same locked packages, same sandbox:

```bash
lc run python -c "import astropy; print(astropy.__version__)"
lc run            # opens a shell in the recipe environment (sandboxed)
```

Probes never materialize outputs. That's the next verb.

## 5. Materialize

```bash
lc materialize                 # everything, all universes
lc materialize hubble_fit      # one output
lc materialize -u baseline     # one universe
```

Each recipe runs inside a sandbox restricted to its declared set (see
[The Environment](environment.md)), and each output directory gains a
`.lightcone-manifest.json` recording exactly how it was produced:
recipe, environment identity, decisions, input hashes, content hash,
and the enforcement it ran under.

## 6. Inspect and verify

```bash
lc status     # what's materialized / stale / missing, plus the env header
lc verify     # recompute hashes; walk the provenance chain
```

`lc status` never runs anything — it reads manifests, offline. A fresh
clone of a finished project reports its state without any setup.

## 7. Publish

```bash
lc export wrroc -o bundle.zip --zip
```

emits a [Workflow Run RO-Crate](https://www.researchobject.org/workflow-run-crate/)
bundle (manifests, spec, lockfile, data) ready for Zenodo or
WorkflowHub.

## The four verbs

| Verb | Does |
|---|---|
| `lc run <cmd>` | probes — arbitrary commands in the recipe environment |
| `lc materialize` | executes — produces outputs with manifests |
| `lc status` | reports — offline, manifest-driven |
| `lc verify` | audits — recomputes the provenance chain |

Outputs are materialized, not run: `lc run <output_id>` is an error
with a pointer to `lc materialize <output_id>`.
