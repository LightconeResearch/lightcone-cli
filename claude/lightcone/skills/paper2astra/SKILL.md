---
name: paper2astra
description: >
  Reproduce a published scientific paper in ASTRA. Interview the user
  about the paper and the intended scope, draft a per-paper reproduction
  constitution, then launch a ralph loop that drives the multi-session
  reproduction work. Composes sibling skills for each phase: managing-
  bibliography for ACQUIRE and narrative for SPECIFY. COMPARE follows the
  original Paper2ASTRA target-ledger structure directly rather than requiring
  sibling comparison skills. Use when the user wants to reproduce a paper,
  has a DOI or arXiv ID and wants to start a reproduction project, or asks
  to "reproduce <paper>", "set up reproduction", "paper2astra",
  "/paper2astra <doi>", or hands you a published paper as a starting point
  for ASTRA work.
---

# paper2astra

Reproduce a published paper in ASTRA. The skill is **interview-first**: a short interactive crafting phase up front that produces both a **per-paper reproduction constitution** and a **per-paper `CLAUDE.md`**. After the interview, paper2astra hands the constitution to a multi-session loop that drives the reproduction. Successive iterations survey the workdir, execute one or two phases, exit cleanly, and re-spawn with fresh context until the constitution is realized.

This is a Claude-Code-native skill. There is no Python orchestrator, no state machine, no resume mechanic — the workdir on disk + git history are the substrate.

A reproduction does not fit in one context window. The loop is, in its simplest form, a way to split one goal across many context windows so each iteration starts uncluttered. That's the substrate, not an aesthetic.

## When to use this skill

- The user has a paper (DOI, arXiv ID, or PDF) and wants to reproduce its analysis
- The user invokes `/paper2astra` (with or without an argument)
- The user is starting a fresh reproduction project under `Reproductions/<collab>/<short-name>/`
- An existing paper-reproduction workdir needs the next phase driven forward (in which case skip the interview, see "Resuming an in-flight reproduction" below)

## The bundle

paper2astra composes the rest of the lightcone-cli paper-reproduction bundle. All siblings live in the same `claude/lightcone/skills/` directory and are available without separate installs:

| Sibling skill | Where it's invoked |
|---|---|
| [`/managing-bibliography`](../managing-bibliography/SKILL.md) | ACQUIRE — arXiv LaTeX source download (primary) and BibTeX caching |
| [`/constitution`](../constitution/SKILL.md) | INTERVIEW — drafting the per-paper reproduction constitution |
| [`/ralph-loops`](../ralph-loops/SKILL.md) | After interview — launches the loop that drives all subsequent phases (when the chosen runtime mode is one of the loop modes) |
| [`/narrative`](../narrative/SKILL.md) | SPECIFY — authoring the `narrative:` and `rationale:` prose in `astra.yaml` |

paper2astra does not re-implement what these skills already do — it tells the agent at each phase to invoke them. The siblings stand alone; they don't know about paper2astra.

After paper2astra completes, the SUMMARIZE_RUN summary recommends two adjacent follow-up skills for the user to invoke directly: [`/check-sentence-by-sentence`](../check-sentence-by-sentence/SKILL.md) audits paper claims against code locations, and [`/figure-comparison`](../figure-comparison/SKILL.md) builds a portable side-by-side HTML report for paper artifacts versus reproduced results. Both are user-invokable rather than orchestrator-spawned — they use `AskUserQuestion` for missing-data prompts and don't run cleanly as silent sub-agents.

## Workflow

### Interview (interactive — once per project)

The interview is the only phase paper2astra runs interactively. Read [`references/interview.md`](references/interview.md) in full before starting.

The interview has six jobs:

1. **Identify the paper** — DOI / arXiv ID / title; whether code is available; whether the user has prior experience with this paper.
2. **Scope the reproduction** — full reproduction vs targeted (e.g. only the BAO fit), which figures/tables/numbers are the targets.
3. **Pick a runtime mode** — interactive / bash-loop / tmux-orchestrated. See "Runtime modes" below.
4. **Pick a termination criterion** — frugality (weak) vs rigor (strong). See "Frugality vs rigor" below.
5. **Choose interactive vs sub-agent per phase** — see "Per-phase mode" below. The defaults are reasonable; the user gets to flip any of them.
6. **Draft the per-paper constitution and CLAUDE.md** — invoke `/constitution` to draft the constitution. Author the per-paper `CLAUDE.md` from the same conversation: paper identity, user intent, what's known about the original codebase, runtime-mode choice, frugality-vs-rigor choice, the canonical-resolution rule (see "Code-as-canonical" below), conventions and warnings. The CLAUDE.md is the durable project memory every iteration's Claude session walks up to; the constitution is the runner's spec.

Both files live inside the reproduction's directory. After they are approved the interview ends, and paper2astra launches whichever runtime the user chose.

### Runtime modes

The interview asks the user to pick *how* the loop runs. Three modes, picked from environment + preference:

| Mode | What runs | Right when |
|---|---|---|
| **(1) Interactive** | No autonomous loop. The user prompts through phases by hand from the same Claude session, one or two phases at a time. | Tight control, small paper, or token budget is tight. No new substrate beyond Claude itself. |
| **(2) Bash-loop** | A plain shell loop the user pastes into a terminal (`while …; do claude --dangerously-skip-permissions … ; done`-shaped). No tmux dependency. | Tmux isn't available locally and the connection is stable. Fragile across SSH disconnects unless wrapped in `nohup` — and `nohup` blocks interaction, so for unstable connections this isn't really a fix; mode (3) is. |
| **(3) Tmux-orchestrated** | A loop inside a tmux session paper2astra drives directly via `../ralph-loops/scripts/ralph`. Survives SSH disconnects; the skill sends keystrokes to the tmux pane, monitors, intervenes. | The smoothest path whenever tmux is available. Becomes the de-facto default once `lc launch claude` ships its registry-shipped python-slim agent container with tmux pre-installed. |

The interview probes for tmux availability with `command -v tmux` and only offers mode (3) when present. Mode (3) is preferred when it's available; it isn't required.

### Frugality vs rigor

Independent of mode, the interview asks the user to pick the loop's termination criterion:

- **Weak (frugal):** "run until the checklist of tasks has been completed." Cheaper. Susceptible to one-shot oversights.
- **Strong (rigorous):** "run until you can't find any further contributions, fixes, or improvements that align with the goal." Almost always catches mistakes the one-shot left behind, but burns more tokens.

Strong is the default for fidelity-critical reproductions; weak is the default when the user explicitly wants to cap token spend. The choice goes into the per-paper CLAUDE.md and is honored by every iteration.

### Where this is going

Codex's `/goal` ([Simon Willison, 2026-04-30](https://simonwillison.net/2026/Apr/30/codex-goals/)) is the closest existing primitive — same shape, with a configurable token budget, made smooth by Codex's invisible compaction. When Anthropic ships an equivalent, modes (2) and (3) collapse into it. Until then, ralph is the substrate.

### Phases (driven by ralph iterations after the interview)

Inside each ralph iteration, the agent reads the per-paper constitution, surveys the workdir to determine which phase is current (file existence + git log), and runs that phase's reference. Each phase reference is self-contained — read the matching one in full before working:

| Phase | Reference | Outputs |
|---|---|---|
| ACQUIRE | [`references/acquire.md`](references/acquire.md) | `work/reference/{document.md, paper.pdf, code/, code-status.yaml}` |
| PARSE | [`references/parse.md`](references/parse.md) | `work/reference/{figures/, tables/, metadata.json}` |
| SUMMARIZE | [`references/summarize.md`](references/summarize.md) | `work/notes/{methodology.md, cited_papers.yaml, code-analysis.md}` |
| EXTRACT_TARGETS | [`references/extract_targets.md`](references/extract_targets.md) | `targets/targets.md` + reference files |
| LITERATURE | [`references/literature.md`](references/literature.md) | `work/notes/literature.yaml` + per-paper YAMLs |
| SPECIFY | [`references/specify.md`](references/specify.md) | `astra.yaml`, `universes/baseline.yaml`, `implementation-notes.md` |
| REVIEW | [`references/review.md`](references/review.md) | (in-place edits to spec + notes) |
| IMPLEMENT | [`references/implement.md`](references/implement.md) | `scripts/`, `requirements.txt`, recipes in `astra.yaml` |
| RUN | [`references/run.md`](references/run.md) | `results/<universe>/<output>/` |
| COMPARE | [`references/compare.md`](references/compare.md) | `comparison-report.{yaml,md}` |
| SUMMARIZE_RUN | [`references/summarize_run.md`](references/summarize_run.md) | Final write-up; constitution outcome update |

The COMPARE → IMPLEMENT loop iterates until the verdict is `pass` or attempts are exhausted. The constitution carries the attempt budget; the ralph iterations consult it.

### Per-phase mode (interactive vs sub-agent)

A reproduction's most consequential decisions show up at known seams. The interview decides — for this paper — which phases run interactively (in the main loop session, the user can be reached via `AskUserQuestion`) and which delegate to a sub-agent (Task tool with fresh context, no user reach).

Defaults the constitution starts with:

| Phase | Default | Why |
|---|---|---|
| ACQUIRE | user choice | Mostly mechanical; surfacing happens only on download failures. |
| PARSE | user choice | Deterministic Docling / arXiv extraction. |
| SUMMARIZE | sub-agent | Parallel paper + code reading benefits from fresh context per task. |
| EXTRACT_TARGETS | user choice | The selection of replication targets is sometimes obvious, sometimes wants user input. |
| LITERATURE | sub-agent | One sub-agent per cited paper — pure parallel grunt-work. |
| SPECIFY | **interactive** | Material paper-vs-code conflicts surface here; the user must ratify. |
| REVIEW | user choice | Pre-implement sanity check; can be either. |
| IMPLEMENT | user choice | Mostly mechanical, but algorithm choices may want ratification. |
| RUN | user choice | Mechanical, but failures need diagnosis. |
| COMPARE | **interactive** | Verdict (was the reproduction close enough?) is the second mandatory user-ratification seam. |
| SUMMARIZE_RUN | sub-agent | Final report; no decisions remain. The summary recommends `/figure-comparison` and `/check-sentence-by-sentence` as user-invokable follow-ups. |

The constitution records the choice; iterations honor it. Sub-agent phases are spawned via the `Task` tool from inside the main loop session — that gives them fresh context but no user-reach. Interactive phases run inline in the loop session and may pause with `AskUserQuestion` at material seams.

### Code-as-canonical

When the original codebase is available at `work/reference/code/`, **the agent reads relevant code on every iteration when implementing**. Where paper and code disagree, the **code is canonical** for numerics, plotting, and method; the agent continues with the code's behavior and either ratifies (interactive phases) or logs (sub-agent / loop phases) the disagreement so the user resolves at the next interactive seam.

This is the load-bearing fidelity discipline. Without it, iterations drift to "looks right" rather than "matches" — the failure mode the first-paper test surfaced (plot styles off, numerical results off). The per-paper CLAUDE.md restates the rule so every iteration's Claude session walks up to it.

### Interactive seams vs the loop's running report

Interview phases use `AskUserQuestion` directly — the user is at the wheel. Once the loop launches, the human is no longer present per-iteration, so the agent's discipline shifts:

- **Interactive phase** (per the per-phase mode table): ratify decisions with the user via `AskUserQuestion`.
- **Loop / sub-agent phase**: when a question would normally surface to the user (paper-vs-code conflicts, figures whose intent isn't obvious, ambiguities the constitution doesn't resolve), **append it to `<paper-slug>/open-questions.md`** and continue with the best-judgment default. The user reads the report at session boundaries (between iterations, or when checking on the loop) and answers in-place.

This matches what the user actually does: stays in the conversation while the seams are still soft, walks away while the loop grinds, comes back to a list of "things you'd want to know" rather than a paused agent waiting on input. The CLAUDE.md captures that the loop should always pour seams into `open-questions.md` rather than blocking.

### Material conflicts (the SPECIFY seam)

Inside SPECIFY, when paper and code disagree on something material:

- **Material** = a different choice would plausibly change a numeric result the paper reports.
- **Stylistic / cosmetic / pure-tooling differences** are not material — record them in `implementation-notes.md` and move on.
- **Code is canonical** for numerics and method per "Code-as-canonical" above.
- **Interactive SPECIFY**: surface the conflict with `AskUserQuestion`. The user picks which option `universes/baseline.yaml` selects.
- **Sub-agent SPECIFY** (rare; default is interactive): take code as canonical, record the conflict in `open-questions.md`, and preserve both options in `astra.yaml` so the user can flip baseline at the next interactive seam.

Both choices land in `astra.yaml` as decision options. Whichever the user picks becomes the option selected by `universes/baseline.yaml`; the alternative is preserved as a sibling option for future universe runs. See `references/specify.md` for the full SPECIFY discipline.

### Resuming an in-flight reproduction

If the workdir already exists (`work/reference/document.md` is present, `astra.yaml` exists, etc.):

1. **Skip the interview** unless the user explicitly wants to revise scope.
2. Read the per-paper constitution if it exists; if it does not, draft a minimal one from the current workdir state.
3. Launch (or re-attach to) the ralph loop. Each iteration's first move is to survey the workdir and determine the current phase.

Workdir signals (file existence implies the phase has been done):

| Signal | Phase done |
|---|---|
| `work/reference/document.md` | ACQUIRE + PARSE |
| `work/notes/methodology.md` | SUMMARIZE (paper) |
| `work/notes/code-analysis.md` | SUMMARIZE (code) |
| `targets/targets.md` | EXTRACT_TARGETS |
| `work/notes/literature.yaml` | LITERATURE |
| `astra.yaml` valid (`astra validate astra.yaml`) | SPECIFY |
| `implementation-notes.md` | SPECIFY |
| recipes present in `astra.yaml` | IMPLEMENT |
| `results/<universe>/<output>/` | RUN |
| `comparison-report.yaml` | COMPARE |

`git log --oneline` complements this — phase commits are the chronological view.

## Skills (activate before working)

- [`/constitution`](../constitution/SKILL.md) — for the interview's drafting phase
- [`/ralph-loops`](../ralph-loops/SKILL.md) — for the bash-loop and tmux-orchestrated runtime modes
- [`/managing-bibliography`](../managing-bibliography/SKILL.md) — for ACQUIRE
- [`/narrative`](../narrative/SKILL.md) — for SPECIFY

(`/figure-comparison` and `/check-sentence-by-sentence` are recommended in the SUMMARIZE_RUN summary as user-invokable post-completion follow-ups; they are not co-active with the paper2astra workflow.)

## Discipline

- **paper2astra is the workflow story; phase references are the depth.** SKILL.md tells you when to read which reference; the references carry the prompt prose ported from the legacy Paper2ASTRA Python package.
- **Workdir is the state.** No state machine, no resume mechanic — file existence + `git log` + `astra validate` answer "what phase am I on" deterministically. Each iteration's first move is *survey*.
- **Deterministic checks live in scripts.** When the answer is yes/no, call the script — `astra validate`, `git log`, `yq`, `ls`. Don't ask the agent to introspect what a deterministic check would tell you.
- **Use the up-to-date CLI surfaces, not skill-specific wrappers.** When `astra validate` already does the job, call it directly. Specifically: `astra validate <file>`, `astra validate --verify-evidence`, `astra paper add`. Use whatever the current `astra --help` surfaces.
- **arxiv-LaTeX-first acquisition.** When the paper is on arxiv, the source tarball is the substrate; equations, ligatures, captions, tables come through clean. PDF + Docling is a fallback for non-arxiv where there's no better source.
- **The original code goes into `work/reference/code/`** during ACQUIRE when available, and stays there as the canonical reference for every subsequent iteration (see "Code-as-canonical" above).
- **`/figure-comparison` and `/check-sentence-by-sentence` are user-invokable follow-ups, not auto-invoked from inside paper2astra.** Both use `AskUserQuestion` for missing-data prompts and don't run cleanly as silent sub-agents. The SUMMARIZE_RUN summary names them so the user can choose to invoke either after the run completes.
- **No synthetic data.** Unless the paper itself uses synthetic data as its input, every input dataset must be real (downloaded, queried, or fetched from a real archive). The implement phase reference repeats this; treat it as load-bearing.
- **Tmux preferred-when-available, never required.** Modes (1) and (2) work without it.
- **The siblings don't know about paper2astra.** Each SKILL stands on its own.
- **Workdir conventions stay.** The phase references preserve Paper2ASTRA's workdir layout (`work/reference/`, `work/notes/`, `targets/`, `astra.yaml`, `universes/`, `results/`) so workdirs from the legacy Paper2ASTRA package are interoperable with workdirs driven by this skill.

## Anti-patterns

- **Asking the user mid-sub-agent.** Sub-agent phases cannot reach the user. If a material conflict surfaces in a sub-agent phase, take the code's behavior (or paper's, if no code) as canonical, record the conflict in `open-questions.md` and as a `decisions:` block with both options preserved in `astra.yaml`, and let the next interactive phase ratify. Never make the sub-agent pick silently and discard the alternative.
- **Re-implementing what astra already does.** If `astra validate` returns clean, do not write a separate validator. If `astra paper add` caches the PDF, do not write a separate cache.
- **Treating Paper2ASTRA workdir as legacy.** It is not legacy — it is the substrate. The phase references inherit its conventions intentionally.
- **Bundling everything into one iteration.** Each iteration runs one or two phases, then exits. The constitution is realized across many iterations.
