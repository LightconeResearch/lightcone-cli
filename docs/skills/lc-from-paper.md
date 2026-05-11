# /lc-from-paper

Reproduce a published scientific paper as a complete ASTRA project. The
skill is an **orchestrator**: it opens with an interactive interview,
drafts a per-paper `CLAUDE.md`, then runs as a persistent session that
spawns named per-phase sub-agents the user can drop into directly.

`/lc-from-paper` is the entry point of the paper-reproduction bundle.
The four sibling skills ([`paper-extraction`](paper-extraction.md),
[`narrative`](narrative.md), [`figure-comparison`](figure-comparison.md),
[`check-sentence-by-sentence`](check-sentence-by-sentence.md)) are
co-located in the same plugin and invoked by role across the phases.

Source: [`claude/lightcone/skills/lc-from-paper/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-from-paper/SKILL.md).

## Architecture

The orchestrator never absorbs paper or code content directly — it
spawns sub-agents and reads what they return. Each sub-agent gets its
own context window, runs one phase, commits its work to git, and exits.
The orchestrator holds the through-line: user intent, what's been done,
what's next, how rigorously to spawn the next phase.

Two persistent sub-agents — `paper-expert` and `code-expert` — are
spawned during ACQUIRE and stay alive for the rest of the reproduction.
Later phases query them via `SendMessage` instead of re-ingesting
materials.

**The user can interact with any sub-agent directly.** When the
orchestrator spawns one, it appears as a chat surface (typically at the
bottom of the screen). The user switches in for turn-by-turn dialogue,
switches back out, and the sub-agent stays addressable.

## Phases

Nine phases, zero-indexed. Phases 0, 1, and 8 run in the orchestrator
session; phases 2–7 are sub-agent dispatches.

| # | Phase | Where | Primary outputs |
|---|-------|-------|------------------|
| 0 | INTERVIEW | orchestrator | per-paper `CLAUDE.md` |
| 1 | ACQUIRE | orchestrator | `work/reference/{paper.pdf, source/ or document.md, figures/, tables/, index.json, astra.yaml, code/, code-index.md}`; `paper-expert` and `code-expert` sub-agents |
| 2 | ARCHITECT | sub-agent | stub `astra.yaml` (sub-analyses, inputs, outputs, narrative) |
| 3 | SPECIFY | sub-agent | filled `astra.yaml` (`decisions:`, `findings:`, `prior_insights:` placeholders, anchored narrative); `targets/targets.md`; `universes/baseline.yaml` |
| 4 | LITERATURE | sub-agent | `prior_insights:` resolved with `evidence:` selectors; per-paper PDFs cached via `astra paper add` |
| 5 | IMPLEMENT | sub-agent | `scripts/`, `requirements.txt`, recipes in `astra.yaml` |
| 6 | RUN | sub-agent | `results/<universe>/<output>/` |
| 7 | COMPARE | sub-agent | `comparison-report.{yaml,md}` plus an opportunity assessment graded against the user's fidelity intent |
| 8 | REVIEW | orchestrator | `REPRODUCTION-SUMMARY.md`, `/figure-comparison` HTML, resolved `open-questions.md`, finalized reproduction outcome |

ACQUIRE runs in the orchestrator session because its work is two
parallel sub-skill invocations (`/paper-extraction` and `/lc-from-code`
in scan-only mode) plus capturing the resulting persistent sub-agents.
INTERVIEW and REVIEW run there because both are interactive bookends.

## Per-paper `CLAUDE.md`

Drafted during INTERVIEW. The reproduction workdir holds a single
`CLAUDE.md` that sub-agents and future orchestrator sessions walk up to
automatically. Sections:

- **Paper identity** — DOI, arXiv ID, title, authors, one-line subject;
  where the original code lives.
- **Goal** — the user's **fidelity intent** as prose: their own answer
  to "when is this good enough." Read on every spawn decision.
- **Rigor** — *Current state* per output or phase (*sketch / baseline /
  tightened / canonical*) plus *open opportunities*. Updated by
  sub-agents as they work.
- **Disagreements** — paper-vs-code disagreements logged as found.
  Code is canonical for numerics; both options are preserved as
  decision options in `astra.yaml`.
- **Rules** — code-as-canonical, never-block-on-`AskUserQuestion`-
  mid-sub-agent, arxiv-LaTeX-first acquisition, `astra validate
  --verify-evidence` as the fidelity gate.

Pointers, not snapshots.

## Disciplines

- **Workdir is the state.** File existence + `git log` + `astra
  validate` answer "what phase am I on" deterministically. No separate
  state machine.
- **Code-as-canonical, with disagreements recorded.** Where paper and
  code disagree on something material, code wins for numerics but the
  disagreement is preserved as a decision option and noted in
  CLAUDE.md.
- **Rigor is a trajectory toward the user's intent.** Sub-agent
  fresh-context self-review is sized per spawn from the gap between
  *Current state* and the Goal's fidelity intent — cheap (skip or one
  pass) vs heavy (iterate until two consecutive clean rounds, cap 5).
- **arxiv-LaTeX-first acquisition.** PDF + Docling is the non-arxiv
  fallback only.
- **No synthetic data.** Unless the paper itself uses synthetic data,
  every input must be real.

## Anti-patterns

- Reading content the orchestrator doesn't need. If the answer fits in
  a sub-agent's return, don't re-read the source.
- Doing phase work in the orchestrator session. Exceptions are
  INTERVIEW, ACQUIRE, and REVIEW.
- Asking a sub-agent to use `AskUserQuestion` — they don't have it.
- Re-implementing what `astra` already does (`astra validate`, `astra
  paper add`).
- Forgetting to announce the spawn — the user needs to know a sub-agent
  has launched and that they can switch into its chat.

## Related

- [Bundle README](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/README.md)
  — why the bundle is co-located rather than a separate plugin install.
- [`/paper-extraction`](paper-extraction.md) — ACQUIRE's primary
  acquisition path.
- [`/narrative`](narrative.md) — SPECIFY's prose authoring.
- [`/figure-comparison`](figure-comparison.md) — REVIEW (mandatory) and
  also user-invokable.
- [`/check-sentence-by-sentence`](check-sentence-by-sentence.md) —
  REVIEW (opt-in) and also user-invokable.
