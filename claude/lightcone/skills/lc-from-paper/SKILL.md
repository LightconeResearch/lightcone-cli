---
name: lc-from-paper
description: >
  This skill should be used when the user wants to reproduce a published
  scientific paper in ASTRA, has a DOI/arXiv ID/PDF and wants to start or
  resume a reproduction project, asks to "reproduce <paper>", "set up
  reproduction", or "import a paper", or hands over a published paper as the
  starting point for ASTRA work. It should also be used for existing
  paper-reproduction workdirs when the user asks to continue, resume, drive the
  next phase, or close out the reproduction.
---

# lc-from-paper

Run an interview-first paper reproduction workflow in ASTRA. Use the workdir, the per-paper constitution, `CLAUDE.md`, and git history as the continuity layer across sessions. Survey the workdir at the start of each session, choose the current phase, read that phase's reference in full, and execute one or two phases before stopping at a clean handoff point.

## Phase Workflow

Read [`references/interview.md`](references/interview.md) before starting a fresh reproduction. The interview identifies the paper, scopes the target outputs, chooses runtime and rigor settings, decides which phases should run inline or in sub-agents, drafts the per-paper constitution with [`/constitution`](../constitution/SKILL.md), and writes the per-paper `CLAUDE.md`.

After the interview, drive the reproduction through these phases. Invoke the named sibling skills when the phase reaches their work; they carry the phase-local procedure.

| # | Phase | Reference | Skill composition | Outputs |
|---|---|---|---|---|
| 1 | ACQUIRE | [`references/acquire.md`](references/acquire.md) | Use [`/paper-extraction`](../paper-extraction/SKILL.md). | `work/reference/{source/ \| document.md, paper.pdf, figures/, tables/, metadata.json, code/, code-status.yaml}` |
| 2 | ARCHITECT | [`references/architect.md`](references/architect.md) | Use exploration sub-agents for paper/code indexing when helpful. | stub `astra.yaml`; `work/notes/architect/{paper-index.md, code-index.md}`; `work/notes/cited_papers.yaml` |
| 3 | SPECIFY | [`references/specify.md`](references/specify.md) | Use [`/narrative`](../narrative/SKILL.md). Use [`/lc-from-code`](../lc-from-code/SKILL.md) in augment mode when substantial reference code should add to the current `astra.yaml`. | filled `astra.yaml`; `universes/baseline.yaml`; `targets/targets.md`; `implementation-notes.md` |
| 4 | LITERATURE | [`references/literature.md`](references/literature.md) | Use parallel sub-agents for cited-paper resolution when useful. | `prior_insights:` evidence selectors resolved in `astra.yaml`; cited-paper notes under `work/notes/literature/` |
| 5 | IMPLEMENT | [`references/implement.md`](references/implement.md) | Use implementation and review sub-agents according to the rigor setting. | `scripts/`, `requirements.txt`, executable recipes in `astra.yaml` |
| 6 | RUN | [`references/run.md`](references/run.md) | Run the declared recipes and diagnose failures from command output. | `results/baseline/<output>/` |
| 7 | COMPARE | [`references/compare.md`](references/compare.md) | Compare reproduced artifacts against the paper targets. | `comparison-report.{yaml,md}` |
| 8 | REVIEW | [`references/review.md`](references/review.md) | Use [`/figure-comparison`](../figure-comparison/SKILL.md); optionally use [`/check-sentence-by-sentence`](../check-sentence-by-sentence/SKILL.md). | `REPRODUCTION-SUMMARY.md`, `.lightcone/comparison.html`, resolved `open-questions.md`, finalized constitution outcome |

Iterate COMPARE -> IMPLEMENT -> RUN -> COMPARE until the verdict passes, the attempt budget is exhausted, or the user accepts a partial reproduction. Run REVIEW as the close-out phase after the comparison loop terminates.

## Runtime and Rigor

Offer three runtime modes during the interview:

| Mode | What runs | Right when |
|---|---|---|
| Interactive | The user prompts through phases by hand from the current session. | Tight control, small paper, or token budget is tight. |
| Bash-loop | A plain shell loop runs one session after another. | Tmux is unavailable and the connection is stable. |
| Tmux-orchestrated | [`/ralph-loops`](../ralph-loops/SKILL.md) runs the loop inside a tmux session. | Preferred when tmux is available. |

Set the rigor dial in the constitution:

- **Frugal:** complete the phase checklist with minimal self-review.
- **Rigorous:** run fresh-context review and fix rounds for artifact-producing phases until consecutive rounds find no fixes, or until the constitution's cap is reached.

Thread the rigor setting through ARCHITECT, SPECIFY, LITERATURE, and IMPLEMENT. Review the current artifact against the paper and code from fresh context; incorporate fixes before advancing phases.

## Operating Discipline

- **Workdir survey first.** Determine the current phase from file existence, `git log`, and validation output before acting.
- **ASTRA CLI checks are the authority.** Use `astra validate <file>`, `astra validate --verify-evidence`, `astra paper add`, and the current `astra --help` surfaces for deterministic checks.
- **Acquire from the richest source.** Prefer arXiv source tarballs when available; use PDF + Docling fallback when source is unavailable.
- **Code is canonical when present.** Keep original code under `work/reference/code/`; read relevant code during SPECIFY and IMPLEMENT; model numerics, plotting, and method on the code when paper prose and code disagree.
- **Material disagreements become decisions.** Represent paper-vs-code conflicts as `decisions:` options in `astra.yaml`; select the baseline option according to the user's choice or the code-as-canonical default, and preserve alternatives for later exploration.
- **ARCHITECT sets structure; SPECIFY fills content.** ARCHITECT writes the sub-analysis skeleton, inputs, outputs, and narrative scaffold. SPECIFY fills `decisions:`, `prior_insights:`, `findings:`, and ASTRA anchors.
- **Use real inputs.** Unless the paper itself uses synthetic data as input, fetch or query real datasets during IMPLEMENT.
- **Keep handoffs crisp.** Each session should leave the constitution, `CLAUDE.md`, `open-questions.md`, git history, and phase artifacts clear enough for the next session to resume.

## Resuming

If the workdir already exists, read the per-paper constitution and `CLAUDE.md`, survey the files below, and continue from the first incomplete phase. Draft a minimal constitution from current state when one is missing.

| Signal | Phase done |
|---|---|
| `work/reference/source/` or `work/reference/document.md` | ACQUIRE |
| `work/reference/code/` | ACQUIRE code clone |
| `work/notes/architect/{paper-index.md,code-index.md}` | ARCHITECT indexing |
| `astra.yaml` validates with empty `decisions:` / `prior_insights:` / `findings:` blocks | ARCHITECT stub |
| `work/notes/cited_papers.yaml` | ARCHITECT citation extraction |
| `astra.yaml` has non-empty `decisions:` and `findings:` per sub-analysis, citation-placeholder `prior_insights:`, `targets/targets.md`, and `implementation-notes.md` | SPECIFY |
| `prior_insights:` entries have resolved `evidence:` selectors verified by `astra validate --verify-evidence`; `work/notes/literature/<doi-slug>.yaml` files exist | LITERATURE |
| recipes exist in `astra.yaml` | IMPLEMENT |
| `results/baseline/<output>/` | RUN |
| `comparison-report.yaml` | COMPARE |
| `REPRODUCTION-SUMMARY.md` and `.lightcone/comparison.html` | REVIEW |
