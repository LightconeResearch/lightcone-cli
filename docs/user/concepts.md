# Core Concepts

The mental model behind `lc`, in one page. Nothing here is required to
follow [Getting Started](getting-started.md) — come back when you want
to know *why* the tool behaves the way it does.

## A project is three files

A lightcone project is a directory holding an ASTRA spec and a uv
project:

- **`astra.yaml`** describes the analysis — inputs, outputs, recipes,
  methodological decisions. It is the single source of truth: everything
  `lc` does is downstream of it.
- **`pyproject.toml` + `uv.lock`** describe the environment — every
  package a recipe may import, resolved to exact versions. The `.venv`
  is built *from* the lock and is disposable; the lock is what's real,
  and it travels in git.

There is no global configuration, no registry, no state outside the
project. Clone the repository and you have everything except two pieces
of local machinery (`.venv` and the git-annex initialization), which
`lc init` rebuilds.

Adding a dependency is a uv operation, not an `lc` one:

```bash
uv add numpy
```

That updates `pyproject.toml`, re-locks, and syncs `.venv` in one step.
Recipes import from the locked environment and nothing else — a stray
`pip install` on your machine changes nothing they can see.

## An output has an identity, and three facts about it

Every materialized output records, in its
`.lightcone-manifest.json`:

1. **What it is** — a hash of its recipe and the decision values that
   shaped it (its *definition*).
2. **What it was made from** — a content hash of each declared input.
3. **What it ran under** — a hash of the environment (the lock, the
   interpreter, the image declaration if any), plus the git commit the
   run started at.

Those three facts are deliberately not one fact, because they age
differently — and that is what the three states mean:

| state | means | what `lc materialize` does |
|---|---|---|
| `current` | the output is exactly what the spec asks for, made from these inputs, under this environment | nothing |
| `stale` | the output **contradicts** the project: the spec now defines it differently, or an input's content changed | remakes it |
| `behind` | the output is still exactly what the spec asks for — only the **environment** moved since it was made | reports it, leaves it alone |

The line between `stale` and `behind` is contradiction versus
circumstance. A stale output is mislabelled — keeping it would be a
lie, so it is remade. A behind output is not wrong in any way: one
`uv add` for a plotting script rewrites the lock for the whole project,
and remaking a week of computation over that buys nothing. Its manifest
records exactly which environment and commit produced it, and that
commit's own `uv.lock` reconstructs the environment if you ever need
it.

When you *do* want behind outputs remade — before a release, say —
that is one flag:

```bash
lc materialize --refresh
```

`--refresh` only ever widens a run: a `current` output stays current
under it, and there is deliberately no flag in the other direction —
nothing suppresses the rebuild of a stale output.

One more way an output can be stale: a hand edit. Every output is
committed by the run that made it, so a file changed by hand and
committed shows up in history under a commit that is not a run record —
and the output classifies `stale` everywhere, with `lc status` naming
the foreign commit.

## Everything is committed, and the tree stays clean

`lc` versions results in the project's own git repository: git carries
the history and the small files, git-annex carries the data bytes —
transparently, behind the ordinary `git add` / `git commit` you already
type.

That model has two consequences you'll feel:

- **A run starts from a clean tree.** Every output is committed
  together with the code that produced it; a run that started from
  uncommitted edits could not say what that code was. So: commit, then
  materialize.
- **A run ends with a clean tree.** Each output is committed as it
  lands — with its manifest, in a commit whose message is a *run
  record* that `datalad rerun` can replay. A failed recipe's partial
  work is rolled back. Your `git log` is the build log.

`results/` is `lc`'s to write. Don't put files there by hand — a
hand-placed file has no manifest and no run record, and the foreign
write check above exists precisely to catch it.

## Two modes, derived from the project

How recipes execute is never configured — it is read off the project:

- **Direct mode** (the default): recipes run on your machine, in the
  project's `.venv`, under an OS sandbox — Landlock on Linux, Seatbelt
  on macOS. The project tree is read-only except each recipe's own
  output directory; undeclared tools don't execute.
- **Containerized mode**: declaring a `[tool.lightcone.image]` table in
  `pyproject.toml` *is* the switch. Recipes then run inside a
  content-addressed image built from that declaration — and the image
  itself is saved into the repository as versioned content, so a clone
  obtains the exact bytes with no registry and no credentials.
  `lc status` shows the mode and the image's state.

Either way, every manifest records what enforcement actually ran
(`hermeticity`) — a host with no sandbox mechanism runs the recipe and
says so, rather than pretending.

## Reading and gating are different verbs

- **`lc status`** reports. It always exits 0 — a state is not a
  failure — runs nothing, and doesn't mind a dirty tree, because the
  moment you most need it is when things aren't clean. It's also the
  verb that shows the commit each output was made at.
- **`lc materialize --check`** gates. It classifies everything without
  running anything and exits 1 if a run would do work — the thing a
  script or CI job branches on.

Both have `--json`; the first two keys of the check report, `ok` and
`up_to_date`, are the ones to branch on.

## Publication is a license away

Declaring a `license` under `[project]` in `pyproject.toml` is
declaring the intent to publish. From then on, every `lc materialize`
maintains `ro-crate-metadata.json` at the project root — an
[RO-Crate](https://www.researchobject.org/ro-crate/) describing the
project, its outputs, and the runs that produced them. The repository
*is* the crate; depositing it is `git archive` on something you already
have.

## Where to next

- [Running on a Cluster](cluster.md) — the same model on SLURM.
- [Troubleshooting](troubleshooting.md) — the refusals quoted, with
  their remedies.
- [Glossary](glossary.md) — the terms, one at a time.
