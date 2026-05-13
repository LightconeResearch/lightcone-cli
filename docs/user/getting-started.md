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

`lc init` is a one-shot setup. It creates a small, opinionated directory
layout and stops; it doesn't ask any questions.

```
r2-decision-demo/
├── astra.yaml          # the spec — this is where everything lives
├── CLAUDE.md           # short note for the agent (resumes context across sessions)
├── .gitignore
├── .git                # initialized git repository (skip with --no-git)
├── .venv/              # Python virtual env (skip with --no-venv)
├── .claude/            # Claude Code plugin — skills, agents, hooks
├── .lightcone/         # internal scratchpad — don't edit by hand
├── Containerfile       # build instructions for a local testing container
├── requirements.txt    # software dependencies
├── universes/
├── src/
└── results/
```

The two files you'll actually look at:

**`astra.yaml`** — the single source of truth for your analysis. Inputs,
outputs, methodological decisions, recipes. Everything else lightcone-cli does
is downstream of this file. The boilerplate from `lc init` has one example
output and an empty decisions block — enough to run `lc run` and see something
materialize, but not yet a real analysis.

**`CLAUDE.md`** — a short note that tells Claude Code about the project. The
skills will update this as you go (filling in working notes, design context).
You can edit it by hand whenever you want.

## 2. Open Claude Code

```bash
claude
```

This opens an interactive session inside the project directory. Claude Code
reads `astra.yaml` and `CLAUDE.md` so it has context from the start.

## 3. The slash commands

Inside Claude Code, the `/lc-from-*` family is organized by what you're
starting from. We'll use `/lc-new` in this guide; the others work the same
way.

| Command | Use it when… |
|---------|--------------|
| `/lc-new` | You're starting from a research question and an empty `astra.yaml`. |
| `/lc-from-code` | You have an existing codebase you want wrapped in ASTRA. |
| `/lc-from-paper` | You have a published paper (DOI / arXiv ID) you want to reproduce. |
| `/lc-feedback` | Something broke and you want to file a GitHub issue without leaving the session. |

These are structured entry points for common starting situations. Once inside a
project you can also just describe what you're trying to do to Claude —
`astra.yaml`, `lc run`, and `lc verify` keep things tracked regardless of how
you got there.

## 4. Scope the analysis with `/lc-new`

Type:

```text
/lc-new
```

The agent banner switches to **RESEARCH QUESTION** and asks something like
"What are you trying to learn?" Reply in plain prose:

    I want to know how much R² changes on the diabetes dataset depending
    on whether I standardize features before fitting a linear regression.

A few follow-ups will sharpen this. After Phase 1 your `astra.yaml` already
has a `name`, `description`, and `version` — open it in another window if
you're curious; it's <30 lines.

In Phase 2 (**ANALYSIS STRUCTURE**) the agent asks about inputs, outputs, and
whether this should be one analysis or split into stages. For our case, one
analysis is right:

- Input: `diabetes` (sklearn's bundled toy dataset).
- Output 1: `r2`, type `metric`.
- Output 2: `fit_plot`, type `figure`.

In Phase 3 (**DEEP DIVE**), say "skip the literature pass" to keep this a
quick demo. The agent will still walk you through identifying the decision:
does it preprocess? what options? what's the default?

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

Phase 4 (**FINALIZE**) runs `astra validate astra.yaml`, writes
`universes/baseline.yaml`, and fills in the `narrative:` block. You're handed
back a short summary table — two outputs, one decision, zero prior insights.

The agent may suggest `/clear` to free up context. Take its advice.

## 5. Implement the spec

```text
/clear
Implement this analysis from astra.yaml. Write the scripts, run the baseline universe, and verify the result.
```

The agent reads the spec, the universe file, and the empty `scripts/` dir,
then makes an implementation checklist:

```text
1. Add Python deps (scikit-learn, matplotlib) to requirements.txt
2. Write Containerfile if missing
3. scripts/fit.py — accepts --standardize {standardized,raw}, writes r2.json
4. scripts/plot.py — reads r2_dir, writes fit_plot.png
5. lc run --universe baseline
6. lc status
7. astra validate astra.yaml
8. lc verify
```

It works through the checklist one item at a time. You'll see commands like:

```bash
lc run --universe baseline
lc status
```

Expected `lc status` output:

```
Universe baseline
  ✓ ok    r2
  ✓ ok    fit_plot
```

`lc verify` and `astra validate` should exit cleanly — no tampering, no broken
chains. If anything fails, ask the agent to fix the concrete error and rerun.

The agent commits after each successful output, so your `git log` is a clean
record of the build.

## 6. Verify integrity

```bash
lc verify
```

This recomputes data hashes for every output and walks the input chain back to
declare whether anything has been tampered with since materialization. Useful
pre-publication, when archiving a project, or any time you want a stronger
guarantee than `lc status`.

## What just happened

- `astra.yaml` was the only file you "wrote" — and the agent did most of the
  typing.
- The agent wrote `scripts/fit.py` and `scripts/plot.py` with argparse-driven
  decision injection.
- `lc run` generated `.lightcone/Snakefile` from your spec, dispatched each
  rule through Snakemake, and wrote a per-output sidecar manifest recording the
  recipe, container image, decisions, input hashes, and output hash.
- `lc status` and `lc verify` rely on those manifests — they don't re-execute
  anything; they just check.

If your laptop dies tomorrow and you `git clone` the repo on a fresh machine
and run `lc run`, you'll get bit-identical results.

## Where to next

- [The Agentic Workflow](agent-workflow.md) — what each slash command does in
  detail.
- [Running on a Cluster](cluster.md) — take the same project to SLURM.
- [Troubleshooting](troubleshooting.md) — when something goes sideways.
- [Glossary](glossary.md) — terms like universe, decision, and manifest in
  plain language.
