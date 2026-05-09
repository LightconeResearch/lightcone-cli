# IMPLEMENT — write scripts and recipes; rigor-dialed self-review

Read `astra.yaml` (the filled spec) and `implementation-notes.md` (practical guidance). Write scripts in `scripts/` that produce each output, then add recipes to `astra.yaml` so the asset graph is wired end to end. After the first-pass implementation lands, a rigor-dialed self-review pass cross-checks the implementation against paper + code — same fresh-context-no-bias shape ARCHITECT and SPECIFY use. Fixes feed back into IMPLEMENT for the next iteration.

The constitution's per-phase mode defaults this to **sub-agent**. Most implementation is mechanical (translate spec → script), but algorithm choices on tricky steps may want ratification. Where parallelization is feasible (multiple independent outputs from different scripts), spawn one sub-agent per output and merge.

## Inputs

- `astra.yaml` — the filled spec (sub-analyses, decisions, prior_insights, findings, narrative — all populated by SPECIFY)
- `implementation-notes.md` — tricky algorithms, numerical gotchas, data-format quirks
- `work/notes/architect/paper-index.md` — for context when the spec compresses (sub-analysis decomposition, result loci, decision clusters)
- `work/notes/architect/code-index.md` (when code present) — natural decomposition + entry-points + data dependencies + gotchas (the canonical map of where each sub-analysis's logic lives in `work/reference/code/`)
- `work/reference/code/` (if present) — **canonical reference. Read on every iteration when implementing.** Where paper and code disagree, code wins for numerics, plotting, and method.

## Outputs

- `scripts/<output>.py` (or `.sh`, or whatever fits) — one script per output (or shared scripts for tightly-coupled outputs)
- `requirements.txt` — Python dependencies
- Recipes in `astra.yaml` — each output gets a `recipe:` block with `command:` and `inputs:`
- `work/notes/implement-review/round-<N>.md` — each rigor-dialed review round's findings (rigor only; one file per round)

## Step 1: write recipes + scripts

Read `astra.yaml` and `implementation-notes.md`. For each output, write a script in `scripts/` that produces it, and add a `recipe:` block to the output's entry in `astra.yaml` with `command:` and `inputs:`.

If `work/reference/code/` exists, **read the relevant code on every iteration** — not just to resolve ambiguities but as the canonical source of truth for numerics + method. Write clean scripts following ASTRA conventions (not verbatim copies), but treat the code's behavior as authoritative when it disagrees with the paper. When you encounter a paper-vs-code disagreement that SPECIFY's code pass missed:

- **Interactive IMPLEMENT** (rare; usually sub-agent): surface via `AskUserQuestion`.
- **Sub-agent IMPLEMENT** (default): continue with the code's behavior, append the disagreement to `<paper-slug>/open-questions.md`, and note it in `implementation-notes.md` so REVIEW (close-out) can ratify or override.

Without this discipline, iterations drift to "looks right" rather than "matches" — the failure mode the first-paper test surfaced.

When the reference code is substantial enough that implementation is really a migration of an existing codebase, follow `/lc-from-code`'s migration workflow in **augment existing ASTRA** mode. Use its code scan, minimal parameter-plumbing, dependency/container, and baseline-preservation strategies, but apply them to this reproduction's existing `astra.yaml`. Do not create a second ASTRA project or duplicate the spec; add recipes, code-backed options, implementation notes, and missing structure to the current reproduction artifact.

### Parallelize where feasible

When outputs are produced by independent scripts (no shared expensive computation), spawn one Task-tool sub-agent per output. Each sub-agent gets:

- The output's spec entry from `astra.yaml` (including its sub-analysis's `decisions:` / `findings:` for context)
- The relevant section of `implementation-notes.md`
- The matching entry in `work/notes/architect/code-index.md`'s natural-decomposition / entry-points block — that's the pointer back to the canonical code location for the sub-analysis the output lives in
- The relevant code path(s) under `work/reference/code/`

The orchestrator merges scripts and recipes after the per-output sub-agents finish. Tightly-coupled outputs (e.g. an MCMC producing both a chain and a summary statistic) stay in one sub-agent and one script.

### Rules for the first pass

1. **One script per output** (or a shared script for tightly-coupled outputs).
2. **Parameterize by decisions.** Each decision is a CLI argument; scripts also receive `--universe <universe_id>`. See lightcone-cli's `CLAUDE.md` for the full convention.
3. **Add recipes** to each output in `astra.yaml` with `command:` and `inputs:` (dependencies). Recipe inputs use the same `<analysis>.<output>` form the narrative skill's data-flow rules require.
4. **Create `requirements.txt`** with needed packages. Do not install them — the RUN phase manages environments.
5. **Do not execute scripts** — the RUN phase handles execution via `lc run`.
6. **Validate** with `astra validate astra.yaml` after adding recipes.

## Step 2: rigor-dialed self-review

After the first-pass implementation lands, the constitution's frugality / rigor dial decides what happens next:

- **Frugal:** one minimal review pass — a single fresh sub-agent reads `scripts/`, `astra.yaml`'s recipes, and the paper, and reports any obvious paper-vs-implementation inconsistencies. Fixes are applied once; no further iteration. If no fixes are needed, IMPLEMENT proceeds to RUN.
- **Rigor:** N rounds of fresh-context sub-agent review + fix. Each round runs a fresh reviewer that does not see the prior round's findings or fixes. Stop when **two consecutive rounds find no fixes** (strong termination criterion), or after 5 rounds (system cap), whichever comes first.

The discipline is the same shape ARCHITECT and SPECIFY use: each round's reviewer is fresh, prompted to check "is the implementation consistent with the paper and the code?", and outputs findings only — not edits. Fixes are applied between rounds by a separate IMPLEMENT-fix sub-agent (or the orchestrator inline for trivial mechanical fixes). Pattern-matching on prior fixes defeats the cross-check; the no-bias rule is load-bearing.

### Per-round fresh sub-agent — system prompt

> You are a paper-vs-implementation review agent. Read the implementation (`scripts/`, `astra.yaml` recipes), the paper, and the code (when present), and report any inconsistencies you find. You will be one of several independent reviewers; do not assume anything has already been fixed.
>
> ### Inputs
>
> - `scripts/` — first-pass implementation
> - `astra.yaml` — the spec (recipes are part of the implementation; structural + content fields are ARCHITECT's and SPECIFY's)
> - `implementation-notes.md`
> - `work/notes/architect/paper-index.md` — Grep into; do not re-read whole
> - `work/notes/architect/code-index.md` (when present) — natural decomposition + entry-points + gotchas
> - `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — paper text (Grep)
> - `work/reference/code/` (when present) — canonical reference for numerics + method
>
> ### What to check
>
> 1. **Recipe coverage.** Every output in `astra.yaml` has a recipe; every recipe runs a script that exists in `scripts/`.
> 2. **Method fidelity.** For each output, the script implements the method described by the relevant sub-analysis's `decisions:` and `findings:` in `astra.yaml` (which carry the verbatim paper quotes and code anchors). Where SPECIFY's code pass surfaced a material disagreement, the script follows the code's method (canonical-resolution rule), unless the spec recorded a different override in `decisions:` and `universes/baseline.yaml`.
> 3. **Numerical correctness.** Constants, hyperparameters, threshold values match the paper (or the code, where the canonical-resolution rule applied). Flag mismatches with `path:line` of the script and the paper §/eq + the relevant `astra.yaml#analyses.<sub-id>.decisions.<key>` entry.
> 4. **Data acquisition.** Scripts that fetch data use the real acquisition path from `astra.yaml`'s inputs — no synthetic / mock substitutes.
> 5. **Determinism.** Scripts set random seeds where the paper's method is stochastic. Library versions in `requirements.txt` are pinned where reproducibility requires it.
> 6. **Recipe wiring.** Recipe `inputs:` references match the data-flow the scripts actually consume; no orphan dependencies, no missing dependencies.
>
> ### What NOT to do
>
> - **Do not edit any file.** Your output is a findings file; an IMPLEMENT-fix pass responds to the findings.
> - **Do not re-read the entire paper.** Grep into `work/notes/architect/` and `work/reference/source/` (or `document.md`) for the specific claims you want to verify; the filled `astra.yaml` is your primary source for what each sub-analysis is supposed to do.
> - **Do not invent problems.** If the implementation matches paper + code, say so briefly.
> - **Do not assume a prior reviewer has been here.** You are fresh. First-principles read only.
>
> ### Output format — `work/notes/implement-review/round-<N>.md`
>
> ```markdown
> # Implement-review round <N>
>
> Reviewer ran fresh against scripts/, recipes in astra.yaml, paper, and code.
>
> ## Findings
>
> ### <category — e.g. "Method fidelity" / "Numerical correctness" / "Recipe wiring">
>
> - **<one-line finding>**
>   - **What's wrong**: <quote or `script:line` of the implementation problem>
>   - **Where to fix**: <`scripts/<file>.py:line` or `astra.yaml#path/to/recipe`>
>   - **Suggested fix**: <one-line concrete change>
>   - **Source**: <paper §X.Y "quote" + `astra.yaml#analyses.<sub-id>.decisions.<key>` evidence, or code `path:line`>
>
> ## Verdict
>
> - **fixes_needed**: <count>
> - **clean** | **needs-fixes**
> ```

### Step 3: IMPLEMENT-fix pass between rounds

After each round's findings file lands, an IMPLEMENT-fix sub-agent (or the orchestrator inline for trivial fixes) edits `scripts/`, `astra.yaml` recipes, `requirements.txt`, and `implementation-notes.md` per the suggested fixes. After any change to `astra.yaml`, run `astra validate astra.yaml`.

### Step 4: termination check

- `weak` (frugal): one pass. Done after fixes (or immediately, if `fixes_needed` was 0).
- `strong` (rigor):
  - If round N's `fixes_needed` was 0 AND round (N-1)'s was also 0 → done.
  - If N hits the system cap of 5 without two consecutive clean rounds, surface to the user via `AskUserQuestion`: "implement-review reached round cap with N fixes still landing; continue, accept the current implementation, or revise the constitution?" Default on user silence: accept current implementation, log the unfinished tail in `<paper-slug>/open-questions.md`, proceed.

The IMPLEMENT-review iterations are independent of the COMPARE → IMPLEMENT retry loop — review iterations run before RUN, on the spec/implementation alignment side; COMPARE retries run after RUN, on the result-matching side.

## Data: REAL DATA ONLY

**NEVER generate synthetic, mock, or fake data.** Every input dataset must be downloaded or queried from its real source (archive URL, database query, API, etc.). The methodology notes and `astra.yaml` inputs describe where each dataset comes from — write scripts that fetch the actual data.

The only exception is if the paper itself uses synthetic / simulated data as its input (e.g., N-body simulations, Monte Carlo samples). In that case, reproduce the paper's data generation procedure exactly as described — but this is reproducing the paper's methodology, not substituting real data with fakes.

If a dataset is behind a paywall, requires registration, or is "available upon request," write the download script with a clear error message explaining what the user needs to do manually. **Do NOT substitute synthetic data as a workaround.**

## Retry attempts (post-COMPARE)

If `comparison-report.yaml` exists from a prior COMPARE that returned `partial` or `fail`, the IMPLEMENT iteration is a **retry attempt**. Read `comparison-report.yaml` to understand what went wrong; focus on the outputs marked as non-matching. The constitution carries the attempt budget (default 5); the iteration's first move is to check whether `attempt` in the report has reached the budget. If it has, surface to the user via `AskUserQuestion` ("verdict still failing after N attempts — continue, change scope, or accept partial?") rather than burning more cycles.

A retry attempt re-runs the IMPLEMENT-review iterations on the changed scripts before proceeding to RUN.

## Survey signals (entry into IMPLEMENT)

- `astra.yaml` validates and `implementation-notes.md` exists ⇒ ready to implement first pass
- `scripts/` has one entry per output id; `requirements.txt` exists; recipes appear in `astra.yaml` ⇒ first-pass IMPLEMENT done
- For frugal: `work/notes/implement-review/round-1.md` with verdict `clean` (or no fixes were incorporated) ⇒ IMPLEMENT done
- For rigor: two consecutive `work/notes/implement-review/round-<N>.md` files both have verdict `clean` ⇒ IMPLEMENT done; proceed to RUN
- `comparison-report.yaml` returns `pass` ⇒ COMPARE → IMPLEMENT loop terminated; proceed to REVIEW (close-out)

## Notes

- **`lc run` is the canonical execution surface.** Scripts assume they will be invoked via the lightcone-cli runner. Do not hard-code working directories or assume environment activation.
- **Determinism where possible.** Set random seeds, fix library versions, prefer reproducible installations. The IMPLEMENT goal is not just "produces output once" but "reproducibly produces output across runs."
- **Tight coupling earns shared scripts.** When two outputs come from the same expensive computation (e.g. an MCMC produces both a parameter chain and a summary statistic), one script with multiple output paths is cleaner than two scripts that each re-do the work.
- **The fresh-context discipline is the same as ARCHITECT's and SPECIFY's self-review.** A reviewer that sees the prior round's findings stops finding the next class of inconsistency. Each round must spawn a brand-new sub-agent.
- **Minimize churn in fixes.** Targeted edits, not restructures. Big restructures defeat the round-over-round comparison the orchestrator uses to decide termination.
