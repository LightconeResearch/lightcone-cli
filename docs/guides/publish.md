# Publish a report

After this page you can turn a finished analysis into a MyST report,
and the repository into a deposit-ready RO-Crate.

!!! abstract "Planned page"
    What this page will cover:

    - The report `lc init` scaffolds: `myst.yml` and `index.md`, which reference `astra.yaml` by path through `{astra}` roles and directives.
    - Pointing the report's references at your own decision and output ids, and writing the narrative around them.
    - Declaring a `license` under `[project]` in `pyproject.toml`, after which every `lc materialize` maintains `ro-crate-metadata.json` and commits it on its own.
    - Reading the `crate:` line of `lc status` to see whether the crate is up to date with the outputs.
    - The gate before sharing: `astra validate` is clean and `lc materialize --check` passes.
    - Depositing: `git archive` (or `datalad export-archive`) on the repository you already have.

    Draws on: [Outputs and provenance](../concepts/provenance.md#publication-is-a-license-away),
    [lc materialize](../reference/cli/materialize.md),
    [lc init](../reference/cli/init.md#what-it-creates),
    [Use lc without an agent, step 7](without-agent.md#7-publish), and the
    `lightcone` plugin's `references/reporting.md` and `references/publishing.md`.
