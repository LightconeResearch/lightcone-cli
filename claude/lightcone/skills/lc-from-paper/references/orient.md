# ORIENT — Phase 0

The opening pre-loop phase. Runs in the user's main session, before the ralph loop launches. Its job is to figure out what the user wants to reproduce, stand up the reference substrate (paper + code), and write the per-paper `constitution.md` + `CLAUDE.md` the ralph loop's iterations will walk up to.

One phase, executed in stages so each later decision is grounded in what was acquired earlier. The paper is read before the interview questions land (so questions reference actual figures and claims); the code is scanned before the constitution is drafted (so the constitution's Scope and sub-analysis decomposition lean on the actual pipeline). The user reviews the drafts before anything commits.

ORIENT is the only pre-loop bookend. REVIEW is the post-loop one. Everything else lives inside the ralph loop.

---

## What ORIENT produces

Three things in the reproduction workdir, all committed together at the end:

- **`constitution.md`** — drafted from [`../templates/constitution.md`](../templates/constitution.md). YAML frontmatter `status: active`, then Goal, Fidelity intent, Scope (in / out), Quality bar, Evidence (paper DOI, arXiv ID, code repo URL, where the substrate lives), Open dimensions. The ralph loop's driving document; each iteration reads it on entry. The body sharpens slowly; Open dimensions is updated each iteration as decisions worth user ratification surface. Task-bound — archivable once the reproduction closes.
- **`CLAUDE.md`** — drafted from [`../templates/CLAUDE.md`](../templates/CLAUDE.md). Paper identity at the top (DOI, title, one-line subject), Rules (universal across reproductions; leave the template's defaults), Disagreements log (starts empty; iterations append), Open opportunities (starts empty; iterations append), Pointers (to `constitution.md`, `work/reference/`, etc.). The auto-loading walk-up; every Claude Code session in the workdir picks it up. Durable — stays useful for any follow-on work in this directory once the reproduction lands.
- **`work/reference/` substrate** — paper substrate from `/paper-extraction` (`paper.pdf`, `source/` or `document.md`, `index.json`, `astra.yaml`, `figures/`, `tables/`, `bibliography-source.{bib,bbl}`) + code substrate from `/lc-from-code` scan-only (`code/`, `code-status.yaml`, `code-index.md`) when there's a reference code repo.

There is no separate "constitution skill" invocation — `/ralph`'s Authoring mode (Study → Draft → Refine → Launch) is what you're following here; the constitution authoring discipline + reference materials live there. Pull the discipline mentally; the deliverable is these two markdown files (plus the substrate produced by the inline skill invocations).

After the user approves both drafts, save them, `git init` the workdir if it isn't one already, commit `constitution.md` + `CLAUDE.md` + the full `work/reference/` substrate as the first commit, then launch the ralph loop (per SKILL.md's *Launching the loop* section).

---

## The stages

### Stage 1 — Ask for the paper

Ask the user for the paper identifier in **prose** — not `AskUserQuestion`. The answer is inherently free-form (an arXiv ID, a DOI, or a path to a PDF on disk), and a multiple-choice modal is the wrong shape for it.

Wording is up to you, but cover the three forms cleanly. Something like:

> *"What paper would you like to reproduce? An arXiv ID, a DOI, or a path to a PDF on disk all work — arXiv ID gives the cleanest acquisition because the LaTeX source comes through."*

If the user supplied the identifier on the `/lc-from-paper` invocation, skip the ask. **No `AskUserQuestion` runs before paper-extraction has landed** — anything beyond the identifier is either inferable from the paper or belongs in a later stage where you can ground the question.

### Stage 2 — Run `/paper-extraction` inline; read the substrate

With the paper identifier in hand, invoke the paper-extraction skill directly:

```
/paper-extraction <doi-or-arxiv-id-or-pdf-path>
```

This produces the paper substrate under `work/reference/`. When it returns, the substrate is on disk. **Read it before continuing to Stage 3** so the next questions are grounded:

- **`work/reference/index.json`** — title, abstract, figure/table inventory with captions, section outline, citations with resolved DOIs. The structural surface.
- **The abstract and the conclusions section of the paper** — give you the claimed headline results, with actual numbers.
- **The "Data availability" / "Code availability" sections of the paper** — usually the canonical place for repo URLs and dataset locations. If neither section exists, grep across `work/reference/source/*.tex` (Path A) or `work/reference/document.md` (Path B) for `github.com`, `gitlab`, `zenodo`, `softwarex`, `\url{}` patterns.
- **The acknowledgements section** — sometimes carries software repos, dataset attributions, cluster acknowledgements that hint at the execution environment.

You do *not* need to read the paper end-to-end. The goal is to ground Stage 3's questions — abstract for claims, conclusions for what the paper says it found, data/code availability for substrate hints. Iterations will read the rest as they need it.

If `/paper-extraction` fails or returns partial substrate (network issue, ambiguous arXiv ID, etc.), surface the failure to the user before continuing.

### Stage 3 — Interview the user, grounded in the paper

Now `AskUserQuestion` is the right tool — each remaining question is a constrained choice with structured options, and the user has paper context loaded from your summary or from the substrate they can browse. Ask in whatever order reads naturally; batching related questions in a single `AskUserQuestion` call (up to 4) is fine.

#### Scope

Present the paper's actual primary outputs as a menu:

> *"The paper claims [N] figures + [M] tables + [headline numerical results]. What's in scope for this reproduction?"*
>
> - Full — every primary result the paper reports
> - Targeted — specific figures / tables / numbers (you'll list which)
> - Use the paper's natural primary-result set (default)

When the user picks "targeted," follow up with the list of the paper's figures/tables (from `index.json`) so they can pick the subset directly rather than recalling from memory.

If the paper has sub-analyses with genuinely independent stages (e.g. reconstruction → clustering → BAO fit), ask about decomposition; if the paper is monolithic, one analysis suffices.

These answers go into `constitution.md`'s **Scope** section (in / out) and inform ARCHITECT's structural decomposition.

#### Fidelity intent

A reproduction can land anywhere from a quick "does this even run" sanity check to a full match across every primary and secondary target. The user owns where they want this one to land — but where it *can* land in this stretch depends on the compute, tokens, time, and attention available. The honest meta-conversation is the point: what does the user want out of this first stretch, given what's spendable on it?

Don't ask the abstract "what would you like to get out of this" — too literal, lands as a wish list. Pivot on what's actually being weighed. With the paper's actual headline numbers in hand from the abstract/conclusions, name them in the prompt so the answer can lock onto something concrete:

> *"The paper's headline is `S_8 = 0.795 ± 0.014`. What's the right shape for this stretch — a quick check that the analysis is tractable, getting that one number right within stated uncertainty, or a full match across every primary target? How much compute and wall-clock do you have to spend on it?"*

Offer the prose options as `AskUserQuestion` options the user can pick from or replace via "Other":

- *"Just checking the analysis is tractable — quick sanity that some headline number comes out close. An afternoon."*
- *"The headline matches within stated uncertainty; secondary results can stay rough. Overnight."*
- *"One specific figure / result fully matches; rest stay rough — a day or two."* — follow up: which one?
- *"Every primary and secondary target lining up within stated tolerance; every paper-vs-code conflict adjudicated. No hard deadline."*

Record the answer verbatim or in close paraphrase under **Fidelity intent** in `constitution.md`'s Goal section. Time/compute bounds are part of the intent — the user's spendable budget shapes what "good enough" can mean for this stretch. Each iteration reads the intent when sizing its next move; COMPARE grades opportunities against it.

If the user genuinely doesn't know yet, write that — *"Not sure yet; let's get something running and revisit"* is itself useful intent, and they can sharpen it at any future REVIEW.

#### Code repository

Use what `/paper-extraction` surfaced. If there's a single candidate URL from the data/code availability or acknowledgements section, lead with that confirmation:

> *"The paper's Data availability section points at `https://github.com/...`. Should we clone that as the reference code? Or is there a different/private repo?"*

If paper-extraction found nothing, ask plainly:

> *"I didn't find a code repo URL in the paper. Is there a private / unpublished repo we should clone? Or proceed paper-only?"*

When the user provides a URL, capture it. When the paper has no code repo and the user doesn't supply one, note *"no public code; paper prose is the only methodological anchor"* and skip directly to Stage 6 (no code substrate to acquire). When the code is available, every iteration that touches a sub-analysis reads from `work/reference/code/` and treats code as canonical for numerics + method — this is recorded in `CLAUDE.md`'s Rules.

#### Paper-specific conventions or warnings

Now Claude has read the paper enough to *propose* one-line conventions / warnings rather than asking the user to volunteer cold. Surface candidates from your post-extraction read:

> *"From the paper I noticed: (a) Paper II of a 5-paper series; siblings in prep with no DOI. (b) Uses non-standard convention for X. (c) Four-way catalog comparison drives every figure. Want any of those as iteration-level pointers in `CLAUDE.md`?"*

Let the user toggle the ones to keep, edit them, add more, or skip cleanly if none apply. The selected items land in `CLAUDE.md`'s **Pointers** section as one-line notes — context every iteration sees on entry.

#### Prior familiarity

A single question:

> *"How familiar are you with this paper?"*
>
> - Haven't read it / barely skimmed
> - Skimmed it / general sense of the claims
> - Read carefully / know the methodology
> - Author / worked closely with the authors

This affects how confidently iterations should defer to the user when adjudicating paper-vs-code disagreements, and how heavy first-iteration review should lean.

#### External context

The real probe is: *"is there context outside the paper substrate + codebase that should inform the spec?"* — co-author feedback, sibling-paper drafts (common for papers in a series), internal blinding documentation, decision-history docs, referee responses, a relevant talk or slide deck. The artifact form varies; what matters is whether such context exists and whether you should point ARCHITECT at it.

Ask in those terms:

> *"Beyond the paper and any code repo, is there context an iteration should know about — co-author / referee feedback, internal notes, a sibling paper still in prep, decisions documented elsewhere? If yes, point at the path(s). Otherwise the paper substrate + code are the source of truth."*

Capture paths into `CLAUDE.md`'s **Pointers** section. Don't proactively read them in ORIENT — that's ARCHITECT's job when it scopes the sub-analyses.

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

   The scan-only branch of `/lc-from-code` does the inventory pass and writes to `work/reference/code-index.md`. Its prompt-context surface carries the "stop at scan" contract.

When no public code repo exists, write `code-status.yaml` with `found: false` and skip `/lc-from-code` entirely. The code-as-canonical rule self-disables in that case.

### Stage 5 — Follow-up questions if the code surfaced anything new

If the code-index reveals something the user should weigh in on — an unexpected dependency, a clear pipeline boundary that suggests a sub-analysis decomposition different from the paper's, an unusual container requirement, an explicit data-availability gate not visible in the paper — ask before drafting the constitution.

Usually this is light or skipped entirely. The code-index is the iterations' surface, not the user's; most of what it reveals doesn't need user adjudication at ORIENT. But when something genuinely affects scope or constitution shape, surface it now rather than waiting for an iteration to file an open question.

### Stage 6 — Draft `constitution.md` + `CLAUDE.md`

Open both templates side-by-side:

- [`../templates/constitution.md`](../templates/constitution.md) — fill in the header, Goal (with fidelity intent), Scope (in / out), Quality bar, Evidence (paper DOI, arXiv ID, code repo URL — these are the user-supplied identifiers; the substrate-path bullets in the template stay as boilerplate, naming where each substrate lives on disk), Open dimensions. Leave the YAML frontmatter `status: active` intact. Both paper and code substrate are on disk by now — the constitution can lean on the actual pipeline decomposition, named figures/tables, and concrete file paths.
- [`../templates/CLAUDE.md`](../templates/CLAUDE.md) — fill in the header (paper title + arXiv ID + DOI + one-line subject), any paper-specific Pointers from Stage 3. Leave Rules in the template state (universal across reproductions). Leave the Disagreements log and Open opportunities sections empty — iterations populate them.

### Stage 7 — User review, refine, commit, launch

**Halt here for explicit user approval.** This is the user's only review point before the autonomous loop takes over; treat it as the final author-mode editorial pass. Do not commit or launch the ralph loop until the user explicitly confirms — silence is not approval.

1. **Show the drafts.** Point the user at `constitution.md` and `CLAUDE.md` (file paths plus a brief inline summary of what each carries — Goal / Fidelity intent / Scope / Quality bar / Evidence for the constitution; paper header + Pointers for the CLAUDE.md). The user reads the actual files; don't paste the full bodies inline.

2. **Surface any open questions you have at this gate.** If a paper detail is ambiguous, a scope choice didn't fully resolve in Stages 3–5, a sub-analysis decomposition is uncertain, or a fidelity intent is implicit but not pinned — ask now, in this same exchange, *before* the loop launches. Each ralph iteration runs cold from `constitution.md` + `CLAUDE.md`; an open question held back here is much harder to raise later.

3. **Gate on `AskUserQuestion`.** Offer options like "Looks good — commit and launch", "I want to edit first" (point them at the file paths), "I have feedback" (collect, refine, re-show, gate again). The launch decision waits on this answer.

4. **When the user approves:**
   - `git init` the workdir if it isn't one already (per SKILL.md's *Setup: git-tracked workdir* discipline).
   - Commit `constitution.md` + `CLAUDE.md` + the full `work/reference/` substrate (paper + code, when code present) as the first commit. A single commit captures the full ORIENT deliverable.
   - The `work/reference/code/` clone itself can be `.gitignore`d for large monorepos; `code-index.md` is what downstream iterations actually consult. The clone is reproducible from `code-status.yaml`'s URL.
   - Launch the ralph loop per SKILL.md's *Launching the loop* section.

Tell the user the tmux session name and the attach command, and that you'll be ready for REVIEW close-out when the loop terminates.

---

## Discipline

- **No `AskUserQuestion` before paper-extraction has run.** Stage 1 collects the identifier in prose; everything else waits until Stage 3, after the paper is on disk and you can ground the questions in actual content.
- **The paper-identifier question is prose.** It's the one question that doesn't fit `AskUserQuestion`'s multiple-choice shape; the free-form answer (arXiv ID / DOI / PDF path) belongs in a prose ask.
- **Three to six `AskUserQuestion` rounds total across Stages 3 + 5** — scope, fidelity, code repo, conventions, familiarity, external context, plus any Stage 5 follow-ups. Some can batch into a single multi-question call when they're independent.
- **One commit at the end, with everything.** `constitution.md` + `CLAUDE.md` + paper substrate + code substrate are committed together. No intermediate commits for "paper-extraction landed but the user hasn't approved yet" or "code cloned but constitution not drafted yet."
- **Defaults are the path.** When the user says "you choose," take the defaults — full reproduction, the paper's natural sub-analysis structure if any. The defaults reflect what the architecture has learned about which seams matter.
- **One paper at a time.** A single `constitution.md` + `CLAUDE.md` pair covers one paper. If the user wants two, run ORIENT twice — two reproduction directories, two pairs.
- **No code repo is still a valid ORIENT outcome.** When `code-status.yaml` records `found: false`, iterations operate in paper-only mode — methodology lives in the paper's prose; no code-as-canonical adjudication is needed. CLAUDE.md's code-as-canonical Rule self-disables.

---

## When ORIENT gets stuck

Most failure modes resolve into "the user has not yet decided what 'reproduce' means for them." If the conversation is circling, ask one of these directly:

- *"If we ran this and it produced figure 3 plus the headline number in Table 2, would you be done?"* — pins targeted vs full.
- *"Is there a specific decision in the paper you want to vary, or are we trying to match the paper exactly?"* — pins whether universes need to span alternatives.
- *"What's the moment you'd call this useful — any number coming out, a specific figure matching in shape, the headline matching within stated uncertainty, or every target lining up?"* — pins fidelity intent.
- *"Are you trying to verify the paper, build on it, or critique it?"* — shifts where the fidelity bar naturally sits.
- *"Is there anything weird about this paper you want every iteration to know up front?"* — pins paper-specific conventions.

When these answer cleanly, both files draft themselves.

---

## Survey signals (entry into ORIENT)

If the user is walking into a workdir mid-flow, check what's already on disk before re-running stages:

- `constitution.md` + `CLAUDE.md` at workdir root, committed → ORIENT already produced its files. If the loop didn't launch (or has exited), skip ahead to launching.
- `work/reference/{paper.pdf, source/ or document.md, index.json, astra.yaml}` present → paper substrate from Stage 2 exists. `/paper-extraction` is idempotent — re-invoke if anything looks partial; it skips done work.
- `work/reference/code/` present **or** `code-status.yaml` records `found: false` **and** `code-index.md` present → code substrate from Stage 4 exists.

When all three are committed, ORIENT is done. Otherwise, identify the earliest missing piece and resume from there.
