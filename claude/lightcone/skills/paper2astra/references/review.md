# REVIEW — rigor-dialed fresh-context spec audit

A fresh-context sub-agent reads `astra.yaml` against the paper and the code and asks "is this consistent?" The reviewer never sees what was just implemented or fixed last round — its only job is first-principles cross-reference. SPECIFY incorporates fixes; a *fresh* reviewer re-runs; iterate until two consecutive rounds find nothing or a configured cap is hit.

REVIEW's depth is set by the constitution's **frugality / rigor** dial (see "Rigor vs frugality" in `../SKILL.md`):

- **Frugal:** skip REVIEW entirely, or run a single fresh sub-agent pass and incorporate its fixes once.
- **Rigor:** N rounds — each round runs a fresh reviewer; SPECIFY incorporates fixes; the next round runs *another* fresh reviewer that has not seen the fixes. Iterate until two consecutive rounds find no fixes (the strong termination criterion the loop already uses), or a system cap of 5 rounds, whichever is sooner.

The constitution's per-phase mode defaults this to **sub-agent**; interactive REVIEW is rare (a paper that hits the SPECIFY conflict-surfacing path heavily may want a human in the loop).

## Why fresh-context sub-agents

A reviewer that has just helped fix `astra.yaml` will pattern-match on its own fixes rather than re-reading the paper. Catching the *next* class of inconsistency requires a fresh context that doesn't carry the prior round's framing. The sub-agent's prompt must therefore say "check `astra.yaml` is consistent with the paper and the code" — never "here's what was just fixed; check it." The reviewer's only inputs are the paper, the code, and the spec.

This also bounds the work: each round is one fresh sub-agent over a bounded artifact. Rigor doesn't mean "longer review" — it means "more independent reviewers."

## Inputs

- `astra.yaml` — the spec from SPECIFY (the artifact under review)
- `universes/baseline.yaml` — the universe selection
- `implementation-notes.md` — practical guidance for IMPLEMENT
- `targets/targets.md` — coverage obligations
- `work/notes/methodology.md` — consolidated decision map / results inventory / data sources (Grep into for cross-reference; do not re-read whole)
- `work/notes/study/` — per-section paper-vs-code agreement-check files (Grep into for verbatim claims and code locations)
- `work/notes/literature.yaml` (if present) — for evidence verification
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — paper text (Grep into; do not re-read whole)
- `work/reference/code/` (if present) — original code, canonical reference for numerics + method

## Outputs

- In-place edits to `astra.yaml`, `universes/baseline.yaml`, `implementation-notes.md` driven by reviewer findings — written by SPECIFY in response, **not** by the reviewer itself
- `work/notes/review/round-<N>.md` — each round's reviewer findings (one file per round; the orchestrator passes round-N's findings to SPECIFY for fixing, then spawns round-(N+1) as a fresh sub-agent that does not see round-N's findings)

## Step 1: orchestrator decides round count from the constitution's rigor dial

Read the constitution's termination-criterion field:

- `weak` (frugal) → at most one round; if no fixes found, REVIEW is done. If skipping is preferred (the user said "skip review"), the orchestrator records "REVIEW skipped per constitution" in the workdir and proceeds to IMPLEMENT.
- `strong` (rigor) → iterate. Stop when **two consecutive rounds find no fixes**, or after 5 rounds (system cap), whichever comes first.

## Step 2: per-round fresh sub-agent — system prompt

Spawn one Task-tool sub-agent per round. Each round's sub-agent gets only the inputs above — never the prior round's findings, never a description of what was just fixed.

> You are an ASTRA-spec reviewer. Read `astra.yaml`, the paper, and the code (when present), and report any inconsistencies you find. You will be one of several independent reviewers; do not assume anything has already been fixed.
>
> ### Inputs
>
> - `astra.yaml` — the spec under review
> - `universes/baseline.yaml`
> - `implementation-notes.md`
> - `targets/targets.md`
> - `work/notes/methodology.md` — consolidated paper-derived decision map (Grep into; do not re-read whole)
> - `work/notes/study/` — per-section paper-vs-code agreement-check files (Grep into for verbatim claims and code locations)
> - `work/notes/literature.yaml` (if present)
> - `work/reference/source/` (arXiv LaTeX; preferred) or `work/reference/document.md` (Docling fallback) — paper text (Grep into; do not re-read whole)
> - `work/reference/code/` (when present) — canonical reference for numerics + method
>
> ### What to check
>
> 1. **Target coverage.** Every entry in `targets/targets.md` must appear in `astra.yaml` as an output, finding, input, decision, or universe default. Any missing target either earns a spec home or an explicit out-of-scope reason in `targets.md`.
> 2. **Output definitions.** Each output has a clear `type` and sufficient description.
> 3. **Methodology coverage.** Cross-check `work/notes/methodology.md` against the spec for gaps: missing hyperparameters, underspecified algorithms, vague data-processing steps. Grep targeted sections of the paper to confirm.
> 4. **Decisions.** Decisions cover what affects reproducibility. Cosmetic / pure-tooling choices should not be decisions; anything material that is missing should be added. `universes/baseline.yaml` must be consistent with the paper's reported choices (or with the code's, when paper-vs-code resolution applied per the canonical-resolution rule).
> 5. **Data acquisition.** Every input has a concrete acquisition path — a download URL, database query, API call, or package name. Vague references ("available upon request", no source named) are flagged.
> 6. **Implementation-notes completeness.** Does `implementation-notes.md` flag the tricky parts the IMPLEMENT phase will hit? Cross-check against `work/notes/study/<NN>-<slug>.md` material-disagreement entries — every paper-vs-code material disagreement that landed in the spec should also appear in implementation-notes for IMPLEMENT.
> 7. **Evidence verification.** If `work/notes/literature.yaml` exists, run `astra validate astra.yaml --verify-evidence`. Flag any misquotes or unsupported claims; these typically arise when a quote was paraphrased or when prefix/suffix carry editorial commentary instead of real surrounding text.
> 8. **Code-as-canonical applied.** Where paper and code disagree on a material choice (per `work/notes/study/`'s material-disagreement rows), check that `universes/baseline.yaml` selects the code's choice, OR that an interactive seam recorded a different user choice. Flag any material disagreement where the spec silently picked the paper without recording an explicit override.
> 9. **No synthetic data.** Unless the paper itself uses synthetic data, every input has a real acquisition source — no mock / synthetic substitutes anywhere in the spec, recipes, or implementation-notes.
>
> ### What NOT to do
>
> - **Do not edit `astra.yaml`** or any other file. Your output is a findings file; SPECIFY responds to the findings. Editing here defeats the multi-round-fresh-context discipline.
> - **Do not re-read the entire paper.** Use Grep to look up specific claims you want to verify. Work primarily from `work/notes/methodology.md` and `work/notes/study/`.
> - **Do not invent problems.** If the spec is consistent with paper + code, say so briefly.
> - **Do not assume a prior reviewer has been here.** You are fresh. Treat this as a first-principles read.
>
> ### Output format — `work/notes/review/round-<N>.md`
>
> ```markdown
> # Review round <N>
>
> Reviewer ran fresh against `astra.yaml`, paper, and code.
>
> ## Findings
>
> ### <category — e.g. "Target coverage" / "Decisions" / "Data acquisition" / "Evidence">
>
> - **<one-line finding>**
>   - **What's wrong**: <quote or location of the spec problem>
>   - **Where to fix**: <`astra.yaml#path/to/key` or `implementation-notes.md`>
>   - **Suggested fix**: <one-line concrete change>
>   - **Source**: <paper §X.Y "quote" + `work/notes/study/<id>` row, or code `path:line`>
>
> ## No-fix sections
>
> Brief one-liners for sections that look clean (so the orchestrator knows you actually checked).
>
> ## Verdict
>
> - **fixes_needed**: <count>
> - **clean** | **needs-fixes**
> ```
>
> Be concise. The orchestrator reads this file to decide whether to spawn another round and what SPECIFY needs to fix.

## Step 3: SPECIFY incorporates findings

After the round's findings file lands, SPECIFY (or the orchestrator playing SPECIFY for trivial mechanical fixes) edits `astra.yaml`, `universes/baseline.yaml`, `implementation-notes.md` per the suggested fixes. After any change to `astra.yaml`, run:

```bash
astra validate astra.yaml
```

If literature.yaml is present:

```bash
astra validate astra.yaml --verify-evidence
```

The orchestrator records what was fixed in a small commit per round so `git log` shows the chain.

## Step 4: termination check

After SPECIFY incorporates the round's fixes, the orchestrator decides whether to spawn another round:

- `weak` (frugal): one pass is enough. Done.
- `strong` (rigor):
  - If round N's `fixes_needed` was 0 AND round (N-1)'s was also 0 → done (two consecutive clean rounds = strong termination criterion).
  - If round N is the first round (N=1), spawn round 2 unconditionally so we can compare.
  - If round N produced fixes, spawn round (N+1) as a fresh sub-agent that does not see round N's findings or the fixes.
  - If N hits the system cap of 5 rounds without two consecutive clean rounds, surface to the user: "REVIEW reached round cap with N fixes still landing; continue, accept the current spec, or revise the constitution?" via `AskUserQuestion`. Default on user silence: accept the current spec, log the unfinished tail in `<paper-slug>/open-questions.md`, and proceed.

## Survey signals (entry into REVIEW)

- `astra.yaml` exists and `astra validate astra.yaml` returns clean ⇒ ready to review
- `work/notes/review/round-1.md` exists ⇒ first round done
- For frugal: `round-1.md` exists with verdict `clean` (or no fixes were incorporated) ⇒ REVIEW done
- For rigor: two consecutive `round-<N>.md` and `round-<N-1>.md` files both have verdict `clean` ⇒ REVIEW done; proceed to IMPLEMENT
- `astra validate astra.yaml --verify-evidence` returns clean (when literature.yaml exists) ⇒ evidence side validated

## Notes

- **REVIEW does not write code.** Its outputs are findings; SPECIFY's edits to the spec / notes implement them.
- **The fresh-context discipline is load-bearing.** A reviewer that sees the prior round's findings or fixes pattern-matches on them and stops finding the next class of inconsistency. Each round must spawn a brand-new sub-agent with only paper + code + spec as inputs.
- **Minimize churn in fixes.** SPECIFY's edits should target the specific finding, not restructure surrounding spec. Big restructures defeat the round-over-round comparison the orchestrator uses to decide termination.
- **A clean REVIEW reduces IMPLEMENT thrash.** It is worth running even when SPECIFY's output looked fine — fresh-context cross-checks catch "looks fine in isolation, breaks under full coverage" gaps.
- **For frugal runs, REVIEW can be skipped when SPECIFY ran interactively** and the user already ratified material conflicts. The constitution records the skip; iterations honor it.
