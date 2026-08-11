Build the analysis specified in `astra.yaml` for universe `baseline`.

## Toolchain

This project is driven by two CLIs — use them rather than improvising:

- `astra` is the spec layer. `astra info` summarizes `astra.yaml`;
  `astra validate astra.yaml` checks it against the schema. If an `astra`
  skill or plugin is available in your environment, load it before reading
  or editing `astra.yaml` — it documents the full spec format.
- `lc` (lightcone-cli) is the execution layer, a thin shim over Snakemake:
    - `lc run <output_id> --universe baseline` materializes an output (and
      anything upstream of it) by running the recipe commands declared in
      `astra.yaml`. With no output ids it builds everything. It is
      idempotent: re-running only rebuilds what is stale or missing.
    - `lc status --universe baseline` reports each output as `ok`, `stale`,
      or `missing`; `lc status --json` is the machine-readable form.
    - Outputs land in `results/baseline/<output_id>/`, each with a
      `.lightcone-manifest.json` provenance manifest written by the engine.
      Files placed in `results/` by hand have no manifest and fail
      verification — never write there yourself.
    - When `lc run` fails, read the error and the Snakemake log it points
      to, fix the script or spec, and re-run.

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

You are in an activated Python virtual environment managed by uv. Common
scientific packages (numpy, scipy, matplotlib) are pre-installed. If you
need more, install them with `uv pip install <package>` — plain `pip` is
not available in this venv.

## Build loop

`astra.yaml` is the single source of truth: inputs, outputs, recipes, and
methodological decisions all live there — read it first. For each output
that needs materializing:

1. Read the recipe's `command` to see what script and arguments it expects.
2. Write the script at the path the command names, parameterizing every
   decision via argparse — never hardcode option values.
3. Run `lc run <output_id> --universe baseline` to materialize it through
   the engine.
4. Commit progress as you go.

Build iteratively from upstream outputs to downstream. `lc status
--universe baseline` shows you what's `ok`, `stale`, or `missing` — you're
done when every output shows `ok` and `astra validate astra.yaml` passes.

Skip plan approval and interactive confirmations — this is an automated
eval run.
