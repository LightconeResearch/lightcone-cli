# lightcone.engine.dataset

The git + git-annex seam: how a project stores what it produced.
Storage follows the DataLad model — git carries the pointers and the
history, git-annex carries the bytes — reached through ordinary `git`
commands. Every command goes through `project._run`, so there is one
monkeypatch point and every invocation is inspectable.

Source: `src/lightcone/engine/dataset.py` (+
`templates/files/gitattributes.tmpl` for the routing policy).

## Key symbols

| Symbol | Role |
|---|---|
| `save(root, paths, message)` | Stage scoped, commit — with `-c annex.thin=true` and `-c annex.dotfiles=true`, per-add and never written to config. |
| `restore(root, paths)` | `git clean` always; `git checkout HEAD --` only when HEAD has the path. Never `-- .`. |
| `status(root)` | The dirty question, scoped to the project (`-- .`, prefix-stripped) so a project inside a larger repository works. |
| `head(root)` | The commit a run started at — read once per run, by the driver. |
| `last_writer(root, dir)` | Who last touched an output's directory — the foreign-write question. Answers "cannot say" as empty, never an error. |
| `require_committer(root)` | Refuses a repository with no git identity, before any recipe spends time. Asked as `git var`, the question a commit itself asks. |
| `dataset_id(root)` | The DataLad dataset UUID, read via `git config -f`. |
| `set_annex_filter_required(root)` | Set `filter.annex.required=true`, so a `git add` that cannot reach git-annex fails loudly instead of staging raw bytes. |
| `annex_filter_required(root)` | Whether that flag is already set — `lc init --check`'s question. |

## What must stay true

- **Nobody is ever asked to run a git-annex command.** `filter=annex`
  plus the `.gitattributes` policy make an ordinary `git add` do the
  right thing; `annex.largefiles=nothing` comes first and outputs and
  data opt out — last match wins, and
  `test_analysis_code_stays_in_git_and_stays_writable` pins it against
  a real annex.
- **Manifests stay in git**, exempted back out of the annex, so a
  bytes-free clone can classify a whole project.
- **An unfetched file exists, in two shapes** — an unlocked pointer
  file (readable, hashes to the wrong thing) and a locked dangling
  symlink (drops out of naive walks silently). `assets.data_version`
  refuses both with `ContentNotFetchedError`; detection handles both
  regardless of which shape lc writes, because `annex.thin` and
  `git annex lock` are the researcher's to set.
- **Thin is per-add and only where lc writes.** Thin's hazard is an
  in-place write rewriting the annex object under its own key; lc
  always resets output directories rather than writing in place, but
  `data/` is the researcher's, and their tools (`h5py`, astropy
  `mode='update'`) do open files for update — so the flag never
  reaches repository config.
- **`restore` is asymmetric on purpose:** a first materialization has
  no HEAD version to go back to, and a failed task must not discard
  edits made elsewhere while the graph ran.
- **Committing an archive or dot-named file needs `annex.dotfiles`** —
  git-annex routes dotfiles to git whatever `largefiles` says, and
  without the flag an image archive lands as a git blob, silently.
- **`filter.annex.required=true` is the storage policy's safety net.**
  Without it, a `git add` whose shell cannot resolve git-annex prints
  an error, exits 0, and stages the raw bytes into git history —
  measured, and pinned by
  `test_stock_plumbing_without_required_stages_raw_bytes_silently`.
  It is the *only* thing convergence adds to what `git annex init`
  wrote: no filter driver is rewritten, and no hook is touched, so how
  git dispatches git-annex stays git-annex's own business and stays
  resolved from `PATH`.

## Tests

`tests/test_dataset.py`, deliberately against **real tools**
(`real_tools` fixture): whether bytes land in the annex or as a blob
in git is not a question a stub can answer, and every bug this seam
has had was invisible to one.
