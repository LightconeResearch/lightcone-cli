---
name: lightcone
description: >
  Entry point for any Lightcone / ASTRA project. ALWAYS invoke this FIRST
  whenever an `astra.yaml` or a `.lightcone/` directory exists in the working
  dir or any parent, or whenever the user mentions ASTRA, a reproducible
  analysis, or asks to start, reproduce, migrate, run, validate, or publish
  one, or whenever they seem new to the ecosystem or unsure how to proceed —
  even if they don't name a skill. It carries the lay of the land (what ASTRA
  is, the spec-code invariant, the core `lc` loop), the two references you
  almost always need — the `astra.yaml` spec and the `lc` workflow — a router
  to the task skill for whatever you're doing, and the posture for guiding a
  researcher through the ecosystem at their level so they never have to think
  about how Lightcone works inside.
allowed-tools: Read, Glob, Grep
---

# Lightcone — start here

You're in (or working on) a **Lightcone** project: a reproducible scientific
analysis described by an **`astra.yaml`** spec and materialized by the **`lc`**
CLI. This skill is the entry point. It gives you the lay of the land, carries
the two references you reach for in almost every session (the spec and the
workflow, below), and routes you to a task skill when you're doing a specific
job. Stay oriented here; pull a reference section or a task skill as you need it.

## Meet the user where they are

Most researchers who land here care about their *science*, not about how
Lightcone works under the hood. Absorb the machinery so they don't have to:
take what they want in their own terms and turn it into the right Lightcone
move, explaining only as much as actually helps them.

- **Gauge familiarity, then calibrate.** A newcomer needs the ecosystem
  sketched — what ASTRA is, what reproducibility buys them, why decisions and
  universes matter — and a steadier hand; a fluent user wants you to get out of
  the way and just do it. Read it from how they talk; if it's genuinely unclear
  and would change how you proceed, ask one light question rather than guess.
- **Translate intent, not internals.** They'll say "I want to reproduce this
  paper," "let me try a different prior," "I want to make this analysis
  shareable." Map that to the move — a reproduction (`/lc-from-paper`), a new
  universe over an existing decision, a migration (`/lc-from-code`) — and drive
  it. They shouldn't need the words `astra.yaml`, `recipe`, or `universe` to get
  there; introduce a concept only when learning it genuinely helps.
- **Encourage best practices in the moment.** When you see the better move,
  offer it with its reason: "this looks like a real methodological choice — make
  it a decision so we can sweep alternatives?", "let's parameterize that instead
  of hardcoding," "this stage could be its own sub-analysis so it's reusable." A
  nudge with a why, not a lecture.
- **Carry the complexity yourself.** Reproducibility, the spec-code invariant,
  the activation gate, container execution — your concern, not theirs. Keep the
  user in their scientific frame and let the structure form around what they're
  actually trying to do.

## The lay of the land

- **`astra.yaml`** is the spec: the analysis's decisions, inputs, outputs,
  recipes, evidence, and narrative. It is the source of truth.
- **`lc`** is the CLI: it materializes outputs by running each recipe inside
  its declared container, writing a content-addressed manifest next to every
  result. Treat the execution engine as a black box — always go through `lc`.
- **The Spec–Code Invariant:** `astra.yaml` and the code must never diverge.
  Change one, update the other in the same breath (a new decision, a changed
  recipe, a renamed output → update both, then `astra validate astra.yaml`).
- **An output is not done until `lc run` produces it.** Running scripts
  directly is for debugging only; final results must come from `lc run` so
  they're reproducible.

**The whole point is reproducible, traceable science — and that is broader than
the "official" outputs.** Any result you will *lean on* — a number that goes in
the paper, a finding that settles a decision, a verdict you'll cite — must be
produced by `lc run` from the spec, so it can be regenerated and trusted. This
includes *investigations and diagnostics*, not just the headline pipeline: a
convergence study, a noise calibration, a bias characterization — if it yields a
finding, it **graduates into `astra.yaml`** (as an output, or its own *diagnostic
sub-analysis*), it does not stay an ad-hoc script. When you catch yourself about
to lean on a result from a script that `lc` doesn't manage, that's the signal to
bring it into the spec. The exception is genuinely lightweight inspection —
"what's the max S/N in this catalog?", loading a data product for a quick look —
which is fine to run directly and never needs ASTRA. The test: *will I cite this,
or decide something based on it?* If yes → it belongs in ASTRA. (And heavy compute
always goes to the scheduler — `lc run` dispatches to SLURM/Dask, `sbatch` for
anything not yet in the spec — never the shared login node.)

Core loop — enough to orient; the `lc` reference below carries the depth:

```bash
lc status [--json]      # what's ok / stale / missing / alias, per universe
lc run [OUTPUT...] [-u UNIVERSE]   # materialize (build iteratively, one output at a time)
lc verify               # recompute hashes, walk the provenance chain
astra validate astra.yaml
```

## Core references — read as needed

These two carry the depth you reach for in almost every session. They're
bundled here as references, not separate skills: activating `/lightcone` is
enough — read the section you need when you need it, rather than holding it all.

- **[`references/astra.md`](references/astra.md)** — the `astra.yaml` spec:
  decisions and what counts as one, options, inputs/outputs, recipes, prior
  insights & findings, evidence, sub-analyses, narrative, composition mechanics.
  Read it whenever you read, write, validate, or debug a spec.
- **[`references/lc-cli.md`](references/lc-cli.md)** — the `lc` workflow:
  commands, the Spec–Code Invariant, status interpretation
  (`ok`/`stale`/`missing`/`alias`), failure diagnosis, multiverse runs, scratch
  overrides, WRROC export. Read it whenever you run, debug, or diagnose the flow.

## Task skills — activate for the job

Each task carries its own procedure in its own skill. Activate the one that
matches what you're doing (the references above stay available throughout):

| You're doing… | Activate |
|---|---|
| Starting a new analysis from a research question | **`/lc-new`** |
| Reproducing a published paper end-to-end | **`/lc-from-paper`** |
| Migrating an existing codebase into ASTRA | **`/lc-from-code`** |
| Writing narrative prose in `astra.yaml` narrative blocks | **`/narrative`** |
| Turning an arXiv ID / DOI into a `work/reference/` directory | **`/paper-extraction`** |
| Comparing produced figures against a reference paper | **`/figure-comparison`** |
| Auditing manuscript claims against the implementation | **`/check-sentence-by-sentence`** |
| Verifying every citation in a manuscript | **`/citation-audit`** |
| Hit a bug or rough edge in the tools | **`/lc-feedback`** |

A task often leans on the references as it goes — reproducing a paper reads
both `astra.md` and `lc-cli.md` throughout. Pull a section when you need it.
