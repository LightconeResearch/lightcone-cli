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
| `materialize(task, versions, ...)` | The unit: classify → reset → sandbox → recipe → check the payload → hash → manifest. Returns a `TaskResult`, always. |
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
- **The reset takes what the output's id names, never the directory** —
  outputs share a directory and Dask writes them concurrently, so a
  whole-directory delete would take a neighbour's bytes with it. The
  glob is `<local_id>.*` plus the sidecar: an id cannot contain a dot,
  so it cannot reach a sibling, and it *does* reach a payload left by a
  run that declared another `format`.
- **A payload that is not a regular file fails the task.** `data_version`
  hashes a directory perfectly happily, so `mkdir {output}` would
  otherwise commit a well-formed digest of something that is not the
  output — and exit 0 is not evidence that anything was written.
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
