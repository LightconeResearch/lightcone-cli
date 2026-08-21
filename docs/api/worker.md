# lightcone.engine.worker

Making one output — the unit of work, and the only thing that runs a
recipe. Also an entry point:

```text
python -m lightcone.engine.worker <universe>/<output_id>
```

which is what the `[DATALAD RUNCMD]` record in every materialization
commit names, behind an engine-pinning `uv run --no-project --with …`.
It is a module rather than an `lc` verb on purpose: it makes the
output unconditionally, commits nothing, and leaves the tree dirty by
design — precisely the state `lc materialize` refuses to start from —
so advertising it would hand people a footgun.

Source: `src/lightcone/engine/worker.py`.

## Key symbols

| Symbol | Role |
|---|---|
| `materialize(task, versions, ...)` | The unit: classify → reset the directory → sandbox → recipe → hash → manifest. Returns a `TaskResult`, always. |
| `TaskResult` | `ok` / `current` / `behind` / `failed` / `blocked`, the output's `data_version`, and the attestation. `.usable` is what dependents check. |
| `main(argv)` | The rerun entry point: guards, converges the project environment from the commit's own lock, resolves its own HEAD and runtime, executes. |
| `lc_version()` | The engine version every manifest records. |

## What must stay true

- **The worker never raises** — enforced at the unit boundary, so the
  contract holds for failure modes nobody enumerated. Raising would
  make Dask abort every task in flight; reporting all independent
  failures in one run is most of what owning the loop buys.
- **`data_version` is computed here, before anything is staged** — the
  dependent's argument *is* this return value, so the digest must
  exist while the files are still unannexed. Deriving it from
  `git annex find` records `sha256([])` for everything, silently, with
  green tests — and couples the digest to the annex backend, which is
  deliberately not pinned.
- **The reset takes the whole directory** — a crashed previous run can
  have left anything there, and there is no "expected file list" to
  delete by. The `output_dir` guard bounds the blast radius, not a
  narrower delete.
- **No git in here.** The driver commits; a worker that asked git
  would race the index lock and could read a HEAD this same run moved.
- **`main`'s "no output `<x>`" message covers the task lookup only.**
  It once wrapped the whole body, and a `KeyError` from anywhere
  inside astra surfaced as "bad target" — a rerun misdiagnosing itself
  at the one place nobody is watching.
- **Keep it cheap to import — no click, no rich.** It is on the path
  of every task and every rerun; two tests pin the imports and the
  absence from `--help`. (Nothing pins the absence of a
  `[project.scripts]` entry — treat that as a review item.)

## Tests

`tests/test_worker.py` — real recipes through the real boundary
against a real repository (the `analysis` fixture): whether gates
hold and bytes land are not questions a stub can answer.
