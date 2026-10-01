# How Lightcone works

Three parts share one project directory: the agent plugin writes the analysis, `lc` runs it, and Lightcone Lab shows it.

You describe what you want to learn. Your agent, guided by the plugin, writes it down as `astra.yaml`, [your analysis file](../concepts/analysis-file.md), with the scripts its recipes call. `lc materialize` runs each recipe in the project's locked environment, under a sandbox, and commits every output with a manifest of what made it ([outputs and provenance](../concepts/provenance.md)). Lab reads the same files and the same states `lc status` reports.

```text
you and your agent (with the plugin)
        │ write
        ▼
astra.yaml + scripts
        │ run by
        ▼
lc materialize ──▶ results/ + a manifest per output, committed
                          │ browsed in
                          ▼
                    Lightcone Lab
```

## Why it's built this way

Scientific results depend on methodological choices: which data to
include, how to handle outliers, which prior to assume. In ordinary
research code those choices are scattered across notebooks, scripts,
comments, and memory, which makes results hard to reproduce, audit, and
extend. Lightcone keeps the technical and the conceptual pieces of your
work together: the code, data, and environment behind every result, and
the decisions and evidence behind every choice. All of it is tracked,
checked where it can be, and tied to each result as its provenance.

## What you get

- **Every choice on the record.** Decisions name the options that were
  considered and why one was chosen. Claims taken from papers carry quotes
  that are checked against the paper itself.
- **Locked, isolated execution.** The environment is pinned, and recipes
  run under a sandbox that keeps undeclared files out and stray writes
  contained.
- **Laptop to cluster.** The same project runs on your machine, in a
  container, or across a SLURM allocation.
- **Ready to publish.** Declare a license and Lightcone keeps an RO-Crate
  of the project and its provenance up to date, ready to archive or
  deposit.

!!! abstract "Planned page"
    What this page will cover:

    - Which part does what, and which one you touch at each stage
    - The project as a git repository: outputs committed with the code that made them
    - The two paths, with your agent or by hand, and how they meet at `lc`
    - Where the other concepts fit: [decisions and universes](../concepts/universes.md), [evidence](../concepts/evidence.md)

    Draws on: [Outputs and provenance](../concepts/provenance.md), [Installation](../reference/installation.md), [Agent plugin](../reference/plugin.md)
