# <paper title> — Reproduction Plan

<!-- The human-readable contract for what gets reproduced and how hard. Drafted in
     ORIENT (main session), presented in plan mode, and committed as PLAN.md on
     approval — alongside CLAUDE.md and the work/reference/ substrate. The plan carries
     PROSE SKETCHES of the targets and the decomposition; the Workflow's first phase
     (ARCHITECT) realizes those into the formal targets/targets.md ledger and astra.yaml
     skeleton. This is a STATIC RECORD of what we agreed to: the workflow reads it for
     intent and scope, but does not rewrite it. If the agreement itself changes, a later
     session amends this file in the main session, not the workflow. -->

Reproduce **<paper title>** (<arXiv ID>, DOI <doi>) — <one-line subject, e.g. "BAO scale measurement from DESI DR1">.

## Goal

<The headline, then what "done" means at the chosen fidelity. Concrete: which targets
 reproduced, what verdict against them, what validation passes. E.g.: "A complete
 astra.yaml whose recipes produce reproduced versions of <list of targets>, with the
 VERIFY phase's per-target tests green (each reproduced value within the paper's stated
 uncertainty) under `astra validate astra.yaml --verify-evidence` and an all-`ok`
 `lc status --universe baseline`." Pin "done" to the fidelity intent below — a sanity
 check and a no-deadline reproduction have different finish lines.> 

## Fidelity intent

<!-- THE STOPPING CRITERION. The workflow's VERIFY phase reads this (carried in
     args.intent) to size its fix-loop: how many rounds to spend, how close is close
     enough per target. Capture the user's prose answer from the ORIENT interview
     verbatim or in close paraphrase, including the time/compute/token budget — both the
     aesthetic dimension (what "good enough" looks like) and the pragmatic one (what we
     have to spend). Pin it against the paper's actual headline numbers. -->

<The user's prose answer. E.g.: "Just checking the analysis is tractable — an afternoon
 of compute, one or two fix rounds, accept what's close." / "Figure 3 must land within
 its stated 1σ; the rest can stay rough — overnight." / "Full fidelity on the BAO fit,
 baseline elsewhere — a few days." / "Every primary and secondary target within stated
 tolerance, no hard deadline.">

**Budget:** <wall-clock + compute/token envelope, e.g. "~4 h, single workstation, no cluster" or "overnight on the cluster, ~2 GPU-h">.

## Scope

**In:** <the figures / tables / numbers being reproduced, and the methodological span — name them, these are what Targets enumerates.>

**Out:** <explicit exclusions, fenced from drift — sub-analyses, ablations, or extensions we are deliberately not reproducing this pass.>

## Targets (sketch)

A one-line-per-target sketch the user ratifies in plan mode. The Workflow's ARCHITECT turns this into the formal [`targets/targets.md`](targets/targets.md) ledger — every target with its priority, expected value + stated uncertainty, and comparison guidance — and VERIFY writes one test per ledger row.

<One line per in-scope replication target: what it is, the paper's claimed value with its
 stated uncertainty, primary/secondary. E.g.:
 - Primary · BAO scale α = 0.987 ± 0.012 (Table 3) — metric, within stated 1σ
 - Primary · α posterior (Fig 4) — figure, shape + peak location
 - Secondary · reconstructed ξ(s) (Fig 2) — figure, trend + zero-crossing>

## Decomposition (sketch)

The rough carving of the paper into sub-analyses — *prose*, grounded in the code scan. ARCHITECT realizes this into the [`astra.yaml`](astra.yaml) skeleton (inputs, outputs, narrative; the later phases fill decisions/findings/recipes), taking the code's stage boundaries as canonical where they differ from the paper. Each sub-analysis is a coherent unit the workflow specifies, implements, and verifies.

| Sub-analysis | What it produces | Why it's its own unit |
|---|---|---|
| `<sub_id>` | <outputs> | <why split here — distinct data stage, distinct method, distinct target> |
| `<sub_id>` | <outputs> | <…> |

<One or two lines on the data-flow spine if it isn't obvious from the table — what feeds
 what. If the paper is monolithic, say so: a single `root` analysis, no split.>

## Evidence

The canonical sources this reproduction is built against:

- **Paper:** `work/reference/{paper.pdf, source/ or document.md, index.json, astra.yaml}` — the substrate `/paper-extraction` landed during ORIENT. `index.json#citations` carries each cited paper's resolved DOI for the LITERATURE phase.
- **Code:** `work/reference/code/` — the reference repo cloned during ORIENT; scan inventory at `work/reference/code-index.md`. **Code is canonical where it disagrees materially with the paper.** <Omit this bullet if no public repo exists — implement fresh from the spec, paper is the only anchor.>
- **Paper DOI:** <doi>
- **arXiv ID:** <id> (if applicable)
- **Code repo URL:** <url, or "none — no public repository">
