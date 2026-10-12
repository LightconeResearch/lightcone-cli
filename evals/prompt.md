Build the analysis specified in `astra.yaml` for universe `baseline`.

## Toolchain

This project is driven by two CLIs — use them rather than improvising:

- `astra` is the spec layer. `astra info` summarizes `astra.yaml`;
  `astra validate astra.yaml` checks it against the schema. If an `astra`
  skill or plugin is available in your environment, load it before reading
  or editing `astra.yaml` — it documents the full spec format.
- `lc` (lightcone-cli) is the execution layer:
    - `lc materialize <cluster_id>` makes every output the spec declares, running each
      recipe in dependency order and committing each result to git as it
      lands, together with a provenance manifest. It refuses to start on
      a dirty tree: commit your own edits first, with plain `git add` and
      `git commit` — the project's git-annex filter handles large files
      transparently, so never run a git-annex command yourself.
    - `lc materialize <cluster_id> <output_id>` (or `<universe>/<output_id>`) narrows
      a run to one output and whatever it depends on. Re-running is
      idempotent: only what is stale gets remade — an output the spec now
      defines differently, or one whose declared inputs changed.
    - `lc status` reports each output as `current`, `stale`, or `behind`,
      with the commit it was made at; `lc status --json` is the
      machine-readable form. It always exits 0. The pass/fail gate is
      `lc materialize --check`, which exits 1 while anything still needs
      making. `--check` needs no cluster.
    - `lc run <cluster_id> -- <command>` runs an ad-hoc command in the project
      environment under the same isolation a recipe gets — useful for
      probing why a recipe would fail. Argv style, like `docker run` or
      `uv run`: `lc run <cluster_id> -- python scripts/fit.py --output /tmp/x`, never a
      single quoted shell string; for shell syntax use
      `lc run <cluster_id> -- bash -c '...'`.
    - Outputs land in `results/baseline/<output_id>.<format>`, each with a
      `.<output_id>.manifest.json` manifest beside it, written and
      committed by the engine. Never write into `results/` yourself: a
      hand-placed file has no run record, and the engine detects the
      foreign write and remakes the output.
    - When a recipe fails, `lc materialize` reports which output failed
      and why; fix the script or the spec, commit, and re-run.

Allocate compute before running commands or recipes: `lc compute launch --wait`.
With no CPU/memory flags, this starts a cluster named `local` using all detected
usable CPUs and RAM on this machine and waits until it is ready. No configuration
is needed. GPUs still require explicit offers. Add `--name analysis` to choose
another name. Replace `<cluster_id>` in these commands with the returned name
(normally `local`) or a full immutable ID from launch or status JSON (`--json`).
Reuse the cluster with `run` and `materialize`; neither creates compute automatically.

Only one local cluster can run per user on this machine, even with different names
or catalogs. If one already exists, use the catalog identified in the refusal to
inspect `lc compute status` and reuse it rather than launching another.
`lc compute status <cluster_id> --wait` waits for an
existing cluster. Launch's `--wait` defaults to a 300-second readiness timeout;
`--timeout SECONDS` overrides it. A waiting launch that fails reports the accepted
cluster ID and leaves the allocation unchanged: inspect it before retrying.
A local cluster ends after 30 minutes without task activity; running work keeps
it alive, and `--time` adds a hard lifetime that ends it even mid-run. After it
ends, launch again; the name `local` can be reused, but the immutable ID changes.

Compute configuration is `~/.lightcone/compute.yaml`, or the file selected by
`LC_COMPUTE_CONFIG`. A catalog that lists its own local offers (`provider: local`)
uses those instead of the built-in one, which is how to set a smaller CPU/RAM
budget. Remote offers otherwise coexist with the default local offer. If
`allow_local: false` is configured, respect that policy: local launch and execution
are disabled. Inspect `lc compute resources` and supply both
`--cpus` and `--memory` to select a configured remote allocation; `--wait` works
there too. The no-resource shortcut never selects remote compute automatically.
Recognized NERSC login nodes refuse local compute automatically, even without a
catalog. Use Slurm or an interactive compute-node session; local compute remains
eligible on those compute nodes. Do not try to override the login-node guard.

## Recipe template grammar

A recipe's `command` is a template. The engine substitutes these
placeholders before invoking it:

- `{output}` — the file the output is materialized to,
  `results/<universe>/<output_id>.<format>`, where `format` is the one the
  output declares. Your script must write exactly that path, and nothing
  else: an output is one file. The engine creates the directory; a recipe
  that writes a directory there, or writes some other name, fails.
- `{inputs.<id>}` — the named input's resolved path: an analysis-level
  `Input`'s `source` (e.g. a file under `data/`), or, for an upstream
  output, that output's own file — so your script opens it directly.
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
the directory each recipe's output lands in, and only declared tools are
executable.

- The project is managed by uv and starts with **no dependencies**.
  Every package a recipe script imports must be declared before
  materializing: run `uv add <package> [<package> ...]` in the project
  root (e.g. `uv add numpy scipy`). That updates `pyproject.toml`,
  re-locks `uv.lock`, and syncs `.venv` in one step — commit all of it
  along with your scripts, like any other edit.
- To remove a package use `uv remove <package>`; to pin a version,
  `uv add 'numpy>=2'`. Do **not** use plain `pip` or `uv pip install` —
  an install that bypasses the lock reaches nothing a recipe sees.
- A sandbox denial names the path or tool that was denied and the
  remedy — follow the remedy rather than working around the sandbox.

## Build loop

`astra.yaml` is the single source of truth: inputs, outputs, recipes, and
methodological decisions all live there — read it first. The seed spec is
deliberately incomplete: outputs declare no `format:`, recipe commands do
not yet pass their inputs, decisions, or output path, and outputs may be
missing entries in their `inputs:` / `decisions:` contracts. Completing the spec is part of
the task. For each output:

1. Declare the output's `format:` — the file extension its artifact is
   written with, without the leading dot (`png`, `csv`, `json`, …). lc
   names the file from it and refuses a spec that omits it.
2. Complete the recipe `command` so it references `{output}` and the
   `{inputs.<id>}` / `{decisions.<id>}` the computation needs, and
   declare everything it references in that output's `inputs:` /
   `decisions:` lists.
3. Write the script at the path the command names, parameterizing every
   decision via argparse — never hardcode option values.
4. Commit your edits, then run `lc materialize <cluster_id>` (or
   `lc materialize <cluster_id> <output_id>`) to build through the engine.

Build iteratively from upstream outputs to downstream. `lc status` shows
where every output stands.

## Publication

Once every output is materialized, prepare the repository for
publication:

1. Declare a license in `pyproject.toml`, as an SPDX expression under
   `[project]` — e.g. `license = "CC-BY-4.0"`. Declaring one is what
   turns publication on: from then on `lc materialize` also maintains
   `ro-crate-metadata.json` at the project root, an RO-Crate view of
   the project and its provenance.
2. Commit the edit, then run `lc materialize <cluster_id>` once more — nothing is
   remade, but the crate document is generated and committed.

You're done when `astra validate astra.yaml` and
`lc materialize --check` pass and `ro-crate-metadata.json` exists.
Release your allocation with `lc compute down <cluster_id>` when finished.

Skip plan approval and interactive confirmations — this is an automated
eval run.
