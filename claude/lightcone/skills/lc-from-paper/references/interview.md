# Interview — Phase 0

The opening interactive phase. Run from the orchestrator session, before any sub-agent is spawned. Its job is to crystallize what the user actually wants — which paper, what scope, any paper-specific gotchas — and bake that into the per-paper `CLAUDE.md` every sub-agent walks up to.

The interview is short. Three to six `AskUserQuestion` rounds, total. The user does not need to teach you the paper; they need to tell you what they want reproduced.

---

## What the interview produces

A single `<paper-slug>/CLAUDE.md`, drafted from the template at [`../templates/CLAUDE.md`](../templates/CLAUDE.md). It carries:

- **Paper identity** — DOI, arXiv ID, title, authors, one-line subject; where the original code lives.
- **Goal** — what "done" looks like for this reproduction; in-scope and out-of-scope targets.
- **Pointers** — any paper-specific conventions or warnings the user surfaced.

The Rigor and Disagreements sections start empty — sub-agents fill them in as they work. The Rules section is standing discipline (universal across reproductions); leave it as the template provides.

There is no separate constitution, no runtime-mode choice, no global termination criterion. The architecture is fixed (orchestrator + named per-phase sub-agents) and rigor is chosen per spawn — see SKILL.md's *Rigor is continuous, chosen per spawn* discipline.

After the user approves the draft, save it, ensure the workdir is a git repo (`git init` if needed) and commit `CLAUDE.md` as the first commit, then launch the ACQUIRE sub-agent.

---

## The three jobs

### 1. Identify the paper

Use `AskUserQuestion` for whatever the user did not supply on `/lc-from-paper` invocation:

- **DOI or arXiv ID.** arXiv ID preferred when available — it unlocks the LaTeX-source acquisition path (see ACQUIRE).
- **Code repo URL** if the user knows it. (If not, ACQUIRE will search.) When code is available, every implementing sub-agent reads from `work/reference/code/` and treats code as canonical for numerics + method. This is recorded in CLAUDE.md's Rules.
- **User's prior familiarity.** Has the user reproduced this paper before? Read it recently? Worked with the original authors? Affects how much of ARCHITECT / SPECIFY benefits from heavier rigor settings on first spawn.
- **Notes file.** If the user has any prior notes (their own writeup, a sketch of which figures matter), capture the path; ARCHITECT will read it.

### 2. Scope the reproduction

A paper has many figures, tables, numbers. The user usually does not want all of them.

Ask:

- **Full reproduction or targeted?** Full = every primary result the paper reports. Targeted = "I only care about figures 3, 4, 7 and the headline number in Table 2." Targeted is cheaper and produces a tighter `astra.yaml`.
- **Specific decisions of interest.** A paper makes many choices. The user may care most about a few — e.g. "I want the BAO fit to use a different damping prior than the paper." These become first-class decisions in the spec, with the alternative preserved as a sibling option.
- **Sub-analysis structure.** Does the paper have genuinely independent stages (e.g. reconstruction → clustering → BAO fit)? If so, the spec wants sub-analyses; ARCHITECT will mirror that structure as the stub's decomposition. If the paper is monolithic, one analysis suffices.

These answers go into CLAUDE.md's **Goal** section as "in scope" / "out of scope". There is no separate target-extraction phase — what the user names here becomes explicit `outputs:` declared in the stub `astra.yaml` during ARCHITECT, then filled with paper-anchored `findings:` / `decisions:` during SPECIFY.

### 3. Paper-specific conventions or warnings

Light touch. Ask the user if there's anything they want every sub-agent to know about this paper up front — a known pitfall, a non-obvious convention, a thing the authors did unusually. These go into CLAUDE.md's **Pointers** section as one-line notes. Skip cleanly if nothing comes to mind; sub-agents surface their own as they work.

---

## Drafting CLAUDE.md

Open the template at [`../templates/CLAUDE.md`](../templates/CLAUDE.md) and fill in:

- The header (`<paper-slug>`, paper title, arXiv ID, DOI).
- **Paper** — authors, one-line subject, code repo URL.
- **Goal** — what "done" looks like; in-scope and out-of-scope.
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
- *"Is there anything weird about this paper you want every sub-agent to know up front?"* — pins paper-specific conventions.

When these answer cleanly, CLAUDE.md writes itself.
