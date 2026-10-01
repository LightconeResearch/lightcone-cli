# Decisions and universes

A decision is a methodological choice that could change a result; a universe is one complete set of choices, and each universe gets its own results.

!!! abstract "Planned page"
    What this page will cover:

    - What deserves to be a decision, and its parts: `label`, `rationale`, `default` and `options`
    - Universe files: `universes/<id>.yaml` picks one option per decision, and its results land in `results/<universe>/`
    - How a choice reaches the code: as `{decisions.<id>}` in the recipe, so scripts take it as an argument and nothing is hard-coded
    - Options that cannot go together (`requires`, `incompatible_with`), and rejected options kept on the record with their reason
    - Comparing universes: adding one runs only its outputs, and a target like `robust/fit` narrows a run to one

    Draws on: [Use lc without an agent](../guides/without-agent.md#6-sweep-the-decision) (step 6), [Add a decision and compare universes](../guides/decisions.md), [lc materialize](../reference/cli/materialize.md), the specification's Decisions, Options, Constraints and Universes sections at astra-spec.org
