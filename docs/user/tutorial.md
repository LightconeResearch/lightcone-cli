# Tutorial: Your First Analysis

Let's build a tiny but real analysis from scratch. We'll fit a linear
model on a small public dataset, sweep one methodological decision, and
get to publishable, reproducible results.

You can read this top to bottom without typing anything; if you want to
follow along, every command is plain copy-paste.

## What we'll build

A two-output analysis:

- **`r2`** — the coefficient of determination of a linear model on a
  toy dataset.
- **`fit_plot`** — a scatter plot of predictions vs. truth.

We'll declare one **decision**: whether to standardize the features
before fitting. That gives us two universes, `standardized` and
`raw`, each with its own results.

## 1. Make the project

```bash
lc init r2-decision-demo
cd r2-decision-demo
claude
```

You're now in Claude Code, sitting in a fresh project. The first line
on the screen is the session start banner, which probably says "no
recipes yet."

## 2. Scope the analysis with `/lc-new`

Type:

```
/lc-new
```

The agent banner switches to **RESEARCH QUESTION** and asks something
like "What are you trying to learn?" Reply in plain prose:

> I want to know how much R² changes on the diabetes dataset depending
> on whether I standardize features before fitting a linear regression.

A few follow-ups will sharpen this. After Phase 1 your `astra.yaml`
already has a `name`, `description`, and `version` — open it in another
window if you're curious; it's <30 lines.

In Phase 2 (**ANALYSIS STRUCTURE**) the agent will ask about inputs,
outputs, and whether this should be one analysis or split into stages.
For our case, one analysis is the right answer:

- Input: `diabetes` (sklearn's bundled toy dataset).
- Output 1: `r2`, type `metric`.
- Output 2: `fit_plot`, type `figure`.

In Phase 3 (**DEEP DIVE**), if you want to skip literature for a tiny
demo, just say "skip the literature pass." The agent will still walk
you through identifying the decision: does it preprocess? what
options does it have? what's the default?

You'll end up with something like this in `astra.yaml`:

```yaml
version: "1.0"
name: "R² with and without feature standardization"
description: "Linear regression on the diabetes dataset, sweeping the standardization choice."

inputs: []

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
    recipe:
      command: python scripts/fit.py --standardize {standardize} --output {output[0]}
  - id: fit_plot
    type: figure
    description: "Predicted vs true scatter."
    recipe:
      command: python scripts/plot.py --r2_dir {input.r2} --output {output[0]}
      inputs: [r2]

container: Containerfile
```

Phase 4 (**FINALIZE**) runs `astra validate astra.yaml` and writes
`universes/baseline.yaml`. You're handed back a short summary table —
two outputs, one decision, zero prior insights.

The agent suggests `/clear` to free up context, then `/lc-build`. Take
its advice.

## 3. Build it with `/lc-build`

```
/clear
/lc-build
```

**Phase 1: plan.** The agent reads everything (spec, universe file,
empty `scripts/` dir, the references in `.claude/guides/`) and writes a
build plan to `.lightcone/plans/build-plan-baseline.md`. It might look
like this:

```
1. Add Python deps (scikit-learn, matplotlib) to requirements.txt
2. Write Containerfile if missing
3. scripts/fit.py — accepts --standardize {standardized,raw}, writes r2.json
4. scripts/plot.py — reads r2_dir, writes fit_plot.png
5. lc build to build the container
6. lc run --universe baseline
7. /lc-verify
```

It asks you to approve. Pick "Approve and start building."

**Phase 2: loop.** The agent works through the plan one item at a
time. You'll see lines like:

```
▶ scripts/fit.py — writing
▶ lc build — building image lc-r2-decision-demo-9a1f3...
▶ lc run accuracy --universe baseline
▶ ✓ ok    r2
▶ ▶ scripts/plot.py — writing
▶ ✓ ok    fit_plot
✓ build complete
```

The agent commits after each successful output, so your `git log` is a
clean record of the build.

## 4. Verify it with `/lc-verify`

```
/lc-verify
```

Read-only audit:

```
| Check                    | Status |
|--------------------------|--------|
| Spec validation          | ✓      |
| Materialization (2/2)    | ✓      |
| Decision-code alignment  | ✓      |
| Results match spec (2/2) | ✓      |
```

If anything fails, the agent suggests a fix. Re-run `/lc-build` or fix
by hand.

## 5. Add the second universe

The whole point of decisions is to sweep them. Drop out of Claude
Code (`Ctrl+D` or `/exit`) and create the second universe:

```bash
astra universe generate -n raw -d standardize=raw
```

That writes `universes/raw.yaml` selecting `standardize: raw`. Now
materialize it:

```bash
lc run --universe raw
lc status
```

You should see:

```
Universe baseline
  ✓ ok    r2
  ✓ ok    fit_plot
Universe raw
  ✓ ok    r2
  ✓ ok    fit_plot
```

Each universe has its own `results/<universe>/` tree. The two `r2.json`
files are the comparison your paper figure needs.

## 6. Verify integrity

```bash
lc verify
```

This recomputes data hashes for every output and walks the input chain
back to declare whether anything has been tampered with since
materialization. Useful pre-publication, useful when archiving a
project, useful any time you want a stronger guarantee than `lc
status`.

## What just happened

Concretely:

- `astra.yaml` was the only file you "wrote" — and the agent did most
  of the typing.
- The agent wrote `scripts/fit.py` and `scripts/plot.py` with
  argparse-driven decision injection.
- `lc run` generated `.lightcone/Snakefile` from your spec, dispatched
  each rule through Snakemake, and wrote a per-output sidecar manifest
  (`.lightcone-manifest.json`) recording the recipe, container image,
  decisions, input hashes, and output hash.
- `lc status` and `lc verify` rely on those manifests — they don't
  re-execute anything; they just check.

If your laptop dies tomorrow and you `git clone` the repo on a fresh
machine and `lc run` it, you'll get bit-identical results (modulo
floating-point nondeterminism in your numerical libraries).

## Where to next

- [Multiverse Analyses](multiverse.md) — sweep more than one decision.
- [Running on a Cluster](cluster.md) — take the same project to SLURM.
- [Troubleshooting](troubleshooting.md) — when something goes sideways.
