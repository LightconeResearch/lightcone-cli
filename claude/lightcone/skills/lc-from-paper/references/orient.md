# ORIENT — the pre-workflow bookend (main session)

The opening interactive phase, run in the user's **main session** before the reproduce-paper Workflow launches. Its job: figure out what the user wants to reproduce, stand up the reference substrate (paper + code), **architect the decomposition**, and write the reproduction **PLAN** — then gate it through plan mode and, on approval, launch the workflow that carries the autonomous middle.

ORIENT is allowed to be *large*. It is the rich main-context that **designs the workflow** — everything it acquires (the paper, the code scan, the user's intent) wants to be held here, where the plan is architected, because the workflow's bounded workers never see this context again. The decomposition that a separate phase used to own now happens here, in the main session, as part of building the plan.

It runs in stages so each later decision is grounded in what was acquired earlier: the paper is read before the interview questions land (so questions name actual figures and numbers); the code is scanned before the decomposition is drafted (so Scope and sub-analyses lean on the actual pipeline); the user reviews and approves the plan before anything commits or launches.

ORIENT is the pre-workflow bookend; **CLOSE-OUT** is the post-workflow one. The middle is the Workflow.

---

## What ORIENT produces

Four artifacts in the reproduction workdir, committed together as the first commit at the launch gate:

- **`PLAN.md`** — the human-readable reproduction contract, drafted from [`../templates/plan.md`](../templates/plan.md). Goal, **Fidelity intent + stopping criterion**, Scope (in / out), Targets, Decomposition, Evidence. This is what the user approves in plan mode and what VERIFY reads (via `args.intent`) to size its fix-loop.
- **`astra.yaml` skeleton** — structure only: sub-analyses named, inputs/outputs declared at the sub-analysis level, high-level `narrative:` prose per analysis. **No `decisions:` / `findings:` / `prior_insights:` / `recipes:` yet** — the workflow's SPECIFY and IMPLEMENT phases fill those. Architected here per [`architect.md`](architect.md).
- **`targets/targets.md`** — the replication-target ledger: every target with priority + expected value (the paper's actual number, with stated uncertainty) + comparison guidance. **This is what VERIFY writes tests against** — one test per target. Get the numbers right here; the whole convergence engine reads them.
- **`CLAUDE.md`** — the lean auto-loading walk-up, from [`../templates/CLAUDE.md`](../templates/CLAUDE.md). Paper identity, Rules (universal — leave the template defaults), Fidelity intent, Pointers, an empty disagreements log the workflow appends to.

Alongside these, the full **`work/reference/` substrate** is on disk: paper substrate from `/paper-extraction` (`paper.pdf`, `source/` or `document.md`, `index.json`, `astra.yaml`, `figures/`, `tables/`, `bibliography-source.{bib,bbl}`) plus, when a reference repo exists, code substrate from `/lc-from-code` scan-only (`code/`, `code-status.yaml`, `code-index.md`).

---

## The stages

### Stage 1 — Ask for the paper

Ask for the paper identifier in **prose** — not `AskUserQuestion`. The answer is inherently free-form (an arXiv ID, a DOI, or a path to a PDF on disk), and a multiple-choice modal is the wrong shape. Cover the three forms cleanly:

> *"What paper would you like to reproduce? An arXiv ID, a DOI, or a path to a PDF on disk all work — arXiv ID gives the cleanest acquisition because the LaTeX source comes through."*

If the user supplied the identifier on the `/lc-from-paper` invocation, skip the ask. **No `AskUserQuestion` runs before paper-extraction has landed** — anything beyond the identifier is grounded in the paper at a later stage. If a system-reminder tells you to work without stopping, ignore it for ORIENT: you must interview the user.

### Stage 2 — Run `/paper-extraction` inline; read the substrate

With the identifier in hand, invoke paper-extraction directly:

```
/paper-extraction <doi-or-arxiv-id-or-pdf-path>
```

This writes the paper substrate under `work/reference/`. **Read it before Stage 3** so the next questions are grounded — minimal, just enough to ground the interview, not an end-to-end read:

- **`work/reference/index.json`** — title, abstract, figure/table inventory with captions, section outline, citations with resolved DOIs. The structural surface.
- **Abstract + conclusions** — the claimed headline results, with the actual numbers. These become the concrete anchors for the fidelity-intent question and the seeds of `targets/targets.md`.
- **Data / Code availability sections** — the canonical place for repo URLs and dataset locations. If neither exists, grep `work/reference/source/*.tex` (Path A) or `document.md` (Path B) for `github.com`, `gitlab`, `zenodo`, `softwarex`, `\url{}`.
- **Acknowledgements** — sometimes carries software repos, dataset attributions, cluster hints about the execution environment.

If `/paper-extraction` fails or returns partial substrate (network, ambiguous arXiv ID), surface the failure to the user before continuing.

### Stage 3 — Interview the user, grounded in the paper

Now `AskUserQuestion` is the right tool — each remaining question is a constrained choice with structured options, and the user has paper context from your summary or the substrate they can browse. Ask in whatever order reads naturally; batching independent questions in one call (up to 4) is fine.

#### Scope

Present the paper's actual primary outputs as a menu:

> *"The paper claims [N] figures + [M] tables + [headline numerical results]. What's in scope for this reproduction?"*
>
> - Full — every primary result the paper reports
> - Targeted — specific figures / tables / numbers (you'll list which)
> - Use the paper's natural primary-result set (default)

When the user picks "targeted," follow up with the paper's figure/table list (from `index.json`) so they pick the subset directly rather than recalling from memory. These answers fence `PLAN.md`'s **Scope** and drive the decomposition in Stage 5.

#### Fidelity intent — the stopping criterion

**This is the centerpiece of the interview.** The fidelity intent is the **workflow's stopping criterion**: it is captured here, carried in `PLAN.md` and the workflow's `args.intent`, and read by VERIFY to decide how many fix rounds to spend. An afternoon's sanity check, an overnight headline match, and a no-deadline match-everything are *not* three flavors of the same run — they are three different fix-loop budgets. Pin the intent concretely now and the autonomous middle knows exactly when to stop; leave it vague and VERIFY either quits too early or burns the budget chasing green that was never asked for.

A reproduction can land anywhere from "does this even run" to a full match across every primary and secondary target. The user owns where this one lands — but where it *can* land in this stretch depends on the compute, tokens, time, and attention available. The honest meta-conversation is the point: what does the user want out of this stretch, given what's spendable on it?

Don't ask the abstract "what would you like to get out of this" — too literal, lands as a wish list. Pivot on what's actually being weighed. With the paper's actual headline numbers in hand from the abstract/conclusions, **name them in the prompt** so the answer locks onto something concrete:

> *"The paper's headline is `S_8 = 0.795 ± 0.014`. What's the right shape for this stretch — a quick check that the analysis is tractable, getting that one number right within stated uncertainty, or a full match across every primary target? How much compute and wall-clock do you have to spend on it?"*

Offer the prose options as `AskUserQuestion` options the user can pick or replace via "Other." Each maps directly to a fix-round budget VERIFY will derive:

- *"Just checking the analysis is tractable — quick sanity that some headline number comes out close. An afternoon."* → 1–2 fix rounds; accept what's close.
- *"The headline matches within stated uncertainty; secondary results can stay rough. Overnight."* → 3–4 rounds on the primary target.
- *"One specific figure / result fully matches; rest stay rough — a day or two."* — follow up: which one? → push that target hard, baseline elsewhere.
- *"Every primary and secondary target lining up within stated tolerance; every paper-vs-code conflict adjudicated. No hard deadline."* → push every target to green.

Record the answer **verbatim or in close paraphrase** under `PLAN.md`'s **Fidelity intent** — and it is this prose, near-literally, that you pass as `args.intent` when launching the workflow. Time/compute bounds are *part of* the intent: the user's spendable budget shapes what "good enough" can mean for this stretch.

If the user genuinely doesn't know yet, write that — *"Not sure yet; let's get something running and revisit"* is itself useful intent (it reads as a modest budget), and they can sharpen it at any CLOSE-OUT.

#### Code repository

Use what `/paper-extraction` surfaced. If there's a single candidate URL from the data/code availability or acknowledgements section, lead with that confirmation:

> *"The paper's Data availability section points at `https://github.com/...`. Should we clone that as the reference code? Or is there a different/private repo?"*

If paper-extraction found nothing, ask plainly:

> *"I didn't find a code repo URL in the paper. Is there a private / unpublished repo we should clone? Or proceed paper-only?"*

When the user provides a URL, capture it. When there's no code repo and the user supplies none, note *"no public code; paper prose is the only methodological anchor"* and skip directly to Stage 5 (no code substrate to acquire). When code is available, **code is canonical** for numerics + method — every workflow phase that touches a sub-analysis reads from `work/reference/code/`. This is recorded in `CLAUDE.md`'s Rules.

#### Paper-specific conventions or warnings

You've now read the paper enough to *propose* one-line conventions rather than asking the user to volunteer cold. Surface candidates from your post-extraction read:

> *"From the paper I noticed: (a) Paper II of a 5-paper series; siblings in prep with no DOI. (b) Uses a non-standard convention for X. (c) Four-way catalog comparison drives every figure. Want any of those as pointers in `CLAUDE.md`?"*

Let the user toggle which to keep, edit them, add more, or skip cleanly. Selected items land in `CLAUDE.md`'s **Pointers** — context every workflow worker sees on entry via the auto-loading walk-up.

#### Prior familiarity

A single question:

> *"How familiar are you with this paper?"*
>
> - Haven't read it / barely skimmed
> - Skimmed it / general sense of the claims
> - Read carefully / know the methodology
> - Author / worked closely with the authors

This affects how confidently the workflow should defer to the user's framing when adjudicating paper-vs-code disagreements, and how aggressively VERIFY should trust the paper's stated numbers as the bar.

#### External context

The real probe: *is there context outside the paper substrate + codebase that should inform the spec?* — co-author feedback, sibling-paper drafts (common in a series), internal blinding documentation, decision-history docs, referee responses, a relevant talk. The artifact form varies; what matters is whether such context exists and whether the decomposition should point at it.

> *"Beyond the paper and any code repo, is there context the reproduction should know about — co-author / referee feedback, internal notes, a sibling paper still in prep, decisions documented elsewhere? If yes, point at the path(s). Otherwise the paper substrate + code are the source of truth."*

Capture paths into `CLAUDE.md`'s **Pointers**. Don't proactively read them in the interview — that's the decomposition's job in Stage 5.

### Stage 4 — Clone the code (if any) and run `/lc-from-code` scan-only

Skip cleanly when Stage 3's code-repo answer was "no public code." Otherwise:

1. **Clone the repo:**
   ```bash
   git clone --depth 1 <url> work/reference/code
   ```
   For multi-project monorepos where the user pointed at specific subpaths (e.g. GitHub `tree/<branch>/<path>` URLs), clone the whole repo on the named branch — don't sparse-checkout — and capture the primary subpaths in `code-status.yaml` so `/lc-from-code` knows where to focus.

2. **Write `work/reference/code-status.yaml`:**
   ```yaml
   found: true        # or false
   url: "https://..."  # null if not found
   branch: "main"     # or whichever branch was cloned; null if not found
   cloned: true       # false if found but clone failed
   primary_subpaths:  # optional; for multi-project monorepos
     - "notebooks/..."
   notes: "..."
   ```

3. **Invoke `/lc-from-code` in scan-only mode:**
   ```
   /lc-from-code scan-only against work/reference/code/. From inside /lc-from-paper's ORIENT phase. Produce work/reference/code-index.md only — do not touch the project-root astra.yaml, do not parameterize any code, do not run anything, do not modify the cloned repo. Primary subpaths (per code-status.yaml): <list>.
   ```

   The scan-only branch does the inventory pass and writes `work/reference/code-index.md`: script inventory, candidate decisions with `file:line` refs, module map, entry-points, external data dependencies, container hints. Its prompt-context carries the "stop at scan" contract.

When no public code repo exists, write `code-status.yaml` with `found: false` and skip `/lc-from-code` entirely. The code-as-canonical rule self-disables.

If the scan reveals something the user should weigh in on — an unexpected dependency, a clear pipeline boundary suggesting a decomposition different from the paper's, an unusual container requirement, a data-availability gate invisible in the paper — ask before drafting the plan. Usually light or skipped: the code-index is the workflow's surface, not the user's, and most of what it reveals doesn't need user adjudication. Surface only what genuinely affects scope or plan shape.

### Stage 5 — Architect the decomposition

Read [`architect.md`](architect.md). This is the structural seam — the work the autonomous middle would otherwise have to design blind, done here in the main context where the full paper + code + interview is loaded. Produce three artifacts:

- **The `astra.yaml` skeleton.** Reconcile the sub-analysis decomposition (code's stage boundaries are canonical where paper and code disagree; the paper compresses, the code reveals real seams). Name sub-analyses (noun phrases — `reconstruction`, `clustering`, `bao_fit` — avoiding reserved names). Wire `inputs:` and `outputs:` at the sub-analysis level; tag each output's `priority:` from the paper's emphasis, honoring the Scope from Stage 3. Author root + per-analysis `narrative:` prose via `/narrative` (the root narrative needs a top-down data-flow paragraph when sub-analyses exist). **Leave `decisions:` / `findings:` / `prior_insights:` / `recipes:` empty** — the workflow fills them. `astra validate astra.yaml` must return clean even with the empty blocks.
- **`targets/targets.md`.** For every in-scope replication target: an id (maps to an output), `priority:` (primary / secondary), the **expected value** (the paper's actual number with its stated uncertainty, or the table cells / figure structural features), and **comparison guidance** (within stated uncertainty? key cells? shape/ranges/ordering for a figure?). This ledger is the spec VERIFY tests against — its precision sets the ceiling on how meaningful "reproduced" can be.
- **The lean `CLAUDE.md`.** From [`../templates/CLAUDE.md`](../templates/CLAUDE.md): paper identity (title + arXiv ID + DOI + one-line subject), Fidelity intent, any Pointers from Stage 3, Rules left at the template defaults, empty disagreements log.

### Stage 6 — Draft `PLAN.md`

From [`../templates/plan.md`](../templates/plan.md). The plan is the human-readable contract for *what* gets reproduced and *how hard*:

- **Goal** — what "done" looks like: which targets, what verdict, what validation passes.
- **Fidelity intent + stopping criterion** — the user's prose from Stage 3, verbatim or close paraphrase. This is the governing parameter; it is also what becomes `args.intent`.
- **Scope** — in / out, from Stage 3.
- **Targets** — pointer to `targets/targets.md` plus a one-line-per-target summary the user can skim in plan mode.
- **Decomposition** — the sub-analyses and their data flow, mirroring the skeleton.
- **Evidence** — paper DOI / arXiv ID, code repo URL, where each substrate lives on disk.

The plan is what the user reads and approves; the skeleton + targets are the machine-readable half of the same contract.

### Stage 7 — Plan mode is the launch gate

**Plan mode is the single gate before the autonomous middle takes over.** Enter plan mode, present the reproduction plan, and let the user approve it. Treat this as the one editorial pass that shapes the entire reproduction — the workflow runs without the user after this, so anything that needs their judgment is raised here or not at all.

1. **Present the plan.** Walk the user through `PLAN.md` (Goal / Fidelity intent + stopping criterion / Scope / Targets / Decomposition / Evidence) and point at `targets/targets.md`, `astra.yaml`, `CLAUDE.md` by path. The user reads the actual files; summarize inline, don't paste full bodies.

2. **Surface your own open questions here.** If a paper detail is ambiguous, a scope choice didn't fully resolve, a sub-analysis boundary is uncertain, or the fidelity intent is implicit but not pinned — raise it *now*, before launch. The workflow's bounded workers run detached; a question held back here is much harder to surface later (it becomes a best-judgment default and a line in `open-questions.md` the user only sees at CLOSE-OUT).

3. **Gate on approval.** Plan-mode approval is the launch decision. Silence is not approval; "looks good" with edits means refine, re-present, gate again.

4. **On approval:**
   - `git init` the workdir if it isn't a repo already (per SKILL.md's *Setup: git-tracked workdir* discipline).
   - Commit `PLAN.md` + the `astra.yaml` skeleton + `targets/targets.md` + `CLAUDE.md` + the full `work/reference/` substrate as **the first commit** — the complete ORIENT deliverable in one commit.
   - The `work/reference/code/` clone can be `.gitignore`d for large monorepos; `code-index.md` is what the workflow actually consults, and the clone is reproducible from `code-status.yaml`'s URL.
   - **Launch the workflow:**
     ```js
     Workflow({
       scriptPath: '.claude/skills/lc-from-paper/reproduce_workflow.js',
       args: { workdir: '.', intent: '<the fidelity-intent prose from Stage 3>' }
     })
     ```

The workflow runs in the background and notifies on completion. Tell the user you'll be ready for the CLOSE-OUT bookend when it returns — its return value (verify verdict, `report.html` path, open questions) is the input to close-out.

---

## Discipline

- **Fidelity intent is the stopping criterion.** It is the spine of the whole autonomy model: the user says how hard to push, so the middle doesn't need them. Pin it concretely against the paper's actual headline numbers; carry it into `PLAN.md` and `args.intent` unchanged.
- **No `AskUserQuestion` before paper-extraction has run.** Stage 1 collects the identifier in prose; everything else waits until Stage 3, after the paper is on disk and questions can name actual content.
- **The paper-identifier question is prose.** It's the one question that doesn't fit `AskUserQuestion`'s multiple-choice shape.
- **Three to six `AskUserQuestion` rounds total** — scope, fidelity, code repo, conventions, familiarity, external context, plus any Stage 4 follow-up. Batch independent ones into a single multi-question call.
- **One commit at the launch gate, with everything.** `PLAN.md` + skeleton + targets + `CLAUDE.md` + substrate go in together. No intermediate "paper landed but unapproved" commits.
- **Defaults are the path.** When the user says "you choose," take the defaults — full reproduction, the paper's natural sub-analysis structure. The defaults reflect what the architecture has learned about which seams matter.
- **One paper per workdir.** A single PLAN covers one paper. Two papers → run ORIENT twice, two reproduction directories.
- **No code repo is a valid outcome.** When `code-status.yaml` records `found: false`, the reproduction runs paper-only — methodology lives in the paper's prose, no code-as-canonical adjudication. `CLAUDE.md`'s code-as-canonical Rule self-disables.

---

## When ORIENT gets stuck

Most failure modes resolve into "the user has not yet decided what 'reproduce' means for them." If the conversation is circling, ask one of these directly:

- *"If we ran this and it produced figure 3 plus the headline number in Table 2, would you be done?"* — pins targeted vs full.
- *"Is there a specific decision in the paper you want to vary, or are we trying to match the paper exactly?"* — pins whether the spec needs to span alternatives.
- *"What's the moment you'd call this useful — any number coming out, a specific figure matching in shape, the headline matching within stated uncertainty, or every target lining up?"* — pins fidelity intent (and therefore the fix-loop budget).
- *"Are you trying to verify the paper, build on it, or critique it?"* — shifts where the fidelity bar naturally sits.
- *"Is there anything weird about this paper you want the reproduction to know up front?"* — pins paper-specific conventions.

When these answer cleanly, the plan drafts itself.

---

## Resuming an in-flight ORIENT

If the user walks into a workdir mid-flow, read what's on disk before re-running stages. The artifacts are the resume mechanic — no separate state machine:

- **`PLAN.md` at workdir root, committed** → ORIENT already produced its deliverable. If the workflow hasn't launched (or has exited mid-run), don't re-run ORIENT — re-launch the workflow (it is journal-resumable and idempotent against on-disk state), or run CLOSE-OUT if it returned. `git log --oneline` + `astra validate` + `lc status` answer "where are we."
- **`work/reference/{paper.pdf, source/ or document.md, index.json, astra.yaml}` present, no `PLAN.md`** → the paper substrate from Stage 2 exists but the plan isn't built. `/paper-extraction` is idempotent — re-invoke if anything looks partial; it skips done work. Resume from the earliest incomplete stage.
- **`work/reference/code/` present, or `code-status.yaml` `found: false` + `code-index.md` present** → the code substrate from Stage 4 exists. `/lc-from-code` scan-only skips done work too.

Identify the earliest missing piece and resume from there. ORIENT is done — and the workflow takes over — only once `PLAN.md`, the `astra.yaml` skeleton, `targets/targets.md`, and `CLAUDE.md` are all committed.
