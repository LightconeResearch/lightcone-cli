# Interview — drafting the per-paper reproduction constitution and CLAUDE.md

The interview is the only phase paper2astra runs interactively. It happens once per project, up front, before any loop is launched. Its job is to crystallize what the user actually wants — which paper, what scope, which runtime, which seams want their attention, which they want delegated — and bake that into the artifacts every iteration walks up to.

Use the [`/constitution`](../../constitution/SKILL.md) skill to draft the constitution. The interview's job is to *gather* the inputs both the constitution and the per-paper `CLAUDE.md` need; the constitution skill carries the discipline of writing the constitution.

---

## What the interview produces

The interview produces a **directory for the reproduction** containing two markdown files:

- **`<paper-slug>/CLAUDE.md`** — the per-reproduction project memory. Captures everything that's useful across phases: the paper's identity (DOI / arxiv id / authors / one-line subject), the user's stated intent and constraints, what's known about the original codebase, runtime-mode choice, frugality-vs-rigor choice, the canonical-resolution rule (code-as-canonical when `work/reference/code/` exists), any user-supplied conventions or warnings. Every Claude session in this directory finds it on walk-up; iterations don't re-derive context.
- **`<paper-slug>/<constitution>.md`** — the per-paper constitution. Pointers (not snapshots) for the runner: desired state, evidence checks, scope fence, per-phase mode table. The runner re-reads it each iteration.

Both are written at the end of the interview from the same conversation; the CLAUDE.md is the durable context, the constitution is the runner's spec. After they are approved, paper2astra launches whichever runtime the user chose:

| Runtime | Launch |
|---|---|
| **(1) Interactive** | No launch. The user prompts through phases by hand from this Claude session. |
| **(2) Bash-loop** | Show the user the loop snippet to paste into a terminal — `while …; do claude --dangerously-skip-permissions … ; done`-shaped. |
| **(3) Tmux-orchestrated** | `../ralph-loops/scripts/ralph <constitution>.md` — paper2astra drives the tmux session directly. |

There is no separate "interview state" file. Everything lives in the two artifacts and the workdir.

---

## The six jobs

### 1. Identify the paper

Use `AskUserQuestion` if the user did not supply enough on `/paper2astra` invocation:

- **DOI or arXiv ID.** arXiv ID preferred when available — it unlocks the LaTeX-source acquisition path (see ACQUIRE).
- **Code repo URL** if the user knows it. (If not, ACQUIRE will search.) **If code is available, every implementing iteration will read from `work/reference/code/`** and treat code as canonical for numerics + method (the canonical-resolution rule, recorded in CLAUDE.md).
- **User's prior familiarity.** Has the user reproduced this paper before? Read the paper recently? Worked with the original authors? This affects how much of the SUMMARIZE / EXTRACT_TARGETS work needs human ratification.
- **Notes file.** If the user has any prior notes (their own writeup, a sketch of which figures matter), capture the path; SUMMARIZE will read it.

### 2. Scope the reproduction

A paper has many figures, tables, and numbers. The user usually does not want all of them.

Ask:

- **Full reproduction or targeted?** Full = every primary result the paper reports. Targeted = "I only care about figures 3, 4, 7 and the headline number in Table 2." Targeted is cheaper and produces a tighter astra.yaml.
- **Specific decisions of interest.** A paper makes many choices. The user may care most about a few — e.g. "I want the BAO fit to use a different damping prior than the paper." These become first-class decisions in the spec, with the alternative preserved as a sibling option.
- **Sub-analysis structure.** Does the paper have genuinely independent stages (e.g. reconstruction → clustering → BAO fit)? If so, the spec wants sub-analyses; SPECIFY will mirror the structure. If the paper is monolithic, one analysis suffices.

These answers live in the constitution's **Desired State** section.

### 3. Pick a runtime mode

Probe for tmux first:

```bash
command -v tmux
```

Offer the modes the environment supports:

- **(1) Interactive** — no autonomous loop; the user prompts through phases by hand from this Claude session. Right when control is tight, the paper is small, or the token budget is constrained.
- **(2) Bash-loop** — a plain shell loop the user pastes into a terminal. No tmux dependency. Right when tmux isn't available *and* the connection is stable. Fragile across SSH disconnects unless wrapped in `nohup`, and `nohup` blocks interaction — so for unstable connections, mode (3) is the answer, not this.
- **(3) Tmux-orchestrated** — paper2astra drives a tmux session directly via `../ralph-loops/scripts/ralph`. Survives SSH disconnects; the skill sends keystrokes to the pane, monitors, intervenes. Preferred when tmux is available.

If tmux isn't installed, only (1) and (2) appear in the question. The chosen mode goes into the per-paper CLAUDE.md.

### 4. Pick a termination criterion (frugality vs rigor)

Ask:

- **Weak (frugal):** "run until the checklist of tasks has been completed." Cheaper. Susceptible to one-shot oversights.
- **Strong (rigorous):** "run until you can't find any further contributions, fixes, or improvements that align with the goal." Almost always catches mistakes the one-shot left behind, but burns more tokens.

Default to strong for fidelity-critical reproductions; weak when the user wants to cap token spend. The choice goes into the per-paper CLAUDE.md.

### 5. Choose interactive vs sub-agent per phase

Read the "Per-phase mode" table in `../SKILL.md`. The defaults are reasonable. Walk the user through it briefly:

- **Phases that are always interactive (defaults you should not flip):** SPECIFY, COMPARE. These are the ratification seams; the user has to be reachable.
- **Phases that are always sub-agent (defaults you should not flip):** SUMMARIZE, LITERATURE, SUMMARIZE_RUN. These benefit from parallel fresh-context runs and have no decisions left.
- **Phases the user chooses:** ACQUIRE, PARSE, EXTRACT_TARGETS, REVIEW, IMPLEMENT, RUN. These may want user attention if the paper is unfamiliar or the user has strong opinions about implementation.

If the user has no opinion, take the defaults. The choice goes into the constitution's **Context** section as a per-phase mode table. Phases marked sub-agent that hit a question they'd normally surface to the user **append the question to `<paper-slug>/open-questions.md`** rather than blocking; the user reads the running report at session boundaries.

### 6. Draft the constitution and CLAUDE.md

Invoke `/constitution`. Pass in:

- The paper identity (DOI, arXiv ID, code URL)
- The scope (full vs targeted, sub-analysis structure if known)
- The per-phase mode table
- Any prior context the user has shared

The constitution skill carries the discipline of section voice (pointers, not snapshots; constitution, not plan; constraints with reasons). The constitution it produces will look approximately like:

```markdown
---
status: open
---

# Reproduce <paper title> (<arXiv ID>)

## Desired State

A complete `astra.yaml` for <paper> at this workdir, with recipes that
produce reproduced versions of <list of targets>, validated by
`astra validate astra.yaml --verify-evidence`, with `comparison-report.yaml`
verdict `pass` against the targets in `targets/targets.md`.

Non-goals: <e.g., reproducing Figure 12's MCMC stack — out of scope
because compute too large for available targets>.

## Context

- Paper DOI: <doi>
- arXiv ID: <id>; LaTeX source acquisition path is the primary
- Code repo: <url> (or "to be searched in ACQUIRE")
- Runtime mode: <(1) interactive | (2) bash-loop | (3) tmux-orchestrated>
- Termination: <weak | strong>
- Workdir layout: standard Paper2ASTRA conventions —
  `work/reference/`, `work/notes/`, `targets/`, `astra.yaml`,
  `universes/`, `results/`
- Per-phase mode (the canonical version lives in CLAUDE.md):
  | Phase | Mode |
  |---|---|
  | ACQUIRE | <per user> |
  | PARSE | <per user> |
  | SUMMARIZE | sub-agent |
  | EXTRACT_TARGETS | <per user> |
  | LITERATURE | sub-agent |
  | SPECIFY | interactive |
  | REVIEW | <per user> |
  | IMPLEMENT | <per user> |
  | RUN | <per user> |
  | COMPARE | interactive |
  | SUMMARIZE_RUN | sub-agent |

## Skills

- `/paper2astra` — this skill (the orchestrator)
- `/managing-bibliography` — ACQUIRE
- `/narrative` — SPECIFY

(`/figure-comparison` and `/check-sentence-by-sentence` are user-invokable post-completion follow-ups recommended by SUMMARIZE_RUN; they're not part of the per-phase workflow.)

## Code-as-canonical

When `work/reference/code/` exists, the agent reads relevant
code on every implementing iteration. Where paper and code
disagree, **code is canonical** for numerics, plotting, and
method. Disagreements are logged in
`<paper-slug>/open-questions.md` (sub-agent / loop phases) or
ratified with the user via AskUserQuestion (interactive phases).

## Evidence

- `ls work/reference/document.md` — ACQUIRE + PARSE done
- `ls work/reference/code/` — original code present (canonical reference)
- `ls work/notes/methodology.md` — SUMMARIZE done
- `ls targets/targets.md` — EXTRACT_TARGETS done
- `ls astra.yaml && astra validate astra.yaml` — SPECIFY done and valid
- `astra validate astra.yaml --verify-evidence` — evidence quotes match source PDFs
- `ls comparison-report.yaml && yq '.verdict' comparison-report.yaml` — most-recent COMPARE verdict
- `ls figure-comparison.html` — auto-rendered side-by-side at SUMMARIZE_RUN
- `git log --oneline` — chronological view of phase commits

The COMPARE → IMPLEMENT loop iterates until verdict is `pass` or
attempt budget (default 5) is exhausted.

## Open Questions

(empty — populated as the loop runs; questions accrete in
`<paper-slug>/open-questions.md`, the running report the user
reads at session boundaries.)
```

Then author the per-paper `<paper-slug>/CLAUDE.md` from the same conversation. Approximate shape:

```markdown
# <paper-slug> reproduction

Reproduce <paper title> (<arXiv ID>). DOI: <doi>.

## Identity

- Authors: <list>
- One-line subject: <e.g. "BAO scale measurement from DESI DR1">
- Code repo: <url> (cloned to `work/reference/code/` during ACQUIRE)

## User intent and constraints

<paste the scope summary the user gave during the interview>

## Runtime mode: <1 / 2 / 3>

<one paragraph on what that means for this project>

## Termination criterion: <weak / strong>

<one paragraph on what that means for this project>

## Canonical-resolution rule

When `work/reference/code/` exists, code is canonical for numerics + method.
Every implementing iteration reads relevant code; disagreements between paper
and code go into `open-questions.md` (loop / sub-agent phases) or surface via
AskUserQuestion (interactive phases). The user resolves at the next interactive
seam.

## Per-phase mode

(reproduce the per-phase mode table from the constitution)

## Conventions and warnings

- Workdir layout follows Paper2ASTRA conventions: `work/reference/`,
  `work/notes/`, `targets/`, `astra.yaml`, `universes/`, `results/`.
- `arxiv-LaTeX-first` acquisition; PDF + Docling fallback only when
  the paper isn't on arxiv.
- `astra validate --verify-evidence` is the fidelity gate.
- Open questions accumulate in `open-questions.md`; the user reads
  it between iterations.
- <any user-supplied warnings>
```

Show both drafts, take corrections, refine. When the user is happy:

- Save both files inside the reproduction's directory.
- For mode (3), optionally launch ralph: `../ralph-loops/scripts/ralph <constitution>.md`.
- For mode (2), show the user the bash-loop snippet to paste.
- For mode (1), tell the user the interview is done and they can prompt through phases from this session.

The interview ends here. Subsequent work happens inside iterations (modes 2 and 3) or in the same session (mode 1).

---

## Discipline

- **The interview is short.** Do not turn it into a full paper-summarization session. The user does not need to teach you the paper — they need to tell you what they want reproduced. Three to six `AskUserQuestion` rounds, total. If the user is grinding through detail, gently steer back to scope.
- **The constitution and CLAUDE.md are the work products.** Do not file separate "interview notes" or "scope document" files. Everything goes into one of those two artifacts. CLAUDE.md is durable project memory; constitution is the runner's spec.
- **The defaults are the path.** When the user says "I don't know, you choose," take the defaults — runtime (3) when tmux is available else (2) for stable / (1) for unstable connections; rigor (strong) for fidelity-critical work; the per-phase mode table from `../SKILL.md`. The defaults reflect what the loops have learned about which seams matter.
- **One paper at a time.** A single constitution covers one paper. If the user wants two, run the interview twice — two reproduction directories, two CLAUDE.mds, two constitutions.

---

## When the interview gets stuck

Most failure modes resolve into "the user has not yet decided what 'reproduce' means for them." If the conversation is circling, ask one of these directly:

- *"If we ran this and it produced figure 3 plus the headline number in Table 2, would you be done?"* — pins targeted vs full.
- *"Is there a specific decision in the paper you want to vary, or are we trying to match the paper exactly?"* — pins whether universes need to span alternatives.
- *"Do you want to look at every paper-vs-code conflict, or just the ones I think are material?"* — pins SPECIFY mode.
- *"Do you want a quick run that stops at the checklist, or a thorough one that keeps looking for fixes?"* — pins frugality vs rigor.
- *"Are you running this somewhere with a stable connection, or do you want it to survive disconnects?"* — pins runtime mode (when tmux is available).

When these answer cleanly, the constitution and CLAUDE.md write themselves.
