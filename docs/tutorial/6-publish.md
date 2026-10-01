# 6. Inspect and publish

Look at what you built, write it up, and make it something others can check.

!!! abstract "Planned chapter"
    New. Nothing below has been run yet: the prompts are drafts, and every place
    real output belongs is marked **To capture**.

    1. **Lightcone Lab.** Browse the project: both universes, their outputs, and
       the manifest and commit behind each.
    2. **The report.** Have the agent write the MyST report `lc init` scaffolded,
       referencing numbers from the analysis rather than typing them.
    3. **Publish.** Declare a license, so `lc materialize` maintains an RO-Crate
       of the project.
    4. **Going further.** Ported from the source tutorial as the close.

## Browse it in Lightcone Lab

!!! note "To capture"
    Opening this project in Lightcone Lab, and what to look at first: the two
    universes side by side, and the provenance behind each output. Screenshots.

See [browse a project in Lightcone Lab](../guides/lab.md).

## Write the report

`lc init` scaffolded a MyST report next to the analysis file: `myst.yml` and
`index.md`. The report references the analysis instead of restating it, so a
number in the prose is read from the output that holds it, and cannot go stale
behind your back.

```text
Write up the analysis in index.md: the question, the fit, the covariance
decision and why it matters, and the comparison with Suzuki et al. Reference
the numbers and the figure from the analysis rather than typing them.
```

!!! note "To capture"
    The report as the agent writes it, and a rendered preview.

## Publish it

Declaring a license is how you tell `lc` the project is meant for the outside
world. With one declared, `lc materialize` maintains `ro-crate-metadata.json`
at the project root, an [RO-Crate](https://www.researchobject.org/ro-crate/)
rendered from the repository and committed with it. Nothing is rebuilt. See
[publish a report](../guides/publish.md).

```text
Add a CC-BY-4.0 license to the project and materialize, so the RO-Crate is
maintained.
```

!!! note "To capture"
    The real `lc materialize` output, and `lc status` reporting the crate.

## Going further

Three more decisions are sitting in this analysis, none of them implemented
here:

- **Optimiser** — likely a null result, and worth recording as one.
- **Redshift range** — the high-z supernovae carry the longest lever arm and the
  worst systematics.
- **Dark energy model** — assume w = −1, or fit it. The weaker assumption, at
  the cost of a strong degeneracy with Ω_Λ.

Each is one more option, one more recipe argument, and one more reason written
down. [Add a decision and compare universes](../guides/decisions.md) covers the
mechanics.

The [agent plugin](../reference/plugin.md) page covers what the plugin ships,
and [how Lightcone works](../get-started/how-it-works.md) covers the model behind every
step you just took.

## You now have

- A project you have browsed in Lightcone Lab, with a MyST report that reads its
  numbers from the analysis.
- An RO-Crate, kept current by every `lc materialize`, for anyone who wants to
  check your work.
