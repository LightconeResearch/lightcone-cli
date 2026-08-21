# lightcone.engine.materialize

Making a whole analysis: what runs, in what order, and what gets
committed. The driver refuses dirt, hands the graph to Dask, and owns
git alone — plus the read-only halves (`check`, `status`) that share
its classification walk.

Source: `src/lightcone/engine/materialize.py`.

## Key symbols

| Symbol | Role |
|---|---|
| `materialize(root, targets, *, refresh)` | The run: guards → converge → plan → fetch → schedule → save/restore loop → crate converge. |
| `check(root, targets, *, refresh)` | The same classification without executing, committing, or fetching. Exempt from the dirty refusal. |
| `status(root)` | The report: every output's state and provenance commit, plus the mode/image/sandbox header facts. |
| `MaterializeReport` / `StatusReport` | The JSON surfaces; `ok` and `up_to_date` first. |
| `cluster_for_run()` | The venue ladder, and the two-method scheduler seam (`submit`, `completed`). `completed` releases each future as its result lands — a key still held at teardown makes the scheduler fight worker retirement, and every clean SLURM run ends in "srun: forcing job termination". |
| `run_record(...)` / `datalad_run_subject(...)` | The commit message `datalad rerun` replays, and the one spelling of its subject line — shared with the foreign-write comparator, because two strings here would drift. |
| `_engine_requirement()` | How a record pins its engine: by version for a release, by source commit (hatch-vcs) for a dev build. |

## The run's order, and why

1. **Login guard first** — the allocation is the remedy with queue
   latency, so the user submits it before fixing anything else.
2. **Dirty refusal before the environment converge** — in
   containerized mode the converge can commit an image archive, and
   `dataset.save` commits the whole index; on a dirty tree the user's
   staged edits would be swept in.
3. **Converge before the graph runs** — `uv run --locked --no-sync`
   in workers would otherwise execute recipes against a drifted
   `.venv` while manifests record the new lock (measured; the state
   is made impossible rather than detected).
4. **Graph (validation, lock scan) before the image** — a refusal
   over a typo must not cost a minutes-long build.
5. **HEAD, runtime, and foreign-write facts read once, handed down**
   — the driver commits as results arrive, so any per-task read could
   answer differently mid-run. Nondeterminism in a provenance field is
   worse than either answer.
6. **Save on `ok`, restore otherwise, `try/finally` around the loop**
   — an interrupt restores whatever is still outstanding; the tree
   ends as clean as it started.

## What must stay true

- **The driver owns git, alone** — one thread, as results arrive.
  A dependent may start while its upstream is being annexed; that is
  measured-safe (the clean filter renames over the path, which never
  stops existing) and must not be "fixed" by moving the save into the
  task.
- **`up_to_date` is `ok and not made and not planned`** — a run where
  every recipe failed must not report "nothing to do", and `behind`
  never counts against it.
- **A read-only verb never tracebacks.** Anything `check`/`status`
  cannot read classifies as "will be remade" and the real error
  belongs to the recipe that follows.
- **The run record is genuinely re-runnable**: engine pinned by
  requirement, project environment rebuilt by the worker from the
  rerun commit's own lock, format tested *through datalad's parser*
  and a real `datalad rerun` — a golden test over our own JSON stays
  green through a silent break.
- **The crate converge is contained**: it runs after the loop, on the
  full graph, and a failure there is a warning — the outputs are
  already committed, and the crate is the publication view, not the
  run.

## Tests

`tests/test_materialize.py` — real repositories, real recipes, a real
`LocalCluster` through the seam exactly once, real `datalad rerun` for
the record's whole claim. `cluster_for_run` is the one monkeypatch
point for venue-free tests.
