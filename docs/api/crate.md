# lightcone.engine.crate

The publication view: the repository described as a Workflow Run
RO-Crate. The project *is* the crate — `ro-crate-metadata.json` sits at
the root, describes what the repository already holds, and a deposit is
`git archive`, not an export step. lc's manifests stay the canonical
record; the crate is the same facts in schema.org vocabulary for
archives and viewers that will never run `lc`.

Source: `src/lightcone/engine/crate.py` (converged by
`materialize._converge_crate`).

## Key symbols

| Symbol | Role |
|---|---|
| `render(root, graph, *, license, dsid, writer)` | The document, as bytes. A pure function of repository state — git comes in as the `writer` callable, the dataset id as a value. |
| `license_of(root)` | `[project].license` from `pyproject.toml`; empty means no crate is maintained. Presence is publication intent. |
| `CRATE_FILENAME` | `ro-crate-metadata.json`. |

## What must stay true

- **The clock never enters the render.** `datePublished` is the newest
  manifest `finished_at` (the spec file's last-commit date for a
  never-materialized project) and must override rocrate's
  construction-time default. Entities build in sorted order,
  serialization is `sort_keys` — render-twice-identical is the one
  byte-level claim, and it is what makes convergence sound.
- **Maintenance is derived, never configured.** RO-Crate requires a
  license; materialize must not refuse to run science over a missing
  key, and inventing one asserts terms over someone's data. Absent ⇒
  one report line; removed later ⇒ the file is left, and the line says
  it is no longer maintained.
- **Run identity comes free from `git_sha`** — the driver reads HEAD
  once per run, so grouping manifests by it *is* grouping by run: one
  `OrganizeAction` per materialize, a `ControlAction` per execution, a
  `HowToStep` per output id (deduped across universes — a step is spec
  structure, an action is one execution).
- **The `Person` is the author of the output's *saving* commit** (via
  `writer`), never the manifest's `git_sha` — that is the commit the
  run *started* at and can be someone else's.
- **An output is a `File`, not a `Dataset` of parts.** It is one file,
  so there is one annex key to look up and one `sha256` to publish —
  the same number its manifest records as `data_version`, and the one
  `sha256sum` prints.
- **Every analysis root's spec and universes are in the crate**, not
  just the root's: a sub-analysis declared with `path:` keeps its own,
  and publishing its outputs without them would describe half an
  analysis.
- **The manifest is not transliterated.** `env_version`,
  `definition_version` and `hermeticity` get no invented schema.org
  spelling — the manifest itself is in the crate as a `File`,
  `subjectOf` its output. Real vocabulary comes from the workflow-run
  `@context`, without which `containerImage` and `sha256` are
  undefined terms JSON-LD silently drops — the pre-rebuild exporter's
  failure mode.
- **The rerun entry point does not regenerate the crate** — it is one
  task's executor, so the crate lags until the next materialize.
  Recorded residue, not a bug.

## Tests

`tests/test_crate.py` — pure: fixture manifests, a hand-built graph, a
stub writer, no git anywhere; structure and ordering assertions plus
the single render-twice byte check. `tests/test_crate_smoke.py` — the
official `rocrate-validator` against Provenance Run Crate 0.5:
REQUIRED clean, RECOMMENDED pinned to the recorded `_FLOOR` set (a new
failure is a regression, a disappearing one is the floor to shrink),
required in CI via `LC_CRATE_TESTS_REQUIRED=1`.
