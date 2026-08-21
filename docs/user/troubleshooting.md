# Troubleshooting

Common situations and how to unstick them, roughly ordered by how often
they come up. `lc`'s refusals try to carry their own remedy — this page
adds the context around them.

## "uncommitted changes in …"

```
Error: uncommitted changes in /home/you/my-analysis — every
materialization is committed with the code that produced it, so a run
cannot start from a tree that does not say what that code is.

  commit these:   git add -A . && git commit -m "…"
      M src/fit.py
```

Not an error in your project — just the order of operations: commit,
then materialize. The refusal sorts the paths it found: work you own
gets the `commit these` line, while leftover files under `results/`
(from an interrupted run of an older `lc`, or a hand write) are listed
as wreckage to discard instead — `results/` is `lc`'s to write, and
committing hand-placed files there defeats the provenance the tool
exists for.

## "… is not a Lightcone project"

You're outside a project. The current directory *is* the project — `lc`
never walks up to find one, by design — so:

```bash
cd path/to/your/project
```

or, starting fresh, `lc init my-analysis && cd my-analysis`. If you're
in a fresh clone, run `lc init` once — it rebuilds the `.venv` and the
annex, the two pieces of local state git doesn't carry.

## "lc: command not found" or `lc` prints a directory listing

Two possibilities:

1. The tool isn't on `PATH` — with `uv tool install`, that's
   `~/.local/bin`; `uv tool update-shell` fixes the profile.
2. Your shell has a personal alias `lc='ls --color'` shadowing the
   real command. Run `type lc` to see; `unalias lc` to remove.

## A recipe fails with "Permission denied" or "No module named …"

Every sandboxed failure ends with this trailer:

```
this ran under the lc sandbox (landlock) — a permissions or missing-file
error can mean the command reached for something outside the declared
environment
```

Recipes run in the project's locked environment, with the tree
read-only apart from their own output directory. The common cases:

- **`ModuleNotFoundError`** — the package isn't in the project's lock.
  `uv add <package>`, commit, re-run. (Installing it on the host with
  `pip` changes nothing a recipe sees — that's the point.)
- **Reading a file outside the project** — declare it as an ASTRA
  input; declared inputs are readable and their content becomes part
  of the output's provenance.
- **Writing outside the output directory** — a recipe's product
  belongs in `{output}`; for true scratch files, use
  `tempfile.mkdtemp()`, which lands in the writable temp area.

To probe interactively, `lc run <command>` runs any command under
exactly the isolation a recipe gets — if it works there, it works as a
recipe.

## Everything shows `behind` after a `uv add`

Not a problem, and nothing was invalidated. `behind` means: the output
is still exactly what the spec asks for, but the environment has moved
since it was made. Environment changes deliberately don't trigger
rebuilds — the manifest records which environment and commit produced
each output, so nothing is lost by leaving it. When you do want them
remade under the current environment:

```bash
lc materialize --refresh
```

See [Core Concepts](concepts.md) for the `stale` / `behind`
distinction.

## Everything shows `stale` after a spec edit

`stale` means the spec now defines the output differently than it was
made — you edited its recipe, a decision, or a declared input's
content changed. That's the invalidation model working; the next
`lc materialize` remakes exactly those outputs.

One edit that deliberately does *not* invalidate: changing your
analysis code (`src/…`). The recipe *string* is the identity, so if
you want code changes to cascade, declare the source file as an ASTRA
input of the outputs it shapes — that choice is yours to make per
output.

## "the content is not in this clone"

```
data/points.csv: the content is not in this clone — git-annex holds a
reference to it, not the data. Fetch it with `git annex get data/points.csv`.
```

The clone has the *pointer* to an annexed file but not its bytes.
`lc materialize` fetches the declared inputs it needs by itself; the
read-only verbs (`lc status`, `--check`) never transfer data, so they
report the fact instead. Fetch by hand only when you want the bytes
for your own inspection.

## "fatal: … clean filter 'annex' failed"

```
git-annex filter-process: line 1: git-annex: command not found
error: could not read greeting from subprocess 'git-annex filter-process'
error: initialization for subprocess 'git-annex filter-process' failed
fatal: data/catalog.fits: clean filter 'annex' failed
```

Your shell's `PATH` has no `git-annex`, so git could not run the filter
that turns a large file into an annex pointer. **Nothing was staged**,
which is the point: without `filter.annex.required=true` — which
`lc init` sets — git would have exited 0 and committed the raw bytes
into history instead.

`git-annex` ships with `lc`, so a tool install puts both on your `PATH`:

```bash
uv tool install lightcone-cli
git-annex version
```

If `lc` runs but `git-annex` does not, uv's tool directory is not on
your `PATH` — run `uv tool update-shell` and open a new shell. Running
`lc` through `uvx` puts nothing on your `PATH` at all, so a plain
`git add` cannot work that way.

This failure is deliberately loud. `lc init` sets
`filter.annex.required=true` in every project precisely because
without it git handles the same situation by printing the error,
**exiting 0, and staging your data's raw bytes into git history** —
committing a multi-gigabyte dataset into git proper, silently, where
every clone carries it forever. A refused `git add` costs you one
`lc init`; the silent version costs you the repository.

## "… and this is a NERSC login node"

`lc materialize` executes recipes, and on centers `lc` recognizes it
refuses to do that on a shared login node. The refusal prints the
center's own `salloc` and `sbatch` spellings — copy one, run the same
command inside the allocation. `lc status`, `lc materialize --check`,
`lc build` and `lc run` work anywhere. See
[Running on a Cluster](cluster.md).

## git doesn't know who you are

Every output is committed, so a machine that has never committed needs
an identity before the first run — `lc materialize` checks up front,
before any recipe spends time:

```bash
git config --global user.name "Ada Lovelace"
git config --global user.email "ada@example.org"
```

## Containerized projects

- **"image absent"** — the declared image hasn't been built and
  committed yet: `lc build` (announced by materialize too, which
  builds it as a preflight when missing).
- **No runtime found** — install [Podman](https://podman.io/) or
  [Docker](https://docs.docker.com/get-docker/); detection is
  automatic and there is nothing to configure.
- **Architecture mismatch** — the committed archive records the
  architecture it was built for, and a host that can't execute it is
  refused before the recipe would have died mid-run. Build on a
  matching host (on NERSC, a login node), commit, push, and pull on
  the other side.

## Filing a bug

Open an issue at
[github.com/LightconeResearch/lightcone-cli/issues](https://github.com/LightconeResearch/lightcone-cli/issues).
Include the output of `lc --version`, the command you ran, and the
full message — the refusals are designed to be pasted.
