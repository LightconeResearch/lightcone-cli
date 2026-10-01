# Tutorial

Measure the dark energy content of the universe from 580 supernovae, with
your agent doing the work and Lightcone keeping the record.

!!! abstract "Planned changes"
    - Fill in the time estimate once the `lc` chapters have been run end to end.
    - Publish the companion example repository and link it from each chapter.

## What you'll build

One number: **Ω_Λ**, the dark energy density of a flat ΛCDM universe, fitted
to the 580 Type Ia supernovae of the Union2.1 compilation
([Suzuki et al. 2012](https://arxiv.org/abs/1105.3470)). Mechanically, it is a
least-squares fit with a single free parameter.

The fit is the easy part. The tutorial is about what it takes to put that
number beside the published one: an analysis file that says what was done, outputs
made with a record of how, a claim checked against the paper, and one
methodological decision that turns out to matter more than the fit itself.

## What you'll learn

- Scope an analysis with your agent, into an analysis file.
- Turn a working directory into a reproducible project with `lc init`.
- Make outputs with `lc materialize`, each committed with a manifest of how it
  was made, and read their state with `lc status`.
- Check a result against a paper, with the quote verified against the PDF.
- Add a decision, and compare the universes it creates.
- Browse the project in Lightcone Lab and publish it.

## Before you start

- Finish the [install](../reference/installation.md): `lc`, `uv`, an agent, and
  the `lightcone` plugin.
- We recommend the [quickstart](../get-started/quickstart.md) first. It is
  shorter, and this tutorial assumes you have seen `lc` run once.
- No cosmology required. The agent explains what it needs to.

**Time:** TBD.

## How to read it

We recommend that you let your agent do the work. Everything in a `text` block
is a prompt: paste it into the agent, not into your shell. Shell commands
appear only where you are meant to look at something yourself.

The tutorial is intentionally sparse — we leave your own agent to do some of
the explaining for us.

## Chapters

| Chapter | At the end of it, you have |
|---|---|
| [1. The data](1-data.md) | A working directory, the Union2.1 compilation in `data/`, and an agent that has read it. |
| [2. Scope the analysis](2-scope.md) | An `astra.yaml` declaring the data, two outputs, and the recipe that makes each. |
| [3. Make it reproducible](3-reproduce.md) | A Lightcone project, a first Ω_Λ and a Hubble diagram, each committed with the code that made it. |
| [4. Check it against the paper](4-evidence.md) | The paper's own Ω_Λ recorded as a prior insight, its quote verified against the PDF. |
| [5. Add a decision](5-decision.md) | Two universes, statistical and statistical + systematic, and the reason only one of them compares with the paper. |
| [6. Inspect and publish](6-publish.md) | The project browsed in Lightcone Lab, a MyST report, and an RO-Crate for anyone who wants to check your work. |

## Companion repository

!!! note "Planned"
    A companion example repository, with a git tag per chapter, is planned: start
    from any chapter, or diff your run against ours. It does not exist yet.
