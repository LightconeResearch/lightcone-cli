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

Reproduce a published paper in ASTRA. The skill is **interview-first**: a short interactive crafting phase up front that produces a per-paper reproduction constitution. After the interview, paper2astra hands the constitution to a ralph loop that drives multi-session reproduction. Successive iterations of the loop survey the workdir, execute one or two phases, exit cleanly, and re-spawn with fresh context until the constitution is realized.

This is a Claude-Code-native skill. There is no Python orchestrator, no state machine, no resume mechanic — the workdir on disk + git history are the substrate.

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
| [`/ralph-loops`](../ralph-loops/SKILL.md) | After interview — launches the loop that drives all subsequent phases |
| [`/narrative`](../narrative/SKILL.md) | SPECIFY — authoring the `narrative:` and `rationale:` prose in `astra.yaml` |

paper2astra does not re-implement what these skills already do — it tells the agent at each phase to invoke them.

After paper2astra completes, recommend adjacent follow-up skills when useful:
[`/check-sentence-by-sentence`](../check-sentence-by-sentence/SKILL.md) audits
paper claims against code locations, and
[`/figure-comparison`](../figure-comparison/SKILL.md) builds a portable
side-by-side HTML report for paper artifacts versus reproduced results.

## Workflow

### Interview (interactive — once per project)

The interview is the only phase paper2astra runs interactively. Read [`references/interview.md`](references/interview.md) in full before starting.

The interview has four jobs:

1. **Identify the paper** — DOI / arXiv ID / title; whether code is available; whether the user has prior experience with this paper.
2. **Scope the reproduction** — full reproduction vs targeted (e.g. only the BAO fit), which figures/tables/numbers are the targets.
3. **Choose interactive vs sub-agent per phase** — see "Per-phase mode" below. The defaults are reasonable; the user gets to flip any of them.
4. **Draft the per-paper constitution** — invoke `/constitution`. The constitution lives at the project root (or wherever the user prefers). It captures the paper, the scope, the per-phase mode choices, and the evidence checks.

After the constitution is approved, the interview ends. Launch the ralph loop:

```bash
../ralph-loops/scripts/ralph paper2astra-constitution.md
```

Tell the user: *"Constitution drafted. Launching ralph loop in tmux session `ralph-paper2astra-constitution`. Each iteration will run one or two phases and exit; the next iteration picks up where it left off. Attach with `tmux attach -t ralph-paper2astra-constitution`."*

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
| ACQUIRE | sub-agent | Mostly mechanical; surfacing happens only on download failures. |
| PARSE | sub-agent | Deterministic Docling / arXiv extraction. |
| SUMMARIZE | sub-agent | Parallel paper + code reading benefits from fresh context per task. |
| EXTRACT_TARGETS | user choice | The selection of replication targets is sometimes obvious, sometimes wants user input. |
| LITERATURE | sub-agent | One sub-agent per cited paper — pure parallel grunt-work. |
| SPECIFY | **interactive** | Material paper-vs-code conflicts surface here; the user must ratify. |
| REVIEW | user choice | Pre-implement sanity check; can be either. |
| IMPLEMENT | user choice | Mostly mechanical, but algorithm choices may want ratification. |
| RUN | user choice | Mechanical, but failures need diagnosis. |
| COMPARE | **interactive** | Verdict (was the reproduction close enough?) is the second mandatory user-ratification seam. |
| SUMMARIZE_RUN | sub-agent | Final report; no decisions remain. |

The constitution records the choice; ralph iterations honor it. Sub-agent phases are spawned via the `Task` tool from inside the main loop session — that gives them fresh context but no user-reach. Interactive phases run inline in the loop session and may pause with `AskUserQuestion` at material seams.

### Material conflicts (the SPECIFY seam)

Inside SPECIFY, when paper and code disagree on something material, do not silently pick one. Use `AskUserQuestion` to surface the conflict:

- **Material** = a different choice would plausibly change a numeric result the paper reports.
- **Stylistic / cosmetic / pure-tooling differences** are not material — record them in `implementation-notes.md` and move on.
- **Default on user silence is paper.** If the user does not respond, take the paper's stated method as canonical and record the override (with reason) in a finding or insight.

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
- [`/ralph-loops`](../ralph-loops/SKILL.md) — for the loop that drives phases
- [`/managing-bibliography`](../managing-bibliography/SKILL.md) — for ACQUIRE
- [`/narrative`](../narrative/SKILL.md) — for SPECIFY

## Discipline

- **paper2astra is the workflow story; phase references are the depth.** SKILL.md tells you when to read which reference; the references carry the prompt prose ported from the legacy Paper2ASTRA Python package.
- **Use the up-to-date CLI surfaces, not skill-specific wrappers.** When `astra validate` already does the job, call it directly. Specifically: `astra validate <file>`, `astra validate --verify-evidence`, `astra paper add`. Use whatever the current `astra --help` surfaces.
- **No synthetic data.** Unless the paper itself uses synthetic data as its input, every input dataset must be real (downloaded, queried, or fetched from a real archive). The implement phase reference repeats this; treat it as load-bearing.
- **Workdir conventions stay.** The phase references preserve Paper2ASTRA's workdir layout (`work/reference/`, `work/notes/`, `targets/`, `astra.yaml`, `universes/`, `results/`) so workdirs from the legacy Paper2ASTRA package are interoperable with workdirs driven by this skill.

## Anti-patterns

- **Asking the user mid-sub-agent.** Sub-agent phases cannot reach the user. If the constitution puts SPECIFY in sub-agent mode and a material conflict surfaces, the sub-agent must record the conflict in a `decisions:` block (with both options preserved) and let the next interactive phase ratify it. Never make the sub-agent pick silently.
- **Re-implementing what astra already does.** If `astra validate` returns clean, do not write a separate validator. If `astra paper add` caches the PDF, do not write a separate cache.
- **Treating Paper2ASTRA workdir as legacy.** It is not legacy — it is the substrate. The phase references inherit its conventions intentionally.
- **Bundling everything into one ralph iteration.** Each iteration runs one or two phases, then exits. The constitution is realized across many iterations.

## Provenance

`paper2astra` is a fresh skill, but the phase prose ports 1:1 from the prompts in [`LightconeResearch/Paper2ASTRA/src/paper2astra/prompts/`](https://github.com/LightconeResearch/Paper2ASTRA/tree/main/src/paper2astra/prompts) (commit b3b54b5 and onward on `feat/skill-form-redesign`). The Paper2ASTRA Python package retires once this skill is in regular use; the repo persists as a reference for the original prompts and pipeline structure.

The complementary skills (`check-sentence-by-sentence` and `figure-comparison`) originate from Nolan Koblischke.
