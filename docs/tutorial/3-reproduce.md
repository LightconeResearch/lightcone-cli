# 3. Make it reproducible

Run the analysis so that every output arrives with a record of how it was made.

!!! abstract "Planned chapter"
    Mostly new. The source tutorial had the agent run the scripts itself; here
    `lc` runs them. Nothing below has been run yet: the prompts are drafts, and
    every place real output belongs is marked **To capture**.

    1. **`lc init`** makes the directory a Lightcone project. It adopts the
       existing `astra.yaml` untouched and adds the rest.
    2. **Implement.** The agent adds packages with `uv add`, writes the scripts
       the recipes call, and probes them with `lc run`.
    3. **Commit, then `lc materialize`.** Each output lands at
       `results/<universe>/<id>.<format>` beside a manifest, and is committed with
       the code that made it.
    4. **`lc status`** shows both outputs `current`.
    5. **The result.** Confirm the materialized fit reproduces Ω_Λ = 0.722 ± 0.013
       (the source's agent-run number), and regenerate the figure from `results/`.

    Open questions for this chapter:

    - Beside an adopted spec, `lc init` writes no universe file, so the
      `baseline` universe has to come from scoping ([chapter 2](2-scope.md)).
      Confirm it does.
    - `lc init` scaffolds `index.md` against its own boilerplate spec, so its
      references name ids this analysis does not have. The skill repoints them
      at the end of scoping, which in this order has already happened.

## Make it a project

`lc init` converges the working directory into a Lightcone project: a git
repository with git-annex for the data, a locked `uv` environment, a
`results/` directory that `lc` writes into, and a MyST report. A directory that
already holds an `astra.yaml` is adopted: the spec is left untouched, and only
the missing pieces are added. See [`lc init`](../reference/cli/init.md).

```text
Make this directory a Lightcone project with lc init, and tell me what it
added.
```

!!! note "To capture"
    The prompt's real run, and the agent's summary of what `lc init` added.

## Implement the analysis

```text
Implement the analysis: write the scripts the recipes call, add what they
import to the project, and try them out before we make anything for real.
```

A recipe runs in the project's locked environment, in a sandbox. So the agent
adds packages with `uv add`, never `pip install`, and tries each script with
`lc run`, which runs a command under the same boundary a recipe gets: what
works under `lc run` works as a recipe.

!!! note "Where results go"
    `{output}` is not yours to choose. `lc` composes it as
    `results/<universe>/<output_id>.<format>`, so the whole of `results/`
    follows from the analysis file alone.

!!! note "To capture"
    The scripts the agent writes, the `uv add` it runs, and its `lc run` probes.

## Materialize

```text
Commit the data, the scripts and the spec, then materialize the outputs.
```

`lc materialize` runs each recipe in dependency order, `best_fit` before
`hubble_diagram`. Each output lands in `results/<universe>/` beside a
`.<output_id>.manifest.json` that records the recipe, the decisions, the input
hashes, the environment, and the commit, and each is committed as it lands. See
[outputs and provenance](../concepts/provenance.md).

!!! note "To capture"
    The real `lc materialize` output, and a look at one manifest.

## Check where it stands

```bash
lc status
```

One line per output: its state, and the commit it was made at. Both should be
`current`. See [`lc status`](../reference/cli/status.md).

!!! note "To capture"
    The real `lc status` output.

## The result

One number comes back: **Ω_Λ = 0.722 ± 0.013.**

![Two-panel Union2.1 Hubble diagram: distance modulus against redshift with the best-fit flat-ΛCDM curve, and residuals below.](../assets/hubble_two_panel.png)

## You now have

- A Lightcone project whose two outputs were each made by `lc materialize` and
  committed with a manifest of how.
- Ω_Λ = 0.722 ± 0.013, and a Hubble diagram to go with it.
