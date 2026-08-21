# lc materialize

Make the analysis's outputs, and commit each one as it lands. This is
the build verb: it validates the spec, converges the environment, runs
every recipe that needs running — in dependency order, in parallel
where the graph allows — and commits each result together with its
manifest, in a commit whose message is a replayable run record.

## Synopsis

```text
lc materialize [OPTIONS] [TARGETS]...
```

With no targets, everything the spec declares, across every universe.
A target narrows the run to an output and whatever it depends on:

- `fit` — the output `fit` in every universe that has it.
- `robust/fit` — exactly one universe's output.

A target that matches nothing is an error listing what exists —
quietly making nothing is the least useful thing a build tool can do.

## What gets remade

An output is remade when it is `stale` — the analysis defines it
differently than it was made (a changed recipe or decision), one of
its declared inputs changed content, or it was edited by hand since.
Inputs are compared by content, so a rebuild that comes out
byte-identical stops the cascade there.

An output that is `behind` — still exactly what the spec asks for,
but made under an earlier environment — is reported and left alone;
`--refresh` widens the run to remake those too. A `current` output is
never touched, under any flag.

## The run's contract

- **Starts clean, ends clean.** A dirty tree is a refusal (the message
  sorts your uncommitted work from stray files under `results/`); a
  failed or interrupted recipe's partial work is rolled back.
- **Fetches what it needs.** Declared inputs whose annexed content is
  not in this clone are fetched before anything hashes.
- **Commits as it goes.** Each output lands in its own commit, written
  by the driver in one thread while other recipes keep running.
- **Reports every independent failure.** One failing recipe doesn't
  abort the rest; its dependents report `blocked` and the run exits 1
  with all of it listed.
- **Maintains the publication view.** With a `[project].license`
  declared, the run converges `ro-crate-metadata.json` in a trailing
  commit.

On a containerized project, the run resolves the committed image first
(building it as a preflight if the declaration is committed but the
image never built). Inside a SLURM allocation, the run spans every
allocated node — see [Running on a Cluster](../user/cluster.md).

## Check mode

`--check` classifies every output without executing, committing, or
fetching anything, and exits `1` if a run would do work — the gate a
script or CI job branches on. It is exempt from the dirty-tree
refusal: reading the state of a project before deciding what to commit
is what it is for.

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--check` | off | Report what would run and why; exit 1 if anything is out of date. |
| `--refresh` | off | Also remake `behind` outputs. Never touches `current` ones. |
| `--json` | off | Emit the report as JSON on stdout. |

There is deliberately no `--jobs` (a run takes every core; sizing
belongs to the allocation you run it in), no `--force`, and no flag to
*skip* a stale output — deleting its directory is your own file
operation, and stronger consent than a flag.

## The JSON report

```json
{
  "ok": true,
  "up_to_date": true,
  "made": [],
  "current": ["baseline/fit", "robust/fit", "baseline/fit_plot", "robust/fit_plot"],
  "behind": {},
  "failed": [],
  "blocked": [],
  "planned": {},
  "warnings": [],
  "notes": []
}
```

The first two keys are the ones to branch on: `ok` — everything
attempted finished; `up_to_date` — nothing needed doing (a failed run
is never up to date, and `behind` outputs don't count against it).
`planned` is check mode's answer, mapping each would-run output to why;
`behind` maps each left-alone output to the commit that can rebuild its
environment. `notes` carries sandbox messages verbatim — denial
remedies are built to be pasted.

## Examples

```bash
lc materialize                     # everything, all universes
lc materialize fit                 # one output (and upstreams), every universe
lc materialize robust/fit          # one universe's output
lc materialize --check             # would anything run? (exit 1 = yes)
lc materialize --refresh           # also remake behind outputs
lc materialize --check --json      # the machine-readable gate
```
