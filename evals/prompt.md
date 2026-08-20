Build the analysis specified in `astra.yaml` for universe `baseline`.

## Toolchain

This project is driven by two CLIs — use them rather than improvising:

- `astra` is the spec layer. `astra info` summarizes `astra.yaml`;
  `astra validate astra.yaml` checks it against the schema. If an `astra`
  skill or plugin is available in your environment, load it before reading
  or editing `astra.yaml` — it documents the full spec format.
- `lc` (lightcone-cli) is the execution layer:
    - `lc materialize` makes every output the spec declares, running each
      recipe in dependency order and committing each result to git as it
      lands, together with a provenance manifest. It refuses to start on
      a dirty tree: commit your own edits first, with plain `git add` and
      `git commit` — the project's git-annex filter handles large files
      transparently, so never run a git-annex command yourself.
    - `lc materialize <output_id>` (or `<universe>/<output_id>`) narrows
      a run to one output and whatever it depends on. Re-running is
      idempotent: only what is stale gets remade — an output the spec now
      defines differently, or one whose declared inputs changed.
    - `lc status` reports each output as `current`, `stale`, or `behind`,
      with the commit it was made at; `lc status --json` is the
      machine-readable form. It always exits 0. The pass/fail gate is
      `lc materialize --check`, which exits 1 while anything still needs
      making.
    - `lc run <command>` runs an ad-hoc command in the project
      environment under the same isolation a recipe gets — useful for
      probing why a recipe would fail.
    - Outputs land in `results/baseline/<output_id>/`, each with a
      `.lightcone-manifest.json` manifest written and committed by the
      engine. Never write into `results/` yourself: a hand-placed file
      has no run record, and the engine detects the foreign write and
      remakes the output.
    - When a recipe fails, `lc materialize` reports which output failed
      and why; fix the script or the spec, commit, and re-run.

## Recipe template grammar

A recipe's `command` is a template. The engine substitutes these
placeholders before invoking it:

- `{output}` — the directory the output is materialized into
  (`results/<universe>/<output_id>/`). The engine creates it; your script
  must write its artifact file(s) inside it.
- `{inputs.<id>}` — the named input's resolved path: an analysis-level
  `Input`'s `source` (e.g. a file under `data/`), or, for an upstream
  output, that output's results directory (your script reads the file(s)
  inside it).
- `{inputs}` — space-separated paths of all declared inputs, in
  declaration order.
- `{decisions.<id>}` — the active option ID for the named decision in the
  current universe (e.g. `nelder_mead`), which your script should accept
  as an argparse choice.
- `{{` and `}}` emit literal braces. Format specs (`{x:>8}`) are rejected.

Provenance is declared on the Output, not inside the recipe: every
`{inputs.<id>}` / `{decisions.<id>}` the command references must be listed
in that output's `inputs:` / `decisions:` lists, or validation fails.
Dependencies between outputs come from these `inputs:` declarations — that
is how the engine orders the build.

## Environment

Recipes run in the project's own locked environment (`pyproject.toml` +
`uv.lock` + `.venv`), sandboxed: the project tree is read-only apart from
each recipe's own output directory under `results/`, and only declared
tools are executable.

- Every package a recipe script imports must be added to the project
  with `uv add <package>`, run in the project root, before
  materializing. numpy, scipy, and matplotlib are already added. Plain
  `pip` or `uv pip` installs reach nothing a recipe sees.
- A sandbox denial names the path or tool that was denied and the
  remedy — follow the remedy rather than working around the sandbox.

## Build loop

`astra.yaml` is the single source of truth: inputs, outputs, recipes, and
methodological decisions all live there — read it first. The seed spec is
deliberately incomplete: recipe commands do not yet pass their inputs,
decisions, or output directory, and outputs may be missing entries in
their `inputs:` / `decisions:` contracts. Completing the spec is part of
the task. For each output:

1. Complete the recipe `command` so it references `{output}` and the
   `{inputs.<id>}` / `{decisions.<id>}` the computation needs, and
   declare everything it references in that output's `inputs:` /
   `decisions:` lists.
2. Write the script at the path the command names, parameterizing every
   decision via argparse — never hardcode option values.
3. Commit your edits, then run `lc materialize` (or
   `lc materialize <output_id>`) to build through the engine.

Build iteratively from upstream outputs to downstream. `lc status` shows
where every output stands — you're done when `lc materialize --check`
passes and `astra validate astra.yaml` passes.

Skip plan approval and interactive confirmations — this is an automated
eval run.
