# Rigorous research, without the bookkeeping

Lightcone is a sidecar for your research: it attaches to the project
you're working on and to the agent you already use, stays out of the way
of how you work, and makes sure your work is rigorous and reproducible by
default.

- **Every decision, and why.** The methodological choices you make are
  written down with the options you considered, the reasons for the
  choice, and the papers that informed it.
- **Every result, and what made it.** Each result carries a record of the
  code, data, and environment that produced it, and anything made outside
  that record is flagged.
- **What a change affects.** When a decision or the data changes, you can
  see which results are out of date, and why.

We're not prescriptive about how much you use AI in your research: use an
agent for everything, for some things, or not at all. Our goal is to make
sure that the science you do, however you do it, is rigorous and
reproducible.

!!! warning "Beta development"
    Lightcone is in a **public beta**: expect some breaking changes between
    minor versions. Bug reports, design challenges, and use cases we don't
    cover yet are exactly what we want to hear. Please
    [open an issue](https://github.com/LightconeResearch/lightcone-cli/issues).

## Where to next

**New to Lightcone?** Follow the [Quickstart](get-started/quickstart.md)
to install Lightcone, set it up with your agent, and run your first
analysis.

Then, depending on how you work:

- [Work through a full analysis](tutorial/index.md), from question to
  published result.
- [Use Lightcone interactively in JupyterLab](guides/lab.md).
- [Set Lightcone up on a compute cluster](guides/cluster.md).
- [Write up your analysis with MyST](guides/publish.md).
- [Use Lightcone without an agent](guides/without-agent.md).

## Developer corner

Lightcone Research is committed to open source. The `lc` engine, the
agent plugins, and the ASTRA specification are developed in the open on
[GitHub](https://github.com/LightconeResearch), and the
[developer docs](developers/index.md) cover the architecture, the
engine's internals, and how to contribute.

Lightcone is built on [ASTRA](https://astra-spec.org), an open
specification for describing scientific analyses. Visit
[astra-spec.org](https://astra-spec.org) for the full specification, or
to contribute to it.
