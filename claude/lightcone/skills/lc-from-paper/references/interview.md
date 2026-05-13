# INTERVIEW — Phase 0

The opening interactive phase. Runs from the user's main session, before the ralph loop launches. Its job is to crystallize what the user actually wants — which paper, what scope, any paper-specific gotchas — and bake that into the per-paper `constitution.md` (the ralph loop's driving document) and `CLAUDE.md` (the auto-loading walk-up with rules and accumulators) the loop's iterations will walk up to.

The interview is short. Three to six `AskUserQuestion` rounds, total. The user does not need to teach you the paper; they need to tell you what they want reproduced.

---

## What the interview produces

Two files at the reproduction workdir root:

- **`constitution.md`** — drafted from [`../templates/constitution.md`](../templates/constitution.md). YAML frontmatter `status: active`, then Goal, Fidelity intent, Scope (in / out), Quality bar, Evidence (paper DOI, arXiv ID, code repo URL, where the substrate lives), Open dimensions. The ralph loop's driving document; each iteration reads it on entry. Sharpens slowly; the user can revise it at any point (including mid-loop — successive iterations re-read it).
- **`CLAUDE.md`** — drafted from [`../templates/CLAUDE.md`](../templates/CLAUDE.md). Paper identity at the top (DOI, title, one-line subject), Rules (universal across reproductions; leave the template's defaults), Rigor accumulator (starts empty; iterations append), Disagreements log (starts empty; iterations append), Pointers (to `constitution.md`, `work/reference/`, etc.). The auto-loading walk-up; every Claude Code session in the workdir picks it up.

There is no separate "constitution skill" invocation — `/ralph`'s Authoring mode (Study → Draft → Refine → Launch) is what you're following here; the constitution authoring discipline + reference materials live there. Pull the discipline mentally; the deliverable is these two markdown files.

After the user approves both drafts, save them, `git init` the workdir if it isn't one already, commit both files as the first commit, then proceed to ACQUIRE in the same session.

---

## The four jobs

### 1. Identify the paper

If the user did not supply a paper identifier on the `/lc-from-paper` invocation, your first action is `AskUserQuestion` asking for the paper along with the following items rather than trying to search for a paper in their directories.

Use `AskUserQuestion` for whatever the user did not supply on `/lc-from-paper` invocation:

- **DOI or arXiv ID.** arXiv ID preferred when available — it unlocks the LaTeX-source acquisition path (see ACQUIRE).
- **Code repo URL** if the user knows it. (If not, ACQUIRE will search.) When code is available, every iteration that touches a sub-analysis reads from `work/reference/code/` and treats code as canonical for numerics + method. This is recorded in CLAUDE.md's Rules.
- **User's prior familiarity.** Has the user reproduced this paper before? Read it recently? Worked with the original authors? Affects how much you'd lean toward heavy in-iteration review on first iterations.
- **Notes file.** If the user has any prior notes (their own writeup, a sketch of which figures matter), capture the path; iterations will read it during ARCHITECT.

### 2. Scope the reproduction

A paper has many figures, tables, numbers. The user usually does not want all of them.

Ask:

- **Full reproduction or targeted?** Full = every primary result the paper reports. Targeted = "I only care about figures 3, 4, 7 and the headline number in Table 2." Targeted is cheaper and produces a tighter `astra.yaml`.
- **Specific decisions of interest.** A paper makes many choices. The user may care most about a few — e.g. "I want the BAO fit to use a different damping prior than the paper." These become first-class decisions in the spec, with the alternative preserved as a sibling option.
- **Sub-analysis structure.** Does the paper have genuinely independent stages (e.g. reconstruction → clustering → BAO fit)? If so, the spec wants sub-analyses; ARCHITECT will mirror that structure as the stub's decomposition. If the paper is monolithic, one analysis suffices.

These answers go into `constitution.md`'s **Scope** section (in / out) and inform ARCHITECT's structural decomposition.

### 3. Fidelity intent

A reproduction can land anywhere from a quick "does this even run" sanity check to a full match across every primary and secondary target. The user owns where they want this one to land. The job here is to **elicit prose intent** — their own words for what "good enough" looks like, captured into `constitution.md`'s Goal section.

Reach for whichever pivot fits the conversation; you usually only need one or two:

- *"What's the moment you'd call this reproduction useful — when any number comes out at all, when a specific figure matches in shape, when the headline number matches within stated uncertainty, or when every primary and secondary target lines up?"*
- *"Is there a specific result you care about more than the rest, where you'd want full fidelity even if the others stay rough?"*
- *"If this took several sessions of iteration to reach high fidelity everywhere, is that the right investment, or would you rather get a working version in a couple of sessions and decide later whether to push further?"*
- *"Are you trying to verify the paper, build on it, or critique it? That shifts where the fidelity bar wants to sit."*

Record the answer verbatim or in close paraphrase under **Fidelity intent** in `constitution.md`'s Goal section. Concrete examples of what good prose intent looks like:

- *"Just checking if the analysis is tractable — quick sanity that some headline number comes out close."*
- *"I care about Figure 3 being right. The rest can stay rough."*
- *"Full fidelity on the BAO fit specifically; the rest can stay rough."*
- *"Every primary and secondary target lining up within stated tolerance, every paper-vs-code conflict adjudicated."*

Each iteration reads this when deciding cheap vs heavy on the next move; COMPARE grades opportunities against it. If the user genuinely doesn't know yet, write that — *"Not sure yet; let's get something running and revisit"* is itself useful intent, and they can sharpen it at any future REVIEW.

### 4. Paper-specific conventions or warnings

Light touch. Ask the user if there's anything they want every iteration to know about this paper up front — a known pitfall, a non-obvious convention, a thing the authors did unusually. These go into `CLAUDE.md`'s **Pointers** section as one-line notes. Skip cleanly if nothing comes to mind; iterations surface their own as they work.

---

## Drafting the two files

Open both templates side-by-side:

- [`../templates/constitution.md`](../templates/constitution.md) — fill in the header, Goal (with fidelity intent), Scope (in / out), Quality bar, Evidence (paper DOI, arXiv ID, code repo URL — these are the user-supplied identifiers; the substrate-path bullets in the template stay as boilerplate, naming where each substrate lives on disk), Open dimensions. Leave the YAML frontmatter `status: active` intact.
- [`../templates/CLAUDE.md`](../templates/CLAUDE.md) — fill in the header (paper title + arXiv ID + DOI + one-line subject), any paper-specific Pointers. Leave Rules in the template state (universal across reproductions). Leave Rigor and Disagreements sections empty — iterations populate them.

Show both drafts to the user, take corrections, refine, save. Then `git init` the workdir if it isn't one already (per SKILL.md's *Setup: git-tracked workdir* discipline) and commit both as the first commit.

After the user approves and the workdir is initialized, run ACQUIRE in your same main session (see [`acquire.md`](acquire.md)). When ACQUIRE completes, commit the substrate and launch the ralph loop (per SKILL.md's *Launching the loop* section). Tell the user the tmux session name and the attach command, and that you'll be ready for REVIEW close-out when the loop terminates.

---

## Discipline

- **The interview is short.** Three to six `AskUserQuestion` rounds, total. If the user is grinding through detail, gently steer back to scope.
- **Two files, both drafted at INTERVIEW.** No deferring — both `constitution.md` and `CLAUDE.md` are committed before ACQUIRE runs and before the loop launches.
- **Defaults are the path.** When the user says "you choose," take the defaults — full reproduction, the paper's natural sub-analysis structure if any. The defaults reflect what the architecture has learned about which seams matter.
- **One paper at a time.** A single `constitution.md` + `CLAUDE.md` pair covers one paper. If the user wants two, run the interview twice — two reproduction directories, two pairs.

---

## When the interview gets stuck

Most failure modes resolve into "the user has not yet decided what 'reproduce' means for them." If the conversation is circling, ask one of these directly:

- *"If we ran this and it produced figure 3 plus the headline number in Table 2, would you be done?"* — pins targeted vs full.
- *"Is there a specific decision in the paper you want to vary, or are we trying to match the paper exactly?"* — pins whether universes need to span alternatives.
- *"What's the moment you'd call this useful — any number coming out, a specific figure matching in shape, the headline matching within stated uncertainty, or every target lining up?"* — pins fidelity intent.
- *"Are you trying to verify the paper, build on it, or critique it?"* — shifts where the fidelity bar naturally sits.
- *"Is there anything weird about this paper you want every iteration to know up front?"* — pins paper-specific conventions.

When these answer cleanly, both files draft themselves.
