# Scope an analysis with your agent

After this page you can take a research question to a validated
`astra.yaml` through your agent's scoping interview, before any
analysis code is written.

!!! abstract "Planned page"
    What this page will cover:

    - Starting from `lc init` and asking the agent for a new analysis; while scoping, it edits only `astra.yaml`, `universes/` and `AGENTS.md`, and writes no implementation code.
    - The interview's phases, each announced with a banner: research question, analysis structure, an optional literature deep dive, and finalize.
    - Why one analysis stays flat, and why every output is a single file with a `format:`.
    - The optional literature pass: you approve the papers, and every quote is verified before scoping ends.
    - What finalize leaves behind: a validated spec, a `baseline` universe, the report's references repointed at real ids, and project notes in `AGENTS.md`.
    - Resuming in a later session: the agent reads `lc status` and `AGENTS.md` rather than interviewing you again.

    Draws on: the `lightcone` plugin's scoping reference
    (`agent-skills/plugins/lightcone/skills/lightcone/references/scoping.md`),
    [Agent plugin](../reference/plugin.md), and tutorial step
    [2. Scope the analysis](../tutorial/2-scope.md).
