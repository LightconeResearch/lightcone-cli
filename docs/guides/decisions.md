# Add a decision and compare universes

After this page you can add a methodological decision to an existing
analysis, run it in more than one universe, and compare the results.

!!! abstract "Planned page"
    What this page will cover:

    - Declaring a decision and its options in `astra.yaml`, and handing the active option to a script as a CLI flag through `{decisions.<id>}` in the recipe.
    - Creating a universe file per combination of options, by hand or with `astra universe generate -n <name>`, and checking it with `astra universe check`.
    - Why every universe needs its own `id`, and what `lc` says when two files share one.
    - Which outputs a new decision makes `stale`, and why the rest stay `current`.
    - Materializing every universe with a bare `lc materialize`, or one with a target such as `robust/fit`.
    - Comparing: outputs side by side under `results/<universe>/`, and the `decisions` each manifest records.

    Draws on: [Use lc without an agent, step 6](without-agent.md#6-sweep-the-decision),
    [Decisions and universes](../concepts/universes.md),
    [lc materialize](../reference/cli/materialize.md), and the
    `astra universe` commands (`astra-tools/src/astra/cli.py`).
