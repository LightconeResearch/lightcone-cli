---
name: lc-from-paper
description: >
  This skill should be used when the user wants to reproduce a published
  scientific paper in ASTRA — has a DOI, arXiv ID, or PDF — or asks to
  "reproduce <paper>", "set up reproduction", or "import a paper". Also
  use when continuing or resuming an existing reproduction workdir. The
  skill instructs Claude to act as an orchestrator that drives the
  reproduction across phases by spawning named sub-agents per phase, with
  the user able to drop into any sub-agent's chat directly to steer.
---

# lc-from-paper

You are helping the user reproduce a published scientific paper as a complete ASTRA project. This is a long, complex task that won't fit in a single context window — it spans discrete phases: acquire the paper and its code, architect the spec, specify decisions and findings, resolve cited literature, implement, run, compare, review. The complexity is exactly why your role matters. As **orchestrator**, you hold the whole shape for the user — guiding them through the workflow, explaining what's happening, tracking what's been done and what's next, deciding how to delegate. Each sub-agent only ever sees its own slice; you keep the through-line.

The heavy lifting of any phase is done by a sub-agent: you spawn it pointed at the workdir (where its `CLAUDE.md` auto-loads), let it work in its own context window, and read what it returns when it's done. Your own context stays light — you carry user intent forward, watch the workdir, and choose what to spawn next.

**The user can interact with any sub-agent directly.** When you spawn one, it appears as a chat surface the user can switch into (typically at the bottom of the screen). Tell them explicitly: *"I'm launching the X sub-agent now — if you want to interact with it, switch to its chat before its first turn finishes."* While the user stays in that chat, the sub-agent stays active — natural turn-by-turn dialogue, prose questions, the user steering directly. When they switch back to you and the sub-agent goes idle, the surface goes away from their view; the sub-agent stays addressable from your side, and addressing it via SendMessage reopens the surface for the user too. **Sub-agents can be resumed at any time, with full context preserved** — if the user wants to drop into any earlier phase, you pull that phase's sub-agent back and it shows up in their chat exactly where it left off.

**As orchestrator, keep your context lean.** Your job is to coordinate, not to absorb sub-agent outputs or the codebase in detail. The paper itself is the exception worth making — it's among the highest-value text in the workflow, the canonical source the spec is being built against, and worth reading carefully at the start. Your other regular reads are short and load-bearing: the paper-extraction index, `CLAUDE.md`, and what sub-agents return. For everything else, delegate: a quick `grep` or single-file lookup is fine to do directly, but anything more open-ended — cross-cutting search, repeated reads of large content — goes to an Explore sub-agent that reads on your behalf and returns a summary. The failure mode to avoid is the orchestrator quietly turning into "just another iteration" by reading everything itself.

## Setup: git-tracked workdir

The reproduction's directory should be a git repo — if not already, `git init` it locally before spawning the first sub-agent. Every sub-agent commits its work as it goes — small, descriptive commits per significant change. The git log is the chronological trail of the reproduction; `git diff` makes each sub-agent's work auditable from your side without you having to read source files directly. Don't push to a remote unless the user has set one up; local-only is the default.

## The phases

The reproduction runs through nine phases (zero-indexed). Phase 0 (INTERVIEW) and Phase 8 (REVIEW) are the bookends — they happen in your own session because they're short, interactive, and depend on the through-line context only you hold. Phases 1–7 are sub-agent dispatches: you spawn each as a named sub-agent, point it at the matching reference file in `references/`, and let it work in its own context with the per-paper `CLAUDE.md` auto-loading from the workdir.

| # | Phase | Where it runs | Reference | Primary outputs |
|---|---|---|---|---|
| 0 | INTERVIEW | orchestrator session | [`references/interview.md`](references/interview.md) | per-paper `CLAUDE.md` |
| 1 | ACQUIRE | sub-agent | [`references/acquire.md`](references/acquire.md) | `work/reference/{source/, paper.pdf, figures/, tables/, metadata.json, code/, code-status.yaml, index.json}` (index.json's `citations:` block carries each cited paper's `{locations, citation, doi}`) |
| 2 | ARCHITECT | sub-agent | [`references/architect.md`](references/architect.md) | stub `astra.yaml` (sub-analyses, inputs, outputs, narrative); `work/notes/architect/{paper-index.md, code-index.md}` |
| 3 | SPECIFY | sub-agent | [`references/specify.md`](references/specify.md) | filled `astra.yaml` (`decisions:`, `findings:`, `prior_insights:` placeholders, anchored narrative); `targets/targets.md`; `implementation-notes.md`; `universes/baseline.yaml` |
| 4 | LITERATURE | sub-agent | [`references/literature.md`](references/literature.md) | `astra.yaml`'s `prior_insights:` resolved with `evidence:` selectors; per-paper PDFs cached via `astra paper add` |
| 5 | IMPLEMENT | sub-agent | [`references/implement.md`](references/implement.md) | `scripts/`, `requirements.txt`, recipes in `astra.yaml` |
| 6 | RUN | sub-agent | [`references/run.md`](references/run.md) | `results/<universe>/<output>/` |
| 7 | COMPARE | sub-agent | [`references/compare.md`](references/compare.md) | `comparison-report.{yaml,md}` |
| 8 | REVIEW | orchestrator session | [`references/review.md`](references/review.md) | `REPRODUCTION-SUMMARY.md`, `/figure-comparison` HTML, resolved `open-questions.md`, finalized reproduction outcome |

COMPARE produces a verdict plus an opportunity assessment — not just pass / fail, but where the gaps are, how much they likely matter, and how they sit relative to the user's fidelity intent. You and the user decide together whether to spend another IMPLEMENT round now (close a gap that sits below intent) or land the reproduction at its current trajectory and log the gap as an open opportunity in CLAUDE.md's Rigor section. Either way, control eventually passes to REVIEW.

## Spawning a phase sub-agent

When you launch a phase, spawn a named sub-agent in the background with the phase reference as its working spec:

- **Name** the sub-agent after the phase: `architect`, `specify`, `implement`, etc. The name is what the user sees in their chat list. If you re-spawn under the same name, the previous instance becomes addressable only by ID.
- **Prompt** the sub-agent to read its phase reference file (`references/<phase>.md`). The reproduction's `CLAUDE.md` auto-loads from the workdir, so it doesn't need to be passed explicitly. Trust the sub-agent to read what else it needs.
- **Run in background** so the user can switch into the sub-agent's chat without you blocking on it.
- **Announce the spawn to the user** before it starts: *"I'm launching the &lt;phase&gt; sub-agent now — switch to its chat now if you want to interact, otherwise it'll work autonomously and report back."*
- **Note the agent ID** when you spawn it. Names are user-facing — if the user dismisses a sub-agent's surface (escape), the name binding goes away and `SendMessage` by name fails. The agent ID + on-disk transcript persist regardless; `SendMessage` by ID resumes the sub-agent from full context and reopens the surface for the user.

When the sub-agent's turn closes you receive a notification with its full response in the `result` field. Read that, then decide: spawn the next phase, ask the user a clarifying question, or revisit a previous phase.

## Per-paper artifact: CLAUDE.md

The reproduction's directory holds a single `CLAUDE.md` that sub-agents and future orchestrator sessions walk up to automatically. It is the durable spec for the reproduction, drafted during INTERVIEW and evolving over time as iterations learn paper-specific gotchas. The starting shape is in [`templates/CLAUDE.md`](templates/CLAUDE.md). Sections:

- **Paper identity** — DOI, arXiv ID, title, authors, one-line subject; where the original code lives (`work/reference/code/`).
- **Goal** — what the reproduction is aiming for. Desired state, scope (in / out), and the user's **fidelity intent** as prose — their own answer to "when is this good enough." The orchestrator reads the intent on every spawn decision and COMPARE grades opportunities against it. Stays static once approved at INTERVIEW; the user can sharpen the intent at any REVIEW.
- **Rigor** — the reproduction's trajectory toward that intent. *Current state* per output or per phase (e.g. *sketch / baseline / tightened / canonical*); read alongside the Goal's intent to decide cheap vs heavy on the next spawn. *Open opportunities* — what could benefit from more attention, with a sense of leverage and how it sits relative to intent ("Figure 3's systematics treatment is sketch-level; tightening it would change the headline number by ~10% — below intent"). Updated by sub-agents as they work; mined during REVIEW for what's worth coming back for.
- **Disagreements** — paper-vs-code material disagreements logged by sub-agents as they find them. Code is canonical for numerics; both options are preserved as decision options in `astra.yaml`. CLAUDE.md just summarizes them so every walk-up sees them at a glance. Surfaced to the user when they're around.
- **Rules** — the code-as-canonical discipline, the never-block-on-`AskUserQuestion`-mid-sub-agent rule (with `open-questions.md` as the autonomous-mode fallback), arxiv-LaTeX-first acquisition, `astra validate --verify-evidence` as the fidelity gate.
- **Pointers** — to `open-questions.md`, and any paper-specific conventions or warnings the user surfaced during the interview.

Keep it short. Pointers, not snapshots.

## The two bookends

### Interview (Phase 0)

The opening interactive phase. Read [`references/interview.md`](references/interview.md) in full before starting. The interview gathers: (1) the paper (DOI / arXiv ID / code repo URL / prior context), (2) scope (full vs targeted, sub-analysis structure), (3) fidelity intent — the user's prose answer to "when is this good enough," (4) any paper-specific conventions or warnings.

These get drafted into the per-paper `CLAUDE.md` — paper identity, Goal section, Rules, Conventions. The Rigor section starts empty; sub-agents fill it in as they work. Show the user the draft, take corrections, refine, then save.

After the user approves, launch the first sub-agent (typically ACQUIRE).

### Review (Phase 8, close-out)

The closing interactive phase. Drafts `REPRODUCTION-SUMMARY.md`, invokes [`/figure-comparison`](../figure-comparison/SKILL.md) (mandatory) and optionally [`/check-sentence-by-sentence`](../check-sentence-by-sentence/SKILL.md), walks `open-questions.md` with the user, and finalizes the reproduction outcome.

REVIEW runs in the orchestrator session because both `/figure-comparison` and `/check-sentence-by-sentence` use `AskUserQuestion`, which isn't available to sub-agents.

## Disciplines

**Workdir is the state.** No state machine, no resume mechanic — file existence + `git log` + `astra validate` answer "what phase am I on" deterministically. Each phase sub-agent's first move is to survey the workdir on entry; you (orchestrator) survey at startup and after each completion notification.

**Code-as-canonical, with disagreements recorded.** When the original codebase is at `work/reference/code/`, every implementing sub-agent reads relevant code on entry. Where paper and code disagree on something material (a different choice would plausibly change a numeric result the paper reports), **code is canonical** for numerics, plotting, and method — but the disagreement is recorded: as a decision option in `astra.yaml` with both alternatives preserved, and as an entry in CLAUDE.md's *Disagreements* section so it's visible to every sub-agent and to the user. Surface it to the user the next time they're around. Stylistic / cosmetic / pure-tooling differences aren't material — note them in `implementation-notes.md` and move on. Without this discipline, iterations drift to "looks right" rather than "matches" and material disagreements get silently absorbed.

**Rigor is a trajectory toward the user's intent.** A reproduction isn't one-shot — it reaches a baseline, then accumulates as the user comes back. The anchor for the whole trajectory is the user's **fidelity intent**, captured in CLAUDE.md's Goal section at INTERVIEW as prose — their own words for what "good enough" looks like (e.g. *"just checking the analysis is tractable"*, *"Figure 3 must be right; the rest can stay rough"*, *"every primary and secondary target lining up within stated tolerance"*). Your job as orchestrator is to hold that intent and translate it into per-spawn tactical decisions.

When you spawn an artifact-producing sub-agent (ARCHITECT, SPECIFY, LITERATURE, IMPLEMENT), derive how much fresh-context self-review to ask of it from the **gap** between where the artifact currently stands (CLAUDE.md's Rigor *Current state* — *sketch / baseline / tightened / canonical*) and what the Goal's intent says the user cares about. *Cheap:* skip self-review or run one fresh-context pass. *Heavy:* iterate fresh-context review + fix until two consecutive rounds find no fixes (capped at 5 rounds). The reviewing sub-agent never sees prior rounds' fixes — fresh context each round, with the prompt "check the artifact is consistent with the paper and the code." Each spawn that produces an artifact updates CLAUDE.md's Rigor *Current state* so the trajectory stays honest across context windows.

The *sketch / baseline / tightened / canonical* and *cheap / heavy* vocabularies are the orchestrator's internal scaffolding for sizing each spawn. The user's surface is the intent prose; the scaffolding only shows through when they ask how a spawn was sized.

**arxiv-LaTeX-first acquisition.** When the paper is on arxiv, the source tarball is the substrate; equations, ligatures, captions, tables come through clean. PDF + Docling is a fallback for non-arxiv only.

**Use the up-to-date `astra` CLI surfaces.** When `astra validate` already does the job, call it directly. Specifically: `astra validate <file>`, `astra validate --verify-evidence`, `astra paper add`. Use whatever the current `astra --help` surfaces — don't write skill-specific wrappers.

**No synthetic data.** Unless the paper itself uses synthetic data as input, every input dataset must be real (downloaded, queried, or fetched from a real archive). The implement reference repeats this; treat it as load-bearing.

**Open-questions for autonomous mode only.** When the user is reachable (in the sub-agent's chat or in your orchestrator session), questions are asked directly in prose. The `<paper-slug>/open-questions.md` accumulator is for autonomous mode — when the user has explicitly stepped away. The user resolves accumulated questions in REVIEW before the reproduction closes.

## Resuming an in-flight reproduction

When you walk into a workdir that already has artifacts:

1. **Skip INTERVIEW** unless the user explicitly wants to revise scope.
2. CLAUDE.md auto-loads from the workdir — that's the spec.
3. Survey the workdir to determine the current phase (table below).
4. Spawn the appropriate next sub-agent.

Workdir signals — file existence implies the phase has been done:

| Signal | Phase done |
|---|---|
| `work/reference/source/` (arxiv tarball) **or** `work/reference/document.md` (Docling fallback) | ACQUIRE |
| `work/reference/code/` | ACQUIRE (code clone) |
| `work/notes/architect/{paper-index.md,code-index.md}` | ARCHITECT (Explore pass) |
| `astra.yaml` validates with empty `decisions:` / `prior_insights:` / `findings:` blocks | ARCHITECT (stub) |
| `astra.yaml` non-empty `decisions:` and `findings:` per sub-analysis + `prior_insights:` placeholders + `targets/targets.md` + `implementation-notes.md` | SPECIFY |
| `astra.yaml`'s `prior_insights:` resolved with `evidence:` selectors; `work/notes/literature/<doi-slug>.yaml` files present | LITERATURE |
| recipes present in `astra.yaml` | IMPLEMENT |
| `results/<universe>/<output>/` | RUN |
| `comparison-report.yaml` | COMPARE |
| `REPRODUCTION-SUMMARY.md` + `.lightcone/comparison.html` + resolved `open-questions.md` | REVIEW |

`git log --oneline` complements this — phase commits are the chronological view.

## Anti-patterns

- **Reading content the orchestrator doesn't need.** If the answer fits in a sub-agent's return, don't re-read the source yourself. Dispatch Explore for open-ended search.
- **Doing phase work in the orchestrator session.** The orchestrator spawns and routes; phase work happens in sub-agents. Exception: INTERVIEW and REVIEW (the bookends).
- **Asking a sub-agent to use `AskUserQuestion`.** Sub-agents don't have it. They ask in prose, or surface the question to you so you call `AskUserQuestion` from the orchestrator session.
- **Re-implementing what `astra` already does.** If `astra validate` returns clean, don't write a separate validator. If `astra paper add` caches the PDF, don't write a separate cache.
- **Bundling phases into one sub-agent.** Each sub-agent runs one phase. The granularity is what keeps each context window manageable; conflating phases re-creates the failure mode this architecture exists to avoid.
- **Forgetting to announce the spawn to the user.** They need to know a sub-agent has launched and that they can switch into its chat before it finishes its first turn. Without the announcement, the surface comes and goes invisibly.
