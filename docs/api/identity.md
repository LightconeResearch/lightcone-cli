# lightcone.engine.identity

What a materialized output is identified by: two hashes that answer
different questions, and the lock scan that decides whether an
environment can be audited at all.

Source: `src/lightcone/engine/identity.py`.

## Key symbols

| Symbol | Role |
|---|---|
| `definition_version(recipe, decisions)` | What the spec says an output *is* — the rebuild trigger. |
| `env_version(root)` | What it ran under: lock bytes ‖ interpreter pin ‖ install settings ‖ image document. The `behind` trigger. |
| `scan_lock(root)` | Refusals, reports and advisories about what the lock pins. |

## What must stay true

- **`env_version` is not part of `definition_version`.** That is the
  whole shape of the invalidation model: an environment edit stales
  nothing, it makes outputs *behind*. (The original design nested
  them; staling every output in every project on an engine upgrade was
  the bug, not the cost.)
- **Both hashes are length-framed** — label, length, bytes per field —
  so a boundary shift between adjacent fields cannot yield the same
  digest from different inputs. Mutation-checked in the suite.
- **The lock is hashed as raw bytes, never parsed.** A comment reflow
  moves `env_version`, deliberately: over-invalidation costs a report
  line, while a parse of our own can silently disagree with uv.
- **The install-settings list is closed** (`_INSTALL_SETTINGS`), every
  key hashed whether or not the project sets it — a setting outside
  the list must not move the hash, one merely *matching* today's
  default must. Settings are read where uv reads them (`uv.toml`
  **replaces** `[tool.uv]`, measured); only values are hashed, never
  which file supplied them. User-level uv config is deliberately out
  of reach — machine state, not project state — and the residue is
  tracked as issue #176.
- **The git commit is recorded, never hashed, and never a signal** —
  one sha covers the whole tree, so hashing it stales everything on a
  README edit. The honest consequence: editing `src/fit.py` remakes
  nothing unless the file is declared as an ASTRA input. Do not add a
  heuristic that scans recipes for repo paths.
- **The lock scan refuses only what cannot be audited** — path,
  directory, and editable dependencies (two syncs of one lock can
  install different code). A registry package with no wheel is a
  report; a non-default group is advisory; the project's own package
  is exempt. Names compare in PEP 503 form, or a project named
  `my_project` fails to recognise itself.

## Tests

`tests/test_identity.py` — pure, and written as sensitivity tests in
both directions: what must move each hash, and what must not.
