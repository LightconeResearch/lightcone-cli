# Welcome

`lightcone-cli` is a small toolchain that turns a research question into
a reproducible analysis. You describe what you're trying to learn,
Claude Code helps you turn that into a precise specification, and the
`lc` command line keeps the resulting code, decisions, and outputs in
sync — forever.

You don't need to write Python or YAML by hand. The agent handles the
implementation; you stay in charge of the scientific choices.

## What this guide covers

- [Install](install.md) — get `lc` and Claude Code running on your
  machine.
- [Getting Started](getting-started.md) — your first `lc init` and
  what every directory means.
- [The Claude Code Workflow](claude-workflow.md) — `/lc-new`,
  `/lc-build`, `/lc-verify`, `/lc-migrate`, `/lc-feedback` — what each
  one does and when to reach for it.
- [Tutorial: Your First Analysis](tutorial.md) — an end-to-end worked
  example, written so you can read it without running anything.
- [Multiverse Analyses](multiverse.md) — how to explore alternative
  defensible choices side by side.
- [Running on a Cluster](cluster.md) — taking your analysis to a SLURM
  HPC system.
- [Troubleshooting](troubleshooting.md) — common issues and how to
  unstick them.
- [Glossary](glossary.md) — the terms that show up everywhere
  (universe, decision, manifest, …) explained in plain language.

## What you'll do, in three lines

!!! tip "Quick start"
    === "uv"
        ```bash
        uv tool install lightcone-cli
        lc init my-analysis && cd my-analysis
        claude                                 # then, inside Claude Code: /lc-new
        ```

    === "pip"
        ```bash
        pip install lightcone-cli
        lc init my-analysis && cd my-analysis
        claude                                # then, inside Claude Code: /lc-new
        ```

That's the shortest possible path. The rest of the guide is the
unhurried version.

## What lightcone-cli is *not*

- **A statistics package.** It runs your code; it doesn't compute
  things itself.
- **A workflow language.** Recipes in `astra.yaml` are short shell or
  Python commands, not a DSL. There's no learning curve beyond what's
  in the [tutorial](tutorial.md).
- **An IDE.** `lc` is a command-line tool; the agent surface lives
  inside Claude Code.

If you'd rather skim the design and architecture, the
[maintainer docs](../index.md) are the other half of this site.
