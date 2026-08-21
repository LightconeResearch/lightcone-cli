# lc init

Converge a directory into a Lightcone project. Idempotent — safe to run
at any time, on an empty directory, a half-scaffolded one, an existing
project, or a fresh clone.

## Synopsis

```text
lc init [OPTIONS] [DIRECTORY]
```

`DIRECTORY` defaults to `.` (the current directory).

## Convergence semantics

Each run creates whatever is missing, repairs the pieces lightcone
manages, and never overwrites files you own:

- **Created if missing** — every item in the tree below. A directory
  that already holds an `astra.yaml` is *adopted*: the spec is left
  untouched and only the missing lightcone pieces are added. A
  directory inside an existing git repository adopts that repository
  rather than nesting a new one.
- **Repaired** — derived artifacts that have drifted: a `uv.lock` that
  no longer matches `pyproject.toml`, a `.venv` that no longer matches
  the lock, a managed `.gitignore` or `.gitattributes` entry that a
  newer `lc` added, an annexed repository still missing the
  `filter.annex.required` flag. Repairs only ever append or rebuild
  derived state;
  hand-written lines are never reordered or removed.
- **Blocked** — something convergence can see but must not fix by
  appending: a `.gitignore` rule that would silently swallow
  `results/`, a `.gitattributes` whose ordering would misroute storage.
  A blocked item names the file and line at fault, counts against
  convergence, and is yours to resolve.
- **Warned about** — advisory facts (e.g. uv falling back to file
  copies across filesystems). Warnings never affect the exit code.

`--check` computes the same report without writing anything and exits
`1` when the project is not converged. `--json` prints it
machine-readable:

```json
{
  "converged": true,
  "created": [],
  "repaired": [],
  "unchanged": ["astra.yaml", "pyproject.toml", "..."],
  "blocked": [],
  "warnings": []
}
```

Agents driving a project should run `lc init --check --json` at the
start of a session to make sure the directory is workable.

## What it creates

Inside `DIRECTORY` (creating it if needed):

```text
astra.yaml                    # boilerplate spec with one example output
universes/
  baseline.yaml               # the default universe
pyproject.toml                # the uv project — the environment's source of truth
.python-version               # the exact interpreter, pinned
uv.lock                       # derived: converged by correctness, not existence
.venv/                        # derived: built from the lock (local, never committed)
.gitignore                    # managed entries, converged line-wise
.git/                         # a git repository, with git-annex initialized
.gitattributes                # the storage policy: what the annex carries
.datalad/config               # dataset identity (a DataLad dataset from birth)
data/  + README.md            # declared input data lives here
results/  + README.md         # outputs land here — lc's to write
myst.yml                      # MyST report configuration
index.md                      # template report referencing astra.yaml
```

Two things it deliberately does *not* create: a `src/` directory
(where analysis code lives is your layout, and git doesn't track empty
directories), and any dependency in `pyproject.toml` — the lock
carries only what *your* analysis imports, added with `uv add`.

Inside `.git`, convergence sets one configuration key — reported as the
`annex-filter` item:

- `filter.annex.required=true`, always. Without it, a `git add` whose
  shell cannot find git-annex prints an error, **exits 0, and stages
  the raw bytes into git history** — a 2 GB dataset in git proper, on
  every clone, forever. With it, the same situation is a hard, loud
  failure and nothing is staged.

That is the only thing `lc init` adds to what `git annex init` wrote.
How git finds git-annex is still ordinary `PATH` resolution, which is
why `lc` should be installed with `uv tool install lightcone-cli` — it
puts `git-annex` on your `PATH` alongside `lc`. If your `git add` ever
refuses, see
[`fatal: … clean filter 'annex' failed`](../user/troubleshooting.md#fatal-clean-filter-annex-failed)
in the troubleshooting guide.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--check` | off | Report drift without writing; exit 1 if not converged. |
| `--json` | off | Emit the convergence report as JSON on stdout. |

There is deliberately nothing else — no `--no-git`, no template
selection. The project layout is the contract the other verbs rely on.

## Examples

```bash
lc init                    # converge cwd
lc init my-analysis        # scaffold/converge ./my-analysis
lc init --check --json     # is this directory workable? (for scripts/agents)
lc init                    # in a fresh clone: rebuild .venv + the annex
```

## Next steps

```bash
cd my-analysis
# Describe your analysis in astra.yaml — inputs, outputs, recipes,
# decisions — and write the scripts the recipes name.
uv add numpy               # declare what the scripts import
git add -A && git commit -m "First analysis"
lc materialize             # make the outputs
lc status                  # see where everything stands
```
