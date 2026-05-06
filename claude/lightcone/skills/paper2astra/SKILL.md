---
name: paper2astra
description: >
  Reproduce a published scientific paper in ASTRA. Interview the user
  about the paper and the intended scope, draft a per-paper reproduction
  constitution, then launch a ralph loop that drives the multi-session
  reproduction work. The loop is 9 phases bookended by two always-interactive
  seams (INTERVIEW at start, REVIEW at close-out); ARCHITECT writes a stub
  astra.yaml decomposition before SPECIFY's two-pass-per-sub-analysis fills
  it in. Composes sibling skills for each phase: managing-bibliography for
  ACQUIRE and narrative for SPECIFY. Use when the user wants to reproduce
  a paper, has a DOI or arXiv ID and wants to start a reproduction project,
  or asks to "reproduce <paper>", "set up reproduction", "paper2astra",
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

Two further siblings are invoked from **REVIEW** (the close-out), the always-interactive phase that runs after the COMPARE → IMPLEMENT loop terminates: [`/figure-comparison`](../figure-comparison/SKILL.md) builds a portable side-by-side HTML report (paper artifacts vs reproduced), and [`/check-sentence-by-sentence`](../check-sentence-by-sentence/SKILL.md) (optional) audits paper claims against code locations. Both have `AskUserQuestion` in their `allowed-tools`, so REVIEW runs interactively in the main loop session — spawning them under the `Task` tool would fire prompts into nothing.

The phase name **REVIEW** is the close-out (replacing what was briefly called SUMMARIZE_RUN); the rigor-dialed self-review pass that previously lived in a pre-implement REVIEW phase folded into ARCHITECT, SPECIFY, and IMPLEMENT as their internal cross-check. Same word, different jobs — the close-out is named by phase boundary, the self-reviews are named by their host phase.

## Workflow

### Interview (interactive — once per project)

The interview is the first of two always-interactive bookends — INTERVIEW at the start, REVIEW at the close-out. Every phase between them is configurable per the user's per-phase mode choice. Read [`references/interview.md`](references/interview.md) in full before starting.

The interview has six jobs:

1. **Identify the paper** — DOI / arXiv ID / title; whether code is available; whether the user has prior experience with this paper.
2. **Scope the reproduction** — full reproduction vs targeted (e.g. only the BAO fit), which figures/tables/numbers are the targets. The user's named targets get declared as `outputs:` in the stub `astra.yaml` during ARCHITECT and filled with evidence-backed `findings:` / `decisions:` during SPECIFY — there is no separate target-extraction phase.
3. **Pick a runtime mode** — interactive / bash-loop / tmux-orchestrated. See "Runtime modes" below.
4. **Pick a termination criterion** — frugality (weak) vs rigor (strong). The dial threads through ARCHITECT, SPECIFY, and IMPLEMENT, scaling each phase's internal self-review depth. See "Frugality vs rigor" below.
5. **Choose interactive vs sub-agent per phase** — see "Per-phase mode" below. Only INTERVIEW and REVIEW (close-out) are mandatory-interactive; every other phase is the user's call.
6. **Draft the per-paper constitution and CLAUDE.md** — invoke `/constitution` to draft the constitution. Author the per-paper `CLAUDE.md` from the same conversation. The two files have separate jobs and don't overlap:

   - **`CLAUDE.md`** is *info and rules* — paper identity (DOI / arXiv ID / title / authors), where the original code lives (`work/reference/code/`), the code-as-canonical rule, the never-block-on-`AskUserQuestion`-mid-sub-agent rule, any paper-specific conventions or warnings, pointers to the constitution and `open-questions.md`. Auto-loaded by Claude Code on every walk-up to this directory. **Evolves over time** — iterations that learn new conventions or surface paper-specific gotchas can add lines so future sessions don't re-derive the same context.
   - **The constitution** is *desired state* — what "done" looks like, evidence checks, scope fence, the runtime mode the user chose, the termination criterion (weak/strong), per-phase routing (interactive vs sub-agent), and the open-questions section iterations resolve. Read by the runner each iteration as the explicit task.

   CLAUDE.md tells you *what kind of place this is*; the constitution tells you *what we're doing here and when we're done*.

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

Strong is the default for fidelity-critical reproductions; weak is the default when the user explicitly wants to cap token spend. The choice goes into the per-paper constitution (alongside the runtime-mode choice) and is honored by every iteration.

### Phases (driven by ralph iterations after the interview)

Inside each ralph iteration, the agent reads the per-paper constitution, surveys the workdir to determine which phase is current (file existence + git log), and runs that phase's reference. Each phase reference is self-contained — read the matching one in full before working:

| # | Phase | Reference | Outputs |
|---|---|---|---|
| 1 | ACQUIRE | [`references/acquire.md`](references/acquire.md) | `work/reference/{source/ \| document.md, paper.pdf, figures/, tables/, metadata.json, code/, code-status.yaml}` |
| 2 | ARCHITECT | [`references/architect.md`](references/architect.md) | stub `astra.yaml` (sub-analyses, inputs, outputs, narrative — no anchors yet); `work/notes/architect/{paper-index.md, code-index.md}`; `work/notes/cited_papers.yaml`; rigor-dialed self-review |
| 3 | LITERATURE | [`references/literature.md`](references/literature.md) | `work/notes/literature.yaml` + per-paper YAMLs |
| 4 | SPECIFY | [`references/specify.md`](references/specify.md) | filled `astra.yaml` (decisions, prior_insights, findings, anchored narrative); `universes/baseline.yaml`; `implementation-notes.md`; `targets/targets.md`; per-sub-analysis rigor-dialed self-review |
| 5 | IMPLEMENT | [`references/implement.md`](references/implement.md) | `scripts/`, `requirements.txt`, recipes in `astra.yaml`; rigor-dialed paper-vs-implementation review iterations |
| 6 | RUN | [`references/run.md`](references/run.md) | `results/<universe>/<output>/` |
| 7 | COMPARE | [`references/compare.md`](references/compare.md) | `comparison-report.{yaml,md}` |
| 8 | REVIEW (close-out) | [`references/review.md`](references/review.md) | `REPRODUCTION-SUMMARY.md`, `/figure-comparison` HTML, (optional) sentence audit, resolved `open-questions.md`, finalized constitution outcome |

The COMPARE → IMPLEMENT loop iterates until the verdict is `pass` or attempts are exhausted. The constitution carries the attempt budget; the ralph iterations consult it. On pass (or user-accepted partial), control returns to the user and REVIEW runs interactively in the main session — drafting the report, invoking `/figure-comparison`, optionally `/check-sentence-by-sentence`, walking accumulated questions, and finalizing the constitution outcome.

ACQUIRE folds in what was previously a separate PARSE phase: arxiv-LaTeX papers come pre-structured in their tarball (no Docling needed), and PDF-fallback papers run Docling inside ACQUIRE itself to produce `document.md` + extracted figures/tables. ARCHITECT replaces the old STUDY: instead of writing per-section paper-vs-code agreement-check files in markdown that SPECIFY would re-author into YAML, ARCHITECT writes the structural skeleton of `astra.yaml` directly (sub-analyses, inputs, outputs, narrative prose). SPECIFY then fills it in with `decisions:` / `prior_insights:` / `findings:` and `astra-anchor:` references — same content the old STUDY produced, but authored once in YAML rather than twice (markdown then YAML). The pre-implement REVIEW phase folded into ARCHITECT, SPECIFY, and IMPLEMENT as a rigor-dialed self-review discipline at every artifact-producing seam, freeing the REVIEW *name* for the close-out (replacing SUMMARIZE_RUN, whose name was a verb stuck describing one piece of what the close-out actually does).

### Per-phase mode (interactive vs sub-agent)

A reproduction's most consequential decisions show up at known seams. Only the bookends are mandatory-interactive — INTERVIEW at the start, REVIEW (close-out) at the end. Every phase between them is configurable: the interview decides which run interactively (in the main loop session, the user reachable via `AskUserQuestion`) and which delegate to a sub-agent (Task tool with fresh context, no user reach).

Defaults the constitution starts with:

| # | Phase | Default | Why |
|---|---|---|---|
| 0 | INTERVIEW | **interactive — *always*** | The first bookend. Scope, runtime, rigor, per-phase mode all decided here. |
| 1 | ACQUIRE | user choice | Mostly mechanical (LaTeX-tarball download / Docling fallback / code clone); surfacing happens only on download failures. |
| 2 | ARCHITECT | sub-agent (two parallel Explore + synthesis; rigor-dialed self-review) | Two Task-tool sub-agents fan out (one paper-side, one code-side) and produce indexes; a synthesis sub-agent writes the stub `astra.yaml`. Rigor-dialed fresh-context self-review pass cross-checks the stub before SPECIFY runs. |
| 3 | LITERATURE | sub-agent | One sub-agent per cited paper — pure parallel grunt-work. Core, not opt-in: verifiability against citations is what `prior_insights` evidence depends on. |
| 4 | SPECIFY | user choice (default interactive); two-pass-per-sub-analysis | **Paper pass**: authors decisions / prior_insights / findings with paper-anchored evidence; weaves `astra-anchor:` references into the existing narrative. **Code pass** (when code present): augments / amends with code-as-canonical insights and material-disagreement entries; surfaces material conflicts via `AskUserQuestion` (interactive) or `<paper-slug>/open-questions.md` (sub-agent). **Self-review** (rigor-dialed): fresh-context sub-agent per sub-analysis. Per-sub-analysis parallelism when independent. |
| 5 | IMPLEMENT | sub-agent (rigor-dialed review iterations) | Writes recipes + scripts (parallelized by output where feasible). Frugal: minimal review pass after. Rigor: N rounds of fresh-context "is the implementation consistent with the paper?" review + fix iterations. |
| 6 | RUN | user choice | Mechanical, but failures need diagnosis. |
| 7 | COMPARE | user choice | Verdict (was the reproduction close enough?) is the user's call when interactive; sub-agent COMPARE writes the verdict and lets REVIEW (close-out) ratify. |
| 8 | REVIEW (close-out) | **interactive — *always*** | The closing bookend. Drafts the report, runs `/figure-comparison` (mandatory) and `/check-sentence-by-sentence` (opt-in), walks `open-questions.md` with `AskUserQuestion`, finalizes the constitution outcome. |

The constitution records the choice; iterations honor it. Sub-agent phases are spawned via the `Task` tool from inside the main loop session — that gives them fresh context but no user-reach. Interactive phases run inline in the loop session and may pause with `AskUserQuestion` at material seams.

### Rigor vs frugality threads through ARCHITECT, SPECIFY, and IMPLEMENT

The frugality/rigor dial picked in INTERVIEW is not just a termination criterion for the COMPARE → IMPLEMENT loop. It also tunes how aggressively each artifact-producing phase self-checks. Same shape at every seam:

- **Frugal**: skip self-review, or run one fresh-context sub-agent pass and incorporate fixes once.
- **Rigor**: N rounds of fresh-context sub-agent review + fix. Each round runs a brand-new reviewer that does NOT see prior rounds' findings or fixes. Stop when two consecutive rounds find no fixes (strong-termination), or after 5 rounds (system cap), whichever comes first.

The artifact under review changes per phase — ARCHITECT reviews the stub `astra.yaml`; SPECIFY reviews each sub-analysis's filled spec; IMPLEMENT reviews `scripts/` + recipes against paper + code — but the cross-check shape is constant.

The discipline is **never bias the reviewing sub-agent**: each round runs from fresh context with the prompt "check the artifact is consistent with the paper and the code" — not "here's what was just fixed; check it." Otherwise the reviewer pattern-matches on prior fixes rather than thinking from first principles.

### Code-as-canonical

When the original codebase is available at `work/reference/code/`, **the agent reads relevant code on every iteration when implementing**. Where paper and code disagree, the **code is canonical** for numerics, plotting, and method; the agent continues with the code's behavior and either ratifies (interactive phases) or logs (sub-agent / loop phases) the disagreement so the user resolves at the next interactive seam.

This is the load-bearing fidelity discipline. Without it, iterations drift to "looks right" rather than "matches" — the failure mode the first-paper test surfaced (plot styles off, numerical results off). The per-paper CLAUDE.md restates the rule so every iteration's Claude session walks up to it.

### Two surfaces for user attention: open-questions and REVIEW (close-out)

The reproduction has two periods of human reach — the bookends. INTERVIEW at the start, REVIEW (close-out) at the end. In between, the loop runs without a human in the conversation. The discipline has two surfaces to match:

- **`<paper-slug>/open-questions.md` — the during-loop accumulator.** When a sub-agent or loop iteration would normally surface a question to the user (paper-vs-code conflicts, figures whose intent isn't obvious, ambiguities the constitution doesn't resolve), it appends the question to `open-questions.md` and continues with the best-judgment default. Never block on `AskUserQuestion` from inside a sub-agent — the prompt fires into nothing.

- **REVIEW (close-out) — the post-loop interactive close-out.** When the COMPARE→IMPLEMENT loop terminates (verdict=pass or budget exhausted), control returns to the user. REVIEW invokes `/figure-comparison` and (optionally) `/check-sentence-by-sentence` interactively — these skills can use `AskUserQuestion` because the human is back. Then it walks the user through `open-questions.md` with `AskUserQuestion`, lands resolutions, updates `astra.yaml` or `implementation-notes.md` accordingly, drafts `REPRODUCTION-SUMMARY.md`, and finalizes the constitution outcome.

Stays in the conversation while the seams are still soft, walks away while the loop grinds, comes back to a rich review surface plus a list of "things you'd want to know."

### Material conflicts (the SPECIFY code-pass seam)

SPECIFY's code pass (per sub-analysis) is where paper-vs-code material disagreements surface. The paper pass authors decisions / findings from the paper alone; the code pass cross-checks them against the implementation. When paper and code disagree on something material:

- **Material** = a different choice would plausibly change a numeric result the paper reports.
- **Stylistic / cosmetic / pure-tooling differences** are not material — record them in `implementation-notes.md` and move on.
- **Code is canonical** for numerics and method per "Code-as-canonical" above.
- **Interactive SPECIFY**: surface the conflict with `AskUserQuestion`. The user picks which option `universes/baseline.yaml` selects.
- **Sub-agent SPECIFY** (rare; default is interactive): take code as canonical, record the conflict in `open-questions.md`, and preserve both options in `astra.yaml` so the user can flip baseline at REVIEW (close-out).

Both choices land in `astra.yaml` as decision options. Whichever the user picks becomes the option selected by `universes/baseline.yaml`; the alternative is preserved as a sibling option for future universe runs. See `references/specify.md` for the full SPECIFY discipline.

### Resuming an in-flight reproduction

If the workdir already exists (`work/reference/source/` or `work/reference/document.md` is present, `astra.yaml` exists, etc.):

1. **Skip the interview** unless the user explicitly wants to revise scope.
2. Read the per-paper constitution if it exists; if it does not, draft a minimal one from the current workdir state.
3. Launch (or re-attach to) the ralph loop. Each iteration's first move is to survey the workdir and determine the current phase.

Workdir signals (file existence implies the phase has been done):

| Signal | Phase done |
|---|---|
| `work/reference/source/` (arxiv tarball) **or** `work/reference/document.md` (Docling fallback) | ACQUIRE |
| `work/reference/code/` | ACQUIRE (code clone) |
| `work/notes/architect/{paper-index.md,code-index.md}` | ARCHITECT (Explore pass) |
| `astra.yaml` validates with empty `decisions:` / `prior_insights:` / `findings:` blocks | ARCHITECT (stub) |
| `work/notes/cited_papers.yaml` | ARCHITECT (citation extraction) |
| `work/notes/literature.yaml` | LITERATURE |
| `astra.yaml` validates with non-empty `decisions:` per sub-analysis + `targets/targets.md` + `implementation-notes.md` | SPECIFY |
| recipes present in `astra.yaml` | IMPLEMENT |
| `results/<universe>/<output>/` | RUN |
| `comparison-report.yaml` | COMPARE |
| `REPRODUCTION-SUMMARY.md` + `.lightcone/comparison.html` + resolved `open-questions.md` | REVIEW (close-out) |

`git log --oneline` complements this — phase commits are the chronological view.

## Skills (activate before working)

- [`/constitution`](../constitution/SKILL.md) — for the interview's drafting phase
- [`/ralph-loops`](../ralph-loops/SKILL.md) — for the bash-loop and tmux-orchestrated runtime modes
- [`/managing-bibliography`](../managing-bibliography/SKILL.md) — for ACQUIRE
- [`/narrative`](../narrative/SKILL.md) — for SPECIFY
- [`/figure-comparison`](../figure-comparison/SKILL.md) — for REVIEW (close-out, mandatory)
- [`/check-sentence-by-sentence`](../check-sentence-by-sentence/SKILL.md) — for REVIEW (close-out, opt-in)

## Discipline

- **paper2astra is the workflow story; phase references are the depth.** SKILL.md tells you when to read which reference; the references carry the prompt prose ported from the legacy Paper2ASTRA Python package.
- **Workdir is the state.** No state machine, no resume mechanic — file existence + `git log` + `astra validate` answer "what phase am I on" deterministically. Each iteration's first move is *survey*.
- **Deterministic checks live in scripts.** When the answer is yes/no, call the script — `astra validate`, `git log`, `yq`, `ls`. Don't ask the agent to introspect what a deterministic check would tell you.
- **Use the up-to-date CLI surfaces, not skill-specific wrappers.** When `astra validate` already does the job, call it directly. Specifically: `astra validate <file>`, `astra validate --verify-evidence`, `astra paper add`. Use whatever the current `astra --help` surfaces.
- **arxiv-LaTeX-first acquisition.** When the paper is on arxiv, the source tarball is the substrate; equations, ligatures, captions, tables come through clean. PDF + Docling is a fallback for non-arxiv where there's no better source.
- **The original code goes into `work/reference/code/`** during ACQUIRE when available, and stays there as the canonical reference for every subsequent iteration (see "Code-as-canonical" above).
- **`/figure-comparison` and `/check-sentence-by-sentence` run inside REVIEW (close-out), not inside the loop.** Both have `AskUserQuestion` in their `allowed-tools`; REVIEW is the always-interactive close-out bookend that runs them in the main session so the prompts land. Don't try to spawn either under the `Task` tool from inside the loop.
- **Only the bookends are mandatory-interactive.** INTERVIEW (start) and REVIEW (close). Every other phase is configurable per the interview's per-phase mode choice — no "always interactive" flag on anything in between. The dial that does the heavy lifting on quality is rigor/frugality, threaded through ARCHITECT, SPECIFY, and IMPLEMENT's internal self-review passes.
- **Don't bias review sub-agents.** ARCHITECT, SPECIFY, and IMPLEMENT's self-review iterations spawn fresh sub-agents whose prompt is "check the artifact is consistent with the paper and the code" — never "here's what was just authored or fixed last round." Each round runs from a fresh reviewing context. Otherwise the reviewer pattern-matches on prior fixes rather than thinking from first principles.
- **ARCHITECT decides structure; SPECIFY decides content.** ARCHITECT's two parallel Explore sub-agents (paper-side + code-side) feed a synthesis sub-agent that writes the stub `astra.yaml` — sub-analyses, inputs, outputs, narrative prose. SPECIFY's per-sub-analysis paper pass + code pass + self-review fills in `decisions:`, `prior_insights:`, `findings:` and weaves anchor references into the narrative. Splitting **structure** from **content** keeps each phase's cognitive load bounded.
- **No synthetic data.** Unless the paper itself uses synthetic data as its input, every input dataset must be real (downloaded, queried, or fetched from a real archive). The implement phase reference repeats this; treat it as load-bearing.
- **Tmux preferred-when-available, never required.** Modes (1) and (2) work without it.
- **The siblings don't know about paper2astra.** Each SKILL stands on its own.
- **Workdir conventions stay.** The phase references preserve Paper2ASTRA's workdir layout (`work/reference/`, `work/notes/`, `targets/`, `astra.yaml`, `universes/`, `results/`) so workdirs from the legacy Paper2ASTRA package are interoperable with workdirs driven by this skill.

## Anti-patterns

- **Asking the user mid-sub-agent.** Sub-agent phases cannot reach the user. If a material conflict surfaces in a sub-agent phase, take the code's behavior (or paper's, if no code) as canonical, record the conflict in `open-questions.md` and as a `decisions:` block with both options preserved in `astra.yaml`, and let the next interactive phase ratify. Never make the sub-agent pick silently and discard the alternative.
- **Re-implementing what astra already does.** If `astra validate` returns clean, do not write a separate validator. If `astra paper add` caches the PDF, do not write a separate cache.
- **Treating Paper2ASTRA workdir as legacy.** It is not legacy — it is the substrate. The phase references inherit its conventions intentionally.
- **Bundling everything into one iteration.** Each iteration runs one or two phases, then exits. The constitution is realized across many iterations.
