# Welcome to the user guide

`lightcone-cli` is a small toolchain that turns a research question into
a reproducible analysis. You describe what you're trying to learn as a
precise specification — an `astra.yaml` file following the
[**ASTRA**][astra] schema — and the `lc` command line keeps the
resulting code, environments, decisions, and outputs in sync.

ASTRA specs are plain YAML, designed to be easy for both humans and AI
assistants to write. However the spec gets written, **you stay in charge
of the scientific choices** — every methodological decision is declared
in the open, and `lc` records exactly what produced every result: the
recipe, the decisions, the input data, the environment, and the commit.

## What this guide covers

- [Install](install.md) — get the `lc` command line running on your
  machine or on a cluster.
- [Getting Started](getting-started.md) — create your first project,
  build it end-to-end, and understand what each piece does.
- [Core Concepts](concepts.md) — the model behind the tool: what the
  states mean, why everything is committed, and how the two execution
  modes differ.
- [Running on a Cluster](cluster.md) — taking your analysis to a SLURM
  HPC system.
- [Troubleshooting](troubleshooting.md) — common issues and how to
  unstick them.
- [Glossary](glossary.md) — the terms that show up everywhere
  (universe, decision, manifest, …) explained in plain language.

## What you'll do, in a handful of lines

!!! tip "Quick start"

    === "uv"
        ```bash
        uv tool install lightcone-cli
        lc init my-analysis && cd my-analysis
        # describe your analysis in astra.yaml, write your scripts,
        # declare what they import (uv add numpy ...), then:
        git add -A && git commit -m "First analysis"
        lc materialize
        ```

    === "pip"
        ```bash
        pip install lightcone-cli
        lc init my-analysis && cd my-analysis
        # describe your analysis in astra.yaml, write your scripts,
        # declare what they import (uv add numpy ...), then:
        git add -A && git commit -m "First analysis"
        lc materialize
        ```

That's the shortest possible path. The rest of the guide is the
unhurried version — and the commit is not ceremony: every output is
committed together with the code that produced it, which is why a build
starts from a clean tree.

## What lightcone-cli is *not*

- **A statistics package.** It runs your code; it doesn't compute
  things itself.
- **A workflow language.** Recipes in `astra.yaml` are short shell
  commands, not a DSL. There's no learning curve beyond what's in
  [Getting Started](getting-started.md).
- **An IDE.** `lc` is a command-line tool; write `astra.yaml` and your
  analysis code with whatever editor or tooling you prefer.

If you'd rather skim the design and architecture, the
[maintainer docs](../maintainer.md) are the other half of this site.

[astra]: https://astra-spec.org/latest/
