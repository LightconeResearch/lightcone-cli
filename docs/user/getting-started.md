# Getting Started

Let's go from nothing on your disk to a working, reproducible analysis.
You can read this top to bottom without running anything, or follow along —
every command is copy-paste ready.

**What you'll build:** a small two-output analysis that fits a line to a
noisy dataset and sweeps one methodological decision — whether points far
from an initial fit are kept or clipped. The result is two universes,
`baseline` and `robust`, each with its own fitted slope and figure, and a
project that ends published as an [RO-Crate](https://www.researchobject.org/ro-crate/).

Make sure you've finished the [install](install.md) first.

## 1. Create a project

```bash
lc init line-fit-demo
cd line-fit-demo
```

`lc init` converges the directory to a small, opinionated layout and
stops; it doesn't ask any questions, and it's idempotent — re-running
it later only fills in whatever is missing.

```
line-fit-demo/
├── astra.yaml          # the spec — this is where everything lives
├── pyproject.toml      # the project's environment: its dependencies…
├── .python-version     # …and the exact interpreter, locked by uv
├── uv.lock
├── .venv/              # built from the lock (local, never committed)
├── .git/               # a git repository, with git-annex initialized
├── .gitattributes      # the storage policy: what the annex carries
├── .gitignore
├── .datalad/           # dataset identity — the project is a DataLad dataset
├── data/               # declared input data lives here
├── results/            # outputs materialize here — lc's to write, not yours
├── universes/
│   └── baseline.yaml   # one universe, built from decision defaults
├── myst.yml            # MyST report configuration
└── index.md            # template report that references the spec
```

Two things are worth registering now:

- **The project is a git repository.** Every
  output `lc` makes is committed together with the code that produced
  it; large files ride in git-annex behind the scenes, but you only
  ever type ordinary `git add` and `git commit`.
- **The environment is the lock.** `pyproject.toml` + `uv.lock` define
  exactly what your recipes can import, and `.venv` is built from them.
  You'll add packages with `uv add` in a moment — never `pip install`.

The file you'll actually work in is **`astra.yaml`** — the single source
of truth for your analysis. Inputs, outputs, methodological decisions,
recipes: everything else lightcone-cli does is downstream of this file.

## 2. Add the data

A real project starts from a dataset; ours will generate a small one —
200 points on a line, with a few outliers thrown far off it:

```bash
python3 - <<'EOF'
import random
random.seed(0)
rows = ["x,y"]
for _ in range(200):
    x = random.uniform(0, 10)
    y = 2.5 * x + 1.0 + random.gauss(0, 1.5)
    if random.random() < 0.04:
        y += random.gauss(0, 15)
    rows.append(f"{x:.6f},{y:.6f}")
open("data/points.csv", "w").write("\n".join(rows) + "\n")
EOF
```

`data/` is where declared inputs live. When you commit, the
`.gitattributes` policy routes the file's bytes into git-annex
automatically — the file stays an ordinary readable, writable file in
your tree, and the repository stays light.

## 3. Write the spec

Open `astra.yaml` and replace the boilerplate with our analysis:

```yaml
version: "0.0.13"   # ASTRA schema version — keep what the scaffold wrote
name: "line_fit"
description: |
  Fit a straight line to a small synthetic dataset and sweep one
  methodological decision: whether points far from an initial fit are
  kept or clipped before the final fit.

inputs:
  - id: points
    type: data
    source: data/points.csv
    description: "200 synthetic (x, y) points, a few of them far off the line"

outputs:
  - id: fit
    type: metric
    format: json
    description: "Slope and intercept of the least-squares line"
    inputs: [points]
    decisions: [outliers]
    recipe:
      command: python src/fit.py --points {inputs.points} --outliers {decisions.outliers} --output {output}

  - id: fit_plot
    type: figure
    format: png
    description: "The points and the fitted line"
    inputs: [points, fit]
    recipe:
      command: python src/plot.py --points {inputs.points} --fit {inputs.fit} --output {output}

decisions:
  outliers:
    label: "Outlier handling"
    rationale: "A few points sit far off the line; keeping or clipping them shifts the slope."
    default: keep
    options:
      keep:
        label: "Keep every point"
      clip:
        label: "Drop points beyond 3 sigma of an initial fit"
```

A few things to notice:

- Each output declares its full dependency contract: `fit` depends on
  the `points` input and the `outliers` decision; `fit_plot` depends on
  `points` and on the sibling output `fit`. That contract is how `lc`
  orders the build — and how it knows what to rebuild when something
  changes.
- Recipes reference those dependencies through placeholders —
  `{inputs.points}`, `{decisions.outliers}`, `{output}` — which are
  expanded at execution time. `{output}` is the output's own file,
  `results/<universe>/<output_id>.<format>`; the engine creates the
  directory before the recipe runs, and the recipe writes that one path.
- Each output declares a `format` — the extension its artifact is
  written with. It is what names the file, so a consumer knows what an
  output *is* from the spec alone, and one output is always one file.
- The decision's options aren't hardcoded anywhere in code; the scripts
  will take them as command-line arguments.

`universes/baseline.yaml` was scaffolded against the boilerplate spec,
so point it at our decision instead:

```yaml
id: baseline
description: "Every point kept — the decision defaults."
decisions:
  outliers: keep
```

Each universe is one complete selection of decision values; its results
materialize to `results/<universe>/<output_id>.<format>`.

Check the spec is well-formed:

```bash
astra validate astra.yaml
```

(`astra` is the spec-side CLI; it ships with `astra-tools`, a dependency
of lightcone-cli.)

## 4. Write the scripts

Two short scripts, in a `src/` directory (`mkdir src` — the scaffold
doesn't create it; where code lives is your choice, the recipes above
just happen to point there). First `src/fit.py`:

```python
import argparse
import json
from pathlib import Path

import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--points", required=True)
parser.add_argument("--outliers", choices=["keep", "clip"], required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

x, y = np.loadtxt(args.points, delimiter=",", skiprows=1, unpack=True)
if args.outliers == "clip":
    slope, intercept = np.polyfit(x, y, 1)
    residuals = y - (slope * x + intercept)
    mask = np.abs(residuals) < 3 * residuals.std()
    x, y = x[mask], y[mask]
slope, intercept = np.polyfit(x, y, 1)

out = Path(args.output)
(out / "fit.json").write_text(
    json.dumps({"slope": slope, "intercept": intercept, "n_used": len(x)}, indent=2)
)
```

Then `src/plot.py` — reads the upstream output's file, makes the
figure:

```python
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--points", required=True)
parser.add_argument("--fit", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

x, y = np.loadtxt(args.points, delimiter=",", skiprows=1, unpack=True)
fit = json.loads((Path(args.fit) / "fit.json").read_text())

fig, ax = plt.subplots()
ax.scatter(x, y, s=12)
xs = np.linspace(x.min(), x.max(), 2)
ax.plot(xs, fit["slope"] * xs + fit["intercept"], color="C1")
ax.set_xlabel("x")
ax.set_ylabel("y")
ax.set_title(f"slope = {fit['slope']:.3f}")
fig.savefig(Path(args.output) / "fit_plot.png", dpi=150)
```

Both scripts import from the project's locked environment, so declare
what they need:

```bash
uv add numpy matplotlib
```

That one command updates `pyproject.toml`, re-locks `uv.lock`, and syncs
`.venv`. It's the only way packages reach a recipe — recipes run
sandboxed in the locked environment, so a stray `pip install` on your
machine changes nothing they can see. That's a feature: the lock *is*
the record of what your results were computed with.

## 5. Materialize

Commit, then build:

```bash
git add -A && git commit -m "Line-fit analysis"
lc materialize
```

The commit isn't ceremony — every output is committed together with the
code that produced it, so a build refuses to start from a tree with
uncommitted edits (it wouldn't be able to say what code ran). Then:

```
  ✓ made baseline/fit
  ✓ made baseline/fit_plot
  ! no [project].license in pyproject.toml, so no RO-Crate publication
    view is maintained — declare one to enable it

✓ Made 2 output(s) in /home/you/line-fit-demo
```

(We'll come back to that license line in step 7.) Each output landed in
`results/baseline/<output_id>.<format>` next to a
`.<output_id>.manifest.json` —
a manifest recording the recipe, the decisions, the input hashes, the
environment, and the commit — and was committed with a run record that
`datalad rerun` can replay. Look at `git log`: the build wrote history,
not just files.

Check where things stand any time:

```bash
lc status
```

```
  mode:    direct
  sandbox: landlock (fs: declared, network: allowed)

  · current  baseline/fit       a3f1f11
  · current  baseline/fit_plot  a3f1f11

2 current
```

The commit column is the answer to "which code made this?" — for every
output, current or not. And `lc materialize` is idempotent: run it again
and it reports the project is up to date without executing anything.

## 6. Sweep the decision

Add the second universe — `universes/robust.yaml`:

```yaml
id: robust
description: "Points beyond 3 sigma of an initial fit are dropped."
decisions:
  outliers: clip
```

Commit and materialize again:

```bash
git add -A && git commit -m "Add the robust universe"
lc materialize
```

```
  ✓ made robust/fit
  ✓ made robust/fit_plot
  · up to date baseline/fit
  · up to date baseline/fit_plot

✓ Made 2 output(s) in /home/you/line-fit-demo
```

Only the new universe's outputs ran — `baseline` was already exactly
what the spec asks for, so it wasn't touched. Your comparison is on
disk: with this guide's synthetic dataset, clipping drops 4 points and
moves the slope from 2.414 to 2.450 — visibly closer to the true 2.5
the data was generated with.

If a recipe fails, `lc materialize` reports which output failed and why,
and leaves the tree as clean as it found it; fix the script or the spec,
commit, and rerun — only the affected outputs re-execute.

## 7. Publish

RO-Crate requires a license, so declaring one is how you tell `lc` the
project is meant for the outside world. Add one line under `[project]`
in `pyproject.toml`:

```toml
license = "CC-BY-4.0"
```

then commit and materialize once more:

```bash
git add -A && git commit -m "Declare a license"
lc materialize
```

Nothing is rebuilt — but `ro-crate-metadata.json` appears at the project
root and is committed automatically. From here on, every materialize
keeps it in line with the repository: the project *is* the crate, and
depositing it is just `git archive` (or `datalad export-archive`) on a
repository you already have.

## What just happened

- `astra.yaml` was the only place your analysis was *described* —
  inputs, outputs, the decision, and the recipes all live there.
- The scripts take decision values as plain command-line arguments, so
  nothing methodological is hardcoded.
- `lc materialize` ran each recipe in the project's locked environment,
  sandboxed — free to write the directory its output lands in, and
  nothing else —
  and committed every output with a manifest and a re-runnable run
  record.
- `lc status` and `lc materialize --check` read those manifests — they
  don't re-execute anything; they just classify. An output is remade
  when the spec defines it differently than it was made, or when its
  declared inputs changed; an output whose *environment* has since
  moved is reported as `behind` and deliberately left alone — the
  manifest records exactly which environment and commit produced it.

Clone this repository on a fresh machine, run `lc init` (it rebuilds
the two pieces of local state git doesn't carry — the `.venv` and the
annex), then `lc materialize`: it reports up to date without fetching a
single data byte, because the provenance travels in git. The bytes
themselves follow with `git annex get` whenever you actually need them.

## Where to next

- [Core Concepts](concepts.md) — the model behind what you just did:
  the three states, the commit discipline, the two execution modes.
- [Running on a Cluster](cluster.md) — take the same project to SLURM.
- [Troubleshooting](troubleshooting.md) — when something goes sideways.
- [Glossary](glossary.md) — terms like universe, decision, and manifest
  in plain language.
- The [ASTRA docs](https://astra-spec.org/latest/) — the full spec:
  sub-analyses, prior insights, findings, and evidence.
