# REVIEW — two parts: in-workflow synthesis, then main-session close-out

REVIEW spans the seam between the autonomous middle and the human. It has two
parts that run in two different places:

- **PART A — in-workflow REVIEW.** The workflow's last phase (`reproduce_workflow.js`'s
  `Review`). A single synthesizer agent surveys the converged reproduction, fixes
  only obvious leftover problems, writes `report.html`, and **returns a structured
  summary** as the workflow's value.
- **PART B — main-session CLOSE-OUT.** The second interactive bookend, run back in
  the user's main session *after* the workflow returns. It renders the validation
  surfaces with the user reachable (`/figure-comparison`, `/check-sentence-by-sentence`),
  walks `open-questions.md` *with the user*, lands resolutions, and commits the close.

The split is forced by reachability. The workflow runs detached — its agents have
no `AskUserQuestion`. So Part A does everything that needs no user (synthesize,
render, report-back), and Part B does everything that needs one (the two comparison
skills both list `AskUserQuestion` in `allowed-tools`; the open-questions walkthrough
is the user's editorial pass). VERIFY has already honored the fidelity intent —
neither part re-opens a fix campaign.

---

# PART A — in-workflow REVIEW (the synthesizer)

A single agent, no fan-out. It is the workflow's terminal `agent()` call, spawned
with the VERIFY verdict in hand. Its job: take the converged state, fix what is
*obviously* broken, render a self-contained report, and hand a compact summary back
to the main agent. It does **not** edit `astra.yaml`'s science — VERIFY's fix-loop
already ran to the intent's bound; opening a new round here would ignore the governing
parameter.

## Inputs

- `astra.yaml` — the filled spec (decisions, findings, recipes, resolved citations).
- **the VERIFY verdict** — passed in by the workflow as JSON (`results[]` per target:
  `pass`, `reproduced`, `expected`, `diagnosis`; plus `all_pass`, `failing[]`). This is
  the source of truth for what landed and what didn't — do not re-run the test suite.
- `results/baseline/<output>/` — the reproduced figures / tables / metrics.
- `targets/targets.md` — the replication-target ledger (priority, expected value +
  stated uncertainty, comparison guidance per target).
- `PLAN.md` — the **Fidelity intent** section is the lens: a target below intent on a
  "Figure 3 must be right" reproduction is a real gap; the same target below intent on
  an "afternoon sanity check" is expected and gets named as a known limitation, not a defect.
- `open-questions.md` — what the detached workers couldn't resolve and applied a
  best-judgment default to (code-canonical adjudications, citations with no supporting
  quote, targets accepted below intent). These become Part B's walkthrough; surface them,
  don't resolve them.
- `tests/test_<target>.py` — the committed claim tests; pointers, not for re-running here.

## What the synthesizer does

1. **Survey, don't recompute.** Read the verdict, `astra.yaml`, `targets/targets.md`,
   `PLAN.md`'s fidelity intent, and `open-questions.md`. Build the picture of where the
   reproduction landed against each target and against intent. Spot-read
   `results/baseline/` to confirm the verdict's `reproduced` values are actually on disk.

2. **Fix only the obvious.** A mislabeled output id, a recipe typo, a stray
   `prior_insights` entry that `astra validate` flags, a finding whose `replicated_value`
   wasn't written back from the verdict — these are housekeeping and you fix them in place
   (then `astra validate astra.yaml`). **Do not** start a new fix campaign: no re-deriving a
   below-intent number, no re-running outputs to chase a tighter match. The intent has been
   honored; a target that VERIFY accepted below intent stays below intent and gets *named*,
   not *chased*.

3. **Write `report.html`** at the workdir root — a self-contained, phone-renderable
   side-by-side of **paper claims vs reproduced values**, one row per replication target.
   Single file, no sibling assets, base64-inline any small images. Use the editorial
   **parchment palette** (the `--paper`/`--surface`/`--ink`/`--teal`/`--amber`/`--mauve`
   tokens from `/figure-comparison`'s Vellum aesthetic — cream page card on parchment body,
   EB Garamond + JetBrains Mono, faded-ink status, no saturated color). Per target show:
   the paper claim verbatim, the reproduced value, the stated tolerance, a status
   (pass / below-intent / failing), and a one-line note from the verdict's `diagnosis`.
   A header line carries the paper identity and a one-line health summary
   (`N/M targets pass; K below intent`). This is the artifact the user reads on their
   phone and the substrate Part B's `/figure-comparison` builds on — keep it honest:
   below-intent and failing targets are shown as such, never dressed up.

4. **Commit** (`review: synthesize reproduction, report.html`).

## Structured return (the workflow's value)

Return a compact summary — this is what the main agent reads to drive Part B, so it
must be self-contained:

- `targets`: `{passed, total}`.
- `below_intent`: per target that landed below the fidelity intent — `target_id`,
  `reproduced`, `expected`, and **why** it's acceptable-or-not under the stated intent
  (one line). This is the honest core: the reproduction's gaps, graded against what the
  user asked for.
- `open_questions`: the entries from `open-questions.md` the user must adjudicate at
  close-out (each: origin phase, the default the worker applied, one-line summary).
- `report`: the `report.html` path.
- `notes`: anything the close-out should know (a disagreement worth surfacing, a result
  that's right but fragile).

The workflow folds this into its own return value alongside the raw VERIFY verdict and
the `report.html` path. **The main agent reads that return — it is the input to Part B.**

## Part A discipline

- **The verdict is upstream truth; don't re-litigate it.** REVIEW reports what VERIFY
  found. If you think a passing target is actually wrong, say so in `notes` for the
  human — don't silently re-grade or re-run.
- **No new fix campaign.** The fidelity intent bounded VERIFY; it bounds you. Obvious
  typos, yes; chasing a below-intent number, no.
- **Honest report.** A reproduction that landed three of five targets says so in the
  header. A `report.html` that hides the two misses is worse than no report.

---

# PART B — CLOSE-OUT (main session)

When the workflow returns, run the close-out from the user's main session. Read the
workflow's return first — `report.html` path, the `targets`/`below_intent` summary, the
`open_questions` list, the raw VERIFY verdict. That return *is* your agenda. Part B
renders the surfaces with the user reachable, walks the questions with them, lands the
resolutions, writes the final record, and commits.

**Why this runs in the main session, not the workflow:** `/figure-comparison` and
`/check-sentence-by-sentence` both carry `AskUserQuestion` in `allowed-tools`, and the
open-questions walkthrough is a series of `AskUserQuestion` prompts. None of that works
detached. Invoking either skill under the `Task` tool, or trying to ask the user from
inside the workflow, fires the prompt into nothing.

## Step 1 — render the validation surfaces

### `/figure-comparison` (mandatory)

Invoke the `/figure-comparison` skill from the main session. It builds a portable HTML
side-by-side — paper artifacts from `work/reference/` (figures in `work/reference/figures/`,
tables in `work/reference/tables/`) on the left, reproduced artifacts from `results/baseline/`
on the right, red
flags wherever a counterpart is missing — at `.lightcone/comparison.html`.

It **builds on Part A's `report.html`**: the skill reads `targets/targets.md` as its
scope ledger (in this architecture there is no `comparison-report.yaml`), so it enumerates
exactly the replication targets the synthesizer just reported on, now with images embedded
and rendered side-by-side. The workflow's `report.html` is the claims-vs-values table; the
figure-comparison is the visual side-by-side. Both land in front of the user.

Show the user the path and `SendUserFile` the HTML so it reaches their phone. Do **not**
spawn `/figure-comparison` under the `Task` tool — its `AskUserQuestion` would have no
user to reach.

### `/check-sentence-by-sentence` (opt-in)

Ask the user via `AskUserQuestion` whether they want the claim audit. It's optional
because for many reproductions the two comparison surfaces already settle "did it match?";
the sentence-by-sentence audit earns its keep when the paper makes many specific
quantitative claims and the user wants each anchored to a code location. If yes, invoke
`/check-sentence-by-sentence` (same discipline — main session only, never under `Task`),
show the user the output path.

## Step 2 — walk `open-questions.md` with the user

This is the editorial heart of the close-out: the detached workflow applied best-judgment
defaults to things it couldn't ask about, and now the user ratifies or overrides them. The
workflow's return `open_questions` and the on-disk `open-questions.md` are the same agenda;
read the file for the full context. For each unresolved entry, surface it via
`AskUserQuestion` with:

- **The question** (verbatim from the file).
- **Origin** — which phase flagged it (SPECIFY's code-canonical adjudication, LITERATURE's
  unresolved citation, VERIFY's below-intent acceptance).
- **The default the workflow applied** — e.g. "took the code's value as canonical over the
  paper's," "left the citation unsupported," "accepted 4.8σ against the paper's 5σ under the
  afternoon intent."
- **Three options** — ratify the default, override (user spells out their choice), or defer
  (leave as a named limitation in the final record).

The three recurring kinds, all surfaced here:

- **Code-canonical adjudications.** Where paper and code disagreed materially, the workflow
  took code as canonical for numerics/method and preserved both options in `astra.yaml`. The
  user confirms or flips canonicity.
- **Unresolved citations.** A `prior_insights` placeholder LITERATURE found no supporting
  quote for. The user can point to the right reference, accept it as a known gap, or drop the
  claim.
- **Below-intent targets.** A target VERIFY accepted short of the paper's value under the
  fidelity intent's bound. The user decides: accept as a limitation, or authorize one more
  IMPLEMENT/VERIFY round (which re-opens the workflow for that target — rare; most close as
  named limitations).

Append a `## Resolutions` section to `open-questions.md` capturing what the user said for
each entry — this makes the resolution durable for re-runs and future sessions; do **not**
delete the original questions. Cross-reference `CLAUDE.md`'s **Paper-vs-code disagreements**
log: every entry there should now carry its resolution, inline (ratified default) or by
reference to `open-questions.md`.

If a resolution warrants a spec change (an override), edit `astra.yaml` /
`implementation-notes.md` / `universes/baseline.yaml` and re-run `astra validate astra.yaml`.
If the change would invalidate a result (e.g. flips the canonical method for a primary
output), say so plainly — usually the reproduction is done and the override becomes a named
limitation, but the user may choose to re-launch the workflow against that target.

## Step 3 — write `REPRODUCTION-SUMMARY.md`

A single markdown file at the project root, ~1–2 pages — the canonical record of what this
reproduction landed on. The depth lives in `astra.yaml`, `report.html`, and the workdir
notes; the summary is the door into them. Sections:

1. **What was reproduced** — the paper, the scope, the targets.
2. **Verdict** — from the VERIFY verdict + the user's close-out: `N/M` targets pass; which
   landed below intent and why that was accepted (or what was overridden).
3. **Material decisions** — the paper-vs-code conflicts the workflow surfaced, what the user
   chose (ratified default or override), and why.
4. **Outputs** — one bullet per primary target: the path to the reproduced result under
   `results/baseline/`, the paper value, the reproduced value, a one-line match note.
5. **Open questions, resolved** — pull from `open-questions.md`'s `## Resolutions`; one
   bullet per question + its resolution.
6. **Below-intent / future tightening** — targets accepted short of the paper under the
   fidelity intent, each with a leverage note: what a future pass at a higher intent would
   tighten next. (Persist these into `CLAUDE.md` too, so a future walk-up sees them.)
7. **What was learned** — anything the reproduction surfaced that the paper alone didn't
   (a parameter the code uses but the paper omits, a data cut stricter than stated). The
   reproduction's value back to the literature.
8. **Re-running** — one paragraph: `lc run --universe baseline`, where `astra.yaml` and the
   committed `tests/` live, where `CLAUDE.md` sits so a future Claude Code session auto-loads
   it on walk-up.

Brief, not exhaustive. Long reports get skimmed; short reports get read.

## Step 4 — commit the close

Stage `REPRODUCTION-SUMMARY.md`, `open-questions.md` (with `## Resolutions`), the updated
`CLAUDE.md`, the final `astra.yaml`, the comparison artifacts (`.lightcone/comparison.html`,
the optional sentence audit), and any spec changes from Step 2. Commit:

```
review: <paper-short-name> — N/M targets, summary at REPRODUCTION-SUMMARY.md
```

This commit is the durable mark that the reproduction reached close-out. A future walk-up
reads `CLAUDE.md` + `git log` to know where it stands; the close-out commit +
`REPRODUCTION-SUMMARY.md` are the record.

## Part B discipline

- **The user owns verdict acceptance.** Part A reports where it landed; the close-out lets
  the user *see* it (both comparison surfaces) and *decide* whether they accept it. The
  close-out renders and asks; it does not unilaterally declare done.
- **`/figure-comparison` and `/check-sentence-by-sentence` are main-session only.** Both use
  `AskUserQuestion`; never spawn them under `Task`. This is *why* the close-out is a bookend
  and not a workflow phase.
- **Open-question resolutions are durable.** Append to `## Resolutions`; never delete the
  originals. The next re-run / future session reads what was decided.
- **Don't invent further work.** Once the user accepts the verdict and the below-intent items
  are recorded, the reproduction is done. A future session, or the user revisiting at a higher
  intent, decides whether to tighten anything. The fidelity intent already said how hard to
  push — honor it at the close too.
