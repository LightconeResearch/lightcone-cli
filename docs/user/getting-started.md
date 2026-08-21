# Getting Started

Let's go from nothing on your disk to a working, reproducible analysis.
You can read this top to bottom without running anything, or follow along —
every command is copy-paste ready.

**What you'll build:** a small two-output analysis that fits a linear model on
a public dataset and sweeps one methodological decision (whether to standardize
features). The result is two universes, `baseline` and `raw`, each with its
own `r2` metric and `fit_plot` figure — a clean comparison ready for a paper
figure.

Make sure you've finished the [install](install.md) first.

## 1. Create a project

```bash
lc init r2-decision-demo
cd r2-decision-demo
```

`lc init` converges the directory to a small, opinionated layout and
stops; it doesn't ask any questions, and it's idempotent — re-running
it later only fills in whatever is missing.

```
r2-decision-demo/
├── astra.yaml          # the spec — this is where everything lives
├── .gitignore
├── .git                # initialized git repository (skip with --no-git)
├── .venv/              # Python virtual env with the analysis dependencies (skip with --no-venv)
├── .lightcone/         # internal scratchpad — don't edit by hand
├── Containerfile       # build instructions for the project container
├── requirements.txt    # software dependencies
├── myst.yml            # MyST report configuration
├── index.md            # template report that references the spec
├── universes/
│   └── baseline.yaml   # one universe, built from decision defaults
└── results/
    └── README.md       # outputs materialize here via `lc run`
```

The file you'll actually work in:

**`astra.yaml`** — the single source of truth for your analysis. Inputs,
outputs, methodological decisions, recipes. Everything else lightcone-cli does
is downstream of this file. The boilerplate from `lc init` has one example
output and an example decision — enough to run `lc run` and see something
materialize, but not yet a real analysis.

ASTRA specs are plain YAML, designed to be easy for both humans and AI
assistants to write. In this guide you'll write one by hand — it's short.

## 2. Write the spec

Open `astra.yaml` and replace the boilerplate with our analysis: a linear
regression on sklearn's bundled diabetes dataset, with one decision — whether
to standardize features before fitting.

```yaml
version: "0.0.13"   # ASTRA spec version — keep what the scaffold wrote
name: "R² with and without feature standardization"
description: "Linear regression on the diabetes dataset, sweeping the standardization choice."
container: Containerfile

inputs: []          # the diabetes dataset ships with scikit-learn

decisions:
  standardize:
    label: "Feature standardization"
    rationale: "Standardizing changes coefficient scales and can shift R² for ridge-like models."
    default: standardized
    options:
      standardized: { label: "StandardScaler before fit" }
      raw: { label: "No preprocessing" }

outputs:
  - id: r2
    type: metric
    description: "Coefficient of determination on the test split."
    decisions: [standardize]
    recipe:
      command: python src/fit.py --standardize {decisions.standardize} --output {output}
  - id: fit_plot
    type: figure
    description: "Predicted vs true scatter."
    inputs: [r2]
    recipe:
      command: python src/plot.py --r2_dir {inputs.r2} --output {output}
```

A few things to notice:

- Each output declares what it depends on: `r2` depends on the
  `standardize` decision, `fit_plot` depends on the sibling output `r2`.
- Recipes reference those dependencies through placeholders —
  `{decisions.standardize}`, `{inputs.r2}`, `{output}` — which `lc run`
  expands at execution time. `{output}` is the output's own results
  directory.
- The decision's options aren't hardcoded anywhere in code; the scripts
  will take them as command-line arguments.

Check the spec is well-formed:

```bash
astra validate astra.yaml
```

(`astra` is the spec-side CLI; it ships with `astra-tools`, a dependency of
lightcone-cli.)

## 3. Write the scripts

Two short scripts, in a `src/` directory (`mkdir src` — the scaffold
doesn't create it; where code lives is your choice, the recipes above
just happen to point there). First `src/fit.py` — fits the model,
writes the R² metric and the test-set predictions:

```python
import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.datasets import load_diabetes
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

parser = argparse.ArgumentParser()
parser.add_argument("--standardize", choices=["standardized", "raw"], required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

X, y = load_diabetes(return_X_y=True)
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.25, random_state=0
)
if args.standardize == "standardized":
    scaler = StandardScaler().fit(X_train)
    X_train, X_test = scaler.transform(X_train), scaler.transform(X_test)

model = LinearRegression().fit(X_train, y_train)

out = Path(args.output)
out.mkdir(parents=True, exist_ok=True)
(out / "r2.json").write_text(json.dumps({"r2": model.score(X_test, y_test)}))
np.savez(out / "predictions.npz", y_true=y_test, y_pred=model.predict(X_test))
```

Then `src/plot.py` — reads the upstream output directory, makes the figure:

```python
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--r2_dir", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

r2_dir = Path(args.r2_dir)
r2 = json.loads((r2_dir / "r2.json").read_text())["r2"]
data = np.load(r2_dir / "predictions.npz")

fig, ax = plt.subplots()
ax.scatter(data["y_true"], data["y_pred"], s=12)
ax.set_xlabel("true")
ax.set_ylabel("predicted")
ax.set_title(f"R² = {r2:.3f}")

out = Path(args.output)
out.mkdir(parents=True, exist_ok=True)
fig.savefig(out / "fit_plot.png", dpi=150)
```

Finally, add the dependencies to `requirements.txt`:

```text
scikit-learn
matplotlib
```

The Containerfile installs `requirements.txt` into the project image, so
that's all it takes — `lc run` rebuilds the image automatically when the
dependency files change. (If you're running without a container runtime,
install the same packages into `.venv` instead.)

## 4. Add the second universe

`lc init` scaffolded `universes/baseline.yaml`. Point it at our decision's
default:

```yaml
id: baseline
description: "Standardized features (the default)."
decisions:
  standardize: standardized
```

And add the sweep — `universes/raw.yaml`:

```yaml
id: raw
description: "No preprocessing before the fit."
decisions:
  standardize: raw
```

Each universe is one complete selection of decision values; its results
materialize to `results/<universe>/<output_id>/`.

## 5. Run it

```bash
lc run
```

`lc run` materializes every universe it finds under `universes/`. To run just
one, or just one output:

```bash
lc run --universe baseline
lc run r2
```

Then check where things stand:

```bash
lc status
```

Expected output:

```
Universe baseline
  ✓ ok    r2
  ✓ ok    fit_plot

Universe raw
  ✓ ok    r2
  ✓ ok    fit_plot
```

Your comparison is on disk: `results/baseline/r2/r2.json` vs
`results/raw/r2/r2.json`, with a figure next to each.

If a recipe fails, `lc run` surfaces the error; fix the script or the spec
and rerun — only the affected outputs re-execute. Commit as you go so your
`git log` is a clean record of the build.

## 6. Verify integrity

```bash
lc verify
```

This recomputes data hashes for every output and walks the input chain back to
declare whether anything has been tampered with since materialization. Useful
pre-publication, when archiving a project, or any time you want a stronger
guarantee than `lc status`.

## What just happened

- `astra.yaml` was the only place your analysis was *described* — inputs,
  outputs, the decision, and the recipes all live there.
- The scripts take decision values as plain command-line arguments, so
  nothing methodological is hardcoded.
- `lc run` generated `.lightcone/Snakefile` from your spec, dispatched each
  rule through Snakemake, and wrote a per-output sidecar manifest recording the
  recipe, container image, decisions, input hashes, and output hash.
- `lc status` and `lc verify` rely on those manifests — they don't re-execute
  anything; they just check.

If your laptop dies tomorrow and you `git clone` the repo on a fresh machine
and run `lc run`, you'll get bit-identical results.

## Where to next

- [Running on a Cluster](cluster.md) — take the same project to SLURM.
- [Troubleshooting](troubleshooting.md) — when something goes sideways.
- [Glossary](glossary.md) — terms like universe, decision, and manifest in
  plain language.
- The [ASTRA docs](https://astra-spec.org/latest/) — the full spec:
  sub-analyses, prior insights, findings, and evidence.
