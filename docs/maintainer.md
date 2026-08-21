# Developer corner

`lightcone-cli` is a small engine with strong opinions: one way to
identify an output, one way to store it, one boundary to execute it
behind. This guide covers everything below the user surface — how the
engine is put together, what each module owns, and how to get a
working dev loop.

If you're looking for the user-facing docs, the
[user guide](user/index.md) is the other half of this site.

## What this covers

- [Architecture](architecture.md) — the CLI/engine/ASTRA split, the
  run pipeline, identity, storage, the exec boundary, and the
  invariants that hold them together.
- [CLI Reference](cli/index.md) — every `lc` command: flags, JSON
  report shapes, exit codes.
- [Engine Internals](api/index.md) — the `lightcone.engine.*`
  modules: what each owns, its key symbols, and what must stay true
  of it.
- [Contributing](contributing/setup.md) — clone, install, run the
  test suite; [how the suite is shaped](contributing/testing.md); and
  [where a change belongs](contributing/extending.md).

## Get started in three commands

!!! tip "Dev loop"

    ```bash
    git clone https://github.com/LightconeResearch/lightcone-cli.git
    cd lightcone-cli
    uv sync --group dev && uv run pytest
    ```

Test, lint (`uv run ruff check src/ tests/`) and type-check
(`uv run mypy src/`) are the whole loop — there is deliberately no
task runner in between.

## The house rules

A few conventions run through every module; changes are reviewed
against them:

- **No dead code, no foreshadowing.** Nothing lands before the layer
  that calls it, and no message names a verb or flag that doesn't
  exist yet. `lc --help` advertises only what works.
- **No escape hatches around guarantees.** A feature that enforces
  something ships without a flag to turn the enforcement off.
- **Literal behavior over invented convenience.** The current
  directory is the project; erroring beats walking up or guessing.
  Nothing prompts — a verb is run by an agent more often than a
  person, and a prompt is a hang.
- **One implementation per rule.** Classification, path naming, the
  run-record subject, tool resolution — each has exactly one spelling,
  and a second copy is where the two start to disagree.
- **Honest reporting.** What was enforced, what was skipped, and what
  a clone can't see are all recorded or said — never assumed.
