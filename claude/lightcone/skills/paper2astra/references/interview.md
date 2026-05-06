# Interview — drafting the per-paper reproduction constitution and CLAUDE.md

The interview is the only phase paper2astra runs interactively. It happens once per project, up front, before any loop is launched. Its job is to crystallize what the user actually wants — which paper, what scope, which runtime, which seams want their attention, which they want delegated — and bake that into the artifacts every iteration walks up to.

Use the [`/constitution`](../../constitution/SKILL.md) skill to draft the constitution. The interview's job is to *gather* the inputs both the constitution and the per-paper `CLAUDE.md` need; the constitution skill carries the discipline of writing the constitution.

---

## What the interview produces

The interview produces a **directory for the reproduction** containing two markdown files. They have separate jobs and don't overlap:

- **`<paper-slug>/CLAUDE.md`** — *info and rules.* Paper identity (DOI / arxiv id / authors / one-line subject), where the original code lives (`work/reference/code/`), the canonical-resolution rule (code-as-canonical when `work/reference/code/` exists), the never-block-on-`AskUserQuestion`-mid-sub-agent rule, any paper-specific conventions or warnings, pointers to the constitution and `open-questions.md`. Auto-loaded by Claude Code on every walk-up to this directory. **Evolves over time** — iterations that learn new conventions or surface paper-specific gotchas can add lines so future sessions don't re-derive the same context.
- **`<paper-slug>/<constitution>.md`** — *desired state.* Pointers (not snapshots) for the runner: what "done" looks like, evidence checks, scope fence, the runtime mode the user chose, the termination criterion (weak/strong), the per-phase mode table, and the open-questions section iterations resolve. Read by the runner each iteration as the explicit task.

Both are written at the end of the interview from the same conversation. CLAUDE.md tells you *what kind of place this is*; the constitution tells you *what we're doing here and when we're done*. After they are approved, paper2astra launches whichever runtime the user chose:

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
- **User's prior familiarity.** Has the user reproduced this paper before? Read the paper recently? Worked with the original authors? This affects how much of the ARCHITECT / SPECIFY work needs human ratification.
- **Notes file.** If the user has any prior notes (their own writeup, a sketch of which figures matter), capture the path; ARCHITECT will read it.

### 2. Scope the reproduction

A paper has many figures, tables, and numbers. The user usually does not want all of them.

Ask:

- **Full reproduction or targeted?** Full = every primary result the paper reports. Targeted = "I only care about figures 3, 4, 7 and the headline number in Table 2." Targeted is cheaper and produces a tighter astra.yaml.
- **Specific decisions of interest.** A paper makes many choices. The user may care most about a few — e.g. "I want the BAO fit to use a different damping prior than the paper." These become first-class decisions in the spec, with the alternative preserved as a sibling option.
- **Sub-analysis structure.** Does the paper have genuinely independent stages (e.g. reconstruction → clustering → BAO fit)? If so, the spec wants sub-analyses; ARCHITECT will mirror the structure as the stub's decomposition. If the paper is monolithic, one analysis suffices.

These answers live in the constitution's **Desired State** section. There is no separate target-extraction phase — the targets the user names here become explicit `outputs:` declared in the stub `astra.yaml` during ARCHITECT, then filled with paper-anchored `findings:` / `decisions:` during SPECIFY.

### 3. Pick a runtime mode

Probe for tmux first:

```bash
command -v tmux
```

Offer the modes the environment supports:

- **(1) Interactive** — no autonomous loop; the user prompts through phases by hand from this Claude session. Right when control is tight, the paper is small, or the token budget is constrained.
- **(2) Bash-loop** — a plain shell loop the user pastes into a terminal. No tmux dependency. Right when tmux isn't available *and* the connection is stable. Fragile across SSH disconnects unless wrapped in `nohup`, and `nohup` blocks interaction — so for unstable connections, mode (3) is the answer, not this.
- **(3) Tmux-orchestrated** — paper2astra drives a tmux session directly via `../ralph-loops/scripts/ralph`. Survives SSH disconnects; the skill sends keystrokes to the pane, monitors, intervenes. Preferred when tmux is available.

If tmux isn't installed, only (1) and (2) appear in the question. The chosen mode goes into the per-paper constitution.

### 4. Pick a termination criterion (frugality vs rigor)

Ask:

- **Weak (frugal):** "run until the checklist of tasks has been completed." Cheaper. Susceptible to one-shot oversights. ARCHITECT, SPECIFY, and IMPLEMENT each skip or run their internal self-review pass once.
- **Strong (rigorous):** "run until you can't find any further contributions, fixes, or improvements that align with the goal." Almost always catches mistakes the one-shot left behind, but burns more tokens. ARCHITECT, SPECIFY, and IMPLEMENT each iterate their internal self-review — fresh-context sub-agent per round; fixes incorporated; a *fresh* sub-agent re-reviews; iterate until two consecutive rounds find no fixes (or a 5-round system cap).

Default to strong for fidelity-critical reproductions; weak when the user wants to cap token spend. The choice goes into the per-paper constitution and is read by ARCHITECT, SPECIFY, and IMPLEMENT.

### 5. Choose interactive vs sub-agent per phase

Read the "Per-phase mode" table in `../SKILL.md`. The defaults are reasonable. Walk the user through it briefly:

- **The two bookends are always interactive:** INTERVIEW (now) and REVIEW (close-out). These are the only mandatory user-reach phases — every other phase is the user's call.
- **Phases whose defaults are sub-agent (parallel fresh context fits the work):** ARCHITECT (two parallel Explore sub-agents — paper-side + code-side — feed a synthesis sub-agent that writes the stub `astra.yaml`; rigor-dialed self-review pass after), LITERATURE (one sub-agent per cited paper), IMPLEMENT (recipe-writing parallelized by output where feasible, with rigor-dialed self-review iterations after).
- **Phases whose default is interactive:** SPECIFY (material paper-vs-code conflicts in the code pass want ratification; per-sub-analysis self-review pass is rigor-dialed regardless of mode).
- **Phases the user genuinely chooses:** ACQUIRE, RUN, COMPARE. These can run either way without losing the surface that matters most.

If the user has no opinion, take the defaults. The choice goes into the constitution's **Context** section as a per-phase mode table. Phases marked sub-agent that hit a question they'd normally surface to the user **append the question to `<paper-slug>/open-questions.md`** rather than blocking; the user resolves them in REVIEW (close-out).

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

A complete `astra.yaml` for <paper> at this workdir, with recipes that produce reproduced versions of <list of targets>, validated by `astra validate astra.yaml --verify-evidence`, with `comparison-report.yaml` verdict `pass` against the targets in `targets/targets.md`.

Non-goals: <e.g., reproducing Figure 12's MCMC stack — out of scope because compute too large for available targets>.

## Scope

In: <list — the targeted figures / tables / numbers, the methodological span being reproduced>.
Out: <list — explicit exclusions, fenced from drift>.

## Runtime mode

<(1) interactive | (2) bash-loop | (3) tmux-orchestrated>

## Termination criterion

<weak | strong>

The COMPARE → IMPLEMENT loop iterates until verdict is `pass` or the attempt budget (default 5) is exhausted, with the chosen termination shaping how aggressively iterations self-check.

## Per-phase mode

| # | Phase | Mode |
|---|---|---|
| 0 | INTERVIEW | interactive (always) |
| 1 | ACQUIRE | <per user> |
| 2 | ARCHITECT | sub-agent (two parallel Explore + synthesis; rigor-dialed self-review) |
| 3 | SPECIFY | interactive (two-pass per sub-analysis: paper, code, rigor-dialed self-review) |
| 4 | LITERATURE | sub-agent (rigor-dialed self-review) |
| 5 | IMPLEMENT | sub-agent (rigor-dialed review iterations) |
| 6 | RUN | <per user> |
| 7 | COMPARE | <per user> |
| 8 | REVIEW (close-out) | interactive (always) |

## Evidence

- `ls work/reference/source/ || ls work/reference/document.md` — ACQUIRE done (arxiv-LaTeX tarball or Docling fallback)
- `ls work/reference/code/` — original code present (canonical reference)
- `ls work/notes/architect/paper-index.md && ls work/notes/architect/code-index.md` — ARCHITECT Explore pass done
- `ls astra.yaml && astra validate astra.yaml` (with empty `decisions:`/`prior_insights:`/`findings:` blocks) — ARCHITECT stub written
- `ls work/notes/cited_papers.yaml` — ARCHITECT citation list (used by SPECIFY for marker→DOI mapping; consumed by LITERATURE for placeholder resolution)
- `astra validate astra.yaml` (with non-empty `decisions:` and `findings:` per sub-analysis + `prior_insights:` populated as citation-only placeholders) `&& ls targets/targets.md && ls implementation-notes.md` — SPECIFY done
- `ls work/notes/literature/` (one `<doi-slug>.yaml` per cited DOI) and `astra.yaml`'s `prior_insights:` entries each have a resolved `evidence:` selector — LITERATURE done
- `astra validate astra.yaml --verify-evidence` — evidence quotes match source PDFs (runs after LITERATURE)
- `ls comparison-report.yaml && yq '.verdict' comparison-report.yaml` — most-recent COMPARE verdict
- `ls REPRODUCTION-SUMMARY.md && ls .lightcone/comparison.html` — REVIEW (close-out) done
- `git log --oneline` — chronological view of phase commits

## Open Questions

(empty — populated as the loop runs; questions accrete in `<paper-slug>/open-questions.md`, the running report the user resolves in REVIEW (close-out) before the constitution closes.)
```

Then author the per-paper `<paper-slug>/CLAUDE.md` from the same conversation. The CLAUDE.md is *info and rules*, not desired state — paper identity, where things live, disciplines that always apply. Approximate shape:

```markdown
# <paper-slug>

Reproduction of <paper title> (<arXiv ID>). DOI: <doi>.

## Paper

- Authors: <list>
- One-line subject: <e.g. "BAO scale measurement from DESI DR1">
- Code repo: <url> (cloned to `work/reference/code/` during ACQUIRE)

## Where things live

- Workdir layout follows Paper2ASTRA conventions: `work/reference/`, `work/notes/`, `targets/`, `astra.yaml`, `universes/`, `results/`.
- The constitution (desired state, runtime mode, scope, evidence, per-phase mode) lives at `<constitution>.md` in this directory.
- The during-loop questions log lives at `open-questions.md`. The user reviews it in REVIEW (close-out).

## Rules

- **Code-as-canonical when `work/reference/code/` exists.** Every implementing iteration reads relevant code. Where paper and code disagree, code is canonical for numerics, plotting, and method.
- **Never block on `AskUserQuestion` mid-sub-agent.** When a sub-agent or loop phase would surface a question to the user, append it to `open-questions.md` and continue with the best-judgment default. The user resolves in REVIEW (close-out).
- **arxiv-LaTeX-first acquisition.** PDF + Docling is a fallback for non-arxiv only.
- **`astra validate --verify-evidence`** is the fidelity gate; evidence quotes must match source PDFs.

## Conventions and warnings

- <any paper-specific notes the user surfaced during the interview>
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
