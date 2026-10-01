# Your analysis file

`astra.yaml` is the one file that describes your analysis: what goes in, what comes out, which choices shape the results, and how each output is made.

!!! abstract "Planned page"
    What this page will cover:

    - The sections of the file: `description`, `inputs`, `outputs`, `decisions`, `prior_insights`, `findings`, and nested `analyses`
    - Outputs and recipes: `format`, `recipe.command`, and the `{inputs.<id>}`, `{decisions.<id>}` and `{output}` placeholders
    - Why every dependency is declared: it is how `lc` orders the build and knows what to remake
    - Who writes it: the agent drafts it as the scoping conversation settles, you review it, and it is validated on every save
    - Splitting a large analysis into sub-analyses of the same shape
    - What the file does not do: it describes the analysis; `lc` runs it

    Draws on: [Use lc without an agent](../guides/without-agent.md#3-write-the-spec) (step 3), the specification and getting-started pages at astra-spec.org

`astra.yaml` follows ASTRA, an open standard for describing analyses. Full format reference at [astra-spec.org](https://astra-spec.org/). How Lightcone builds on it: [Built on ASTRA](../developers/astra.md).
