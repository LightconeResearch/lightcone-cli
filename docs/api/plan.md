# lightcone.engine.plan

The spec, read as a graph of tasks. `astra.yaml` × `universes/*.yaml`
gives one task per `(universe, output)` pair that has a recipe; a task
carries everything executing it needs — the rendered command, where its
bytes go, what it reads, its decisions, its `definition_version` — and
nothing about *how* it will be executed.

Source: `src/lightcone/engine/plan.py`.

## Key symbols

| Symbol | Role |
|---|---|
| `build(root)` | Validate the spec with ASTRA's own validators, resolve every universe, return the `Graph`. |
| `Graph` | Tasks keyed on `(universe_id, output_id)`; `order()` for the read-only topological walk, `resolve(targets)` for what a user typed, `closure(keys)` to narrow a run. |
| `Task` | One output in one universe, frozen. |
| `declared_path(root, path)` | The one rule that names a path: project-relative inside the tree, absolute outside, never resolved. |

## What must stay true

- **What the spec *means* is ASTRA's to say.** `astra.resolve` settles
  decisions, resolves inputs, drops `when:`-excluded outputs, and
  renders the placeholder grammar. This module holds only what
  *execution* adds. A prior in-house interpretation diverged three
  ways (couldn't build ASTRA's own nested example, ignored `when:`,
  invented an input spelling `astra validate` rejects) — that history
  is why re-derivation is banned. Missing semantics → PR to
  astra-tools.
- **A spec ASTRA rejects never reaches a recipe.** `build` runs the
  schema, file, and universe validators before resolving anything —
  resolution answers what a *valid* spec means and does not re-check
  that it is one.
- **The layout is flat and path-addressed.**
  `<analysis root>/results/<universe>/<scope…>/<id>.<format>`, and the path in a
  rendered recipe *is* the path on disk — no staging, no relocation.
- **`declared_path` is lexical, never `resolve()`d.** A declared input
  under `data/` is an annex symlink; resolving it writes
  `.git/annex/objects/…` into the run record — the storage instead of
  the input. This shipped once.
- **Two universes cannot share an id** (the id names a directory;
  `build` refuses, naming both files), and an out-of-tree absolute
  input is **reported, not refused** — its bytes still hash and
  cascade, but the repository cannot bring it back, and saying so is
  the whole obligation.
- **A target that matches nothing is an error** listing what exists —
  quietly making nothing is the least useful thing a build tool can
  do.

## Tests

`tests/test_plan.py` — pure; tests what lc *adds* (directories, edges,
versions, the validation gate), never what a spec means — that
coverage lives in astra-tools' own suite, and re-asserting it here
would recreate the second implementation this module deleted. Every
fixture must be a spec `astra validate` accepts; the gate enforces it
for free.
