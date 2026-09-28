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

Cluster execution supplies an output receiver to `materialize`/`execute`, which
passes byte chunks from the sandbox back to the invocation. Standalone reruns
retain direct terminal output. The driver submits each cluster task with its
CPU and memory reservations; `execute` applies the task's walltime limit through
the sandbox boundary. Standalone reruns also apply that time limit, but do not
perform Dask resource admission.

## Key symbols

| Symbol | Role |
|---|---|
| `materialize(root, task, context, ...)` | The unit: classify → reset → sandbox → recipe → check the payload → hash → manifest. Returns ordinary failures as `TaskResult`; propagates execution safety exceptions. |
| `execute(root, task, input_versions, context)` | Run a recipe unconditionally with its time limit and cancellation checks, then record its payload and manifest. |
| `TaskResult` | `ok` / `current` / `behind` / `failed` / `blocked`, the output's `data_version`, reason, and diagnostic notes. `.usable` is what dependents check. |
| `main(argv)` | The rerun entry point: guards, converges the project environment from the commit's own lock, resolves its own HEAD and runtime, executes. |
| `lc_version()` | The engine version every manifest records. |

## What must stay true

- **Ordinary recipe failures are results.** Independent tasks continue so
  the driver can report all their failures. `ExecutionCancelled` and
  `ExecutionUncertain` instead propagate and abort the invocation. They must
  not enter the ordinary failed-output restore path: cleanup first establishes
  that writers have stopped, and uncertainty retains partial outputs.
- **Task completion includes subprocess teardown.** The boundary owns process
  and container cleanup, applies `task.resources.time_seconds`, and reports
  uncertain teardown as an exception. A time limit that stops the recipe
  becomes an ordinary failed result. CPU and memory reservations are standard
  Dask scheduling constraints, not OS limits imposed by this module.
- **`data_version` is computed here, before anything is staged** — the
  dependent's argument *is* this return value, so the digest must
  exist while the files are still unannexed. Deriving it from
  `git annex find` records `sha256([])` for everything, silently, with
  green tests — and couples the digest to the annex backend, which is
  deliberately not pinned.
- **The reset takes what the output's id names, never the directory** —
  outputs share a directory and Dask writes them concurrently, so a
  whole-directory delete would take a neighbour's bytes with it. The
  glob is `<output_id>.*` plus the sidecar: an id cannot contain a dot,
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
