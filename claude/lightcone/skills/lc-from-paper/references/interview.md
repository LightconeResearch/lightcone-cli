# Interview — Phase 0

The opening interactive phase. Run from the orchestrator session, before any sub-agent is spawned. Its job is to crystallize what the user actually wants — which paper, what scope, any paper-specific gotchas — and bake that into the per-paper `CLAUDE.md` every sub-agent walks up to.

The interview is short. Three to six `AskUserQuestion` rounds, total. The user does not need to teach you the paper; they need to tell you what they want reproduced.

---

## What the interview produces

A single `<paper-slug>/CLAUDE.md`, drafted from the template at [`../templates/CLAUDE.md`](../templates/CLAUDE.md). It carries:

- **Paper identity** — DOI, arXiv ID, title, authors, one-line subject; where the original code lives.
- **Goal** — what "done" looks like for this reproduction: in-scope and out-of-scope targets, plus the user's fidelity intent in prose.
- **Pointers** — any paper-specific conventions or warnings the user surfaced.

The Rigor and Disagreements sections start empty — sub-agents fill them in as they work. The Rules section is standing discipline (universal across reproductions); leave it as the template provides.

There is no separate constitution, no runtime-mode choice, no global termination criterion. The architecture is fixed (orchestrator + named per-phase sub-agents) and rigor is a trajectory toward the user's Goal-section intent — see SKILL.md's *Rigor is a trajectory toward the user's intent* discipline.

After the user approves the draft, save it, ensure the workdir is a git repo (`git init` if needed) and commit `CLAUDE.md` as the first commit, then launch the ACQUIRE sub-agent.

---

## The four jobs

### 1. Identify the paper

Use `AskUserQuestion` for whatever the user did not supply on `/lc-from-paper` invocation:

- **DOI or arXiv ID.** arXiv ID preferred when available — it unlocks the LaTeX-source acquisition path (see ACQUIRE).
- **Code repo URL** if the user knows it. (If not, ACQUIRE will search.) When code is available, every implementing sub-agent reads from `work/reference/code/` and treats code as canonical for numerics + method. This is recorded in CLAUDE.md's Rules.
- **User's prior familiarity.** Has the user reproduced this paper before? Read it recently? Worked with the original authors? Affects how much you'd lean toward heavy self-review on first spawns.
- **Notes file.** If the user has any prior notes (their own writeup, a sketch of which figures matter), capture the path; ARCHITECT will read it.

### 2. Scope the reproduction

A paper has many figures, tables, numbers. The user usually does not want all of them.

Ask:

- **Full reproduction or targeted?** Full = every primary result the paper reports. Targeted = "I only care about figures 3, 4, 7 and the headline number in Table 2." Targeted is cheaper and produces a tighter `astra.yaml`.
- **Specific decisions of interest.** A paper makes many choices. The user may care most about a few — e.g. "I want the BAO fit to use a different damping prior than the paper." These become first-class decisions in the spec, with the alternative preserved as a sibling option.
- **Sub-analysis structure.** Does the paper have genuinely independent stages (e.g. reconstruction → clustering → BAO fit)? If so, the spec wants sub-analyses; ARCHITECT will mirror that structure as the stub's decomposition. If the paper is monolithic, one analysis suffices.

These answers go into CLAUDE.md's **Goal** section as "in scope" / "out of scope". There is no separate target-extraction phase — what the user names here becomes explicit `outputs:` declared in the stub `astra.yaml` during ARCHITECT, then filled with paper-anchored `findings:` / `decisions:` during SPECIFY.

### 3. Fidelity intent

A reproduction can land anywhere from a quick "does this even run" sanity check to a full match across every primary and secondary target. The user owns where they want this one to land. The job here is to **elicit prose intent** — their own words for what "good enough" looks like, captured into CLAUDE.md's Goal section alongside scope.

Reach for whichever pivot fits the conversation; you usually only need one or two:

- *"What's the moment you'd call this reproduction useful — when any number comes out at all, when a specific figure matches in shape, when the headline number matches within stated uncertainty, or when every primary and secondary target lines up?"*
- *"Is there a specific result you care about more than the rest, where you'd want full fidelity even if the others stay rough?"*
- *"If this took several sessions of iteration to reach high fidelity everywhere, is that the right investment, or would you rather get a working version in a couple of sessions and decide later whether to push further?"*
- *"Are you trying to verify the paper, build on it, or critique it? That shifts where the fidelity bar wants to sit."*

Record the answer verbatim or in close paraphrase under **Fidelity intent** in CLAUDE.md's Goal section. Concrete examples of what good prose intent looks like:

- *"Just checking if the analysis is tractable — quick sanity that some headline number comes out close."*
- *"I care about Figure 3 being right. The rest can stay rough."*
- *"Full fidelity on the BAO fit specifically; the rest can stay rough."*
- *"Every primary and secondary target lining up within stated tolerance, every paper-vs-code conflict adjudicated."*

The orchestrator reads this on every spawn decision and COMPARE grades opportunities against it. If the user genuinely doesn't know yet, write that — *"Not sure yet; let's get something running and revisit"* is itself useful intent, and they can sharpen it at any future REVIEW.

### 4. Paper-specific conventions or warnings

Light touch. Ask the user if there's anything they want every sub-agent to know about this paper up front — a known pitfall, a non-obvious convention, a thing the authors did unusually. These go into CLAUDE.md's **Pointers** section as one-line notes. Skip cleanly if nothing comes to mind; sub-agents surface their own as they work.

---

## Drafting CLAUDE.md

Open the template at [`../templates/CLAUDE.md`](../templates/CLAUDE.md) and fill in:

- The header (`<paper-slug>`, paper title, arXiv ID, DOI).
- **Paper** — authors, one-line subject, code repo URL.
- **Goal** — what "done" looks like; in-scope and out-of-scope; fidelity intent in the user's words.
- **Pointers** — any paper-specific conventions the user surfaced.

Leave the **Rigor**, **Paper-vs-code disagreements**, and **Rules** sections in their template state. Rigor and Disagreements grow as sub-agents work; Rules are universal.

Show the draft to the user, take corrections, refine, save to `<paper-slug>/CLAUDE.md`. Then `git init` the workdir if it isn't one already (per SKILL.md's *Setup: git-tracked workdir* discipline) and commit `CLAUDE.md` as the first commit.

After the user approves and the workdir is initialized, launch the ACQUIRE sub-agent. Follow SKILL.md's *Spawning a phase sub-agent* for the announcement pattern — the user needs to know the sub-agent has launched and that they can switch into its chat before its first turn finishes.

---

## Discipline

- **The interview is short.** Three to six `AskUserQuestion` rounds, total. If the user is grinding through detail, gently steer back to scope.
- **CLAUDE.md is the only artifact.** No separate scope document, no interview notes, no constitution. Everything goes in CLAUDE.md.
- **Defaults are the path.** When the user says "you choose," take the defaults — full reproduction, the paper's natural sub-analysis structure if any. The defaults reflect what the architecture has learned about which seams matter.
- **One paper at a time.** A single CLAUDE.md covers one paper. If the user wants two, run the interview twice — two reproduction directories, two CLAUDE.mds.

---

## When the interview gets stuck

Most failure modes resolve into "the user has not yet decided what 'reproduce' means for them." If the conversation is circling, ask one of these directly:

- *"If we ran this and it produced figure 3 plus the headline number in Table 2, would you be done?"* — pins targeted vs full.
- *"Is there a specific decision in the paper you want to vary, or are we trying to match the paper exactly?"* — pins whether universes need to span alternatives.
- *"What's the moment you'd call this useful — any number coming out, a specific figure matching in shape, the headline matching within stated uncertainty, or every target lining up?"* — pins fidelity intent.
- *"Are you trying to verify the paper, build on it, or critique it?"* — shifts where the fidelity bar naturally sits.
- *"Is there anything weird about this paper you want every sub-agent to know up front?"* — pins paper-specific conventions.

When these answer cleanly, CLAUDE.md writes itself.
