# IMPLEMENT — write scripts and recipes; per-spawn self-review

Read `astra.yaml` (the filled spec) and `implementation-notes.md` (practical guidance). Write scripts in `scripts/` that produce each output, then add recipes to `astra.yaml` so the asset graph is wired end to end. After the first-pass implementation lands, a self-review pass cross-checks the implementation against paper + code — same fresh-context-no-bias shape ARCHITECT, SPECIFY, and LITERATURE use. Fixes feed back inside the same implement sub-agent for the next iteration.

This phase runs as the orchestrator-spawned `implement` sub-agent. Most implementation is mechanical (translate spec → script), but algorithm choices on tricky steps may want user ratification — the user can drop into the implement sub-agent's chat for that. Where parallelization is feasible (multiple independent outputs from different scripts), the implement sub-agent fans out to one Task-tool sub-sub-agent per output and merges.

## Inputs

- `astra.yaml` — the filled spec (sub-analyses, decisions, prior_insights, findings, narrative — all populated by SPECIFY)
- `implementation-notes.md` — tricky algorithms, numerical gotchas, data-format quirks
- `work/reference/index.json` — paper-side structural index (figures, tables, outline, citations); useful when the spec compresses or you need to find where in the paper a behavior is described.
- `work/reference/code-index.md` (when code present) — code inventory: module map, candidate decisions with file:line, entry-points, data dependencies, gotchas (the canonical map of where each sub-analysis's logic lives in `work/reference/code/`).
- `work/reference/code/` (if present) — **canonical reference. Read it when implementing each output.** Where paper and code disagree, code wins for numerics, plotting, and method.
- **code-expert** (agent ID passed in by the orchestrator) — reachable via `SendMessage`. The first stop for "where does X live in the code", "what's the canonical entry-point for Y", "what's the default parameter the code uses for Z". Cheaper than re-reading the code yourself.
- **paper-expert** (agent ID passed in by the orchestrator) — reachable via `SendMessage`. Useful when implementing an output and the spec doesn't fully capture what the paper says it should produce (e.g. "what's the expected axis range for Figure 4").
- CLAUDE.md — **Rigor** for this spawn's chosen rigor level; **Paper-vs-code disagreements** for prior conflicts already logged.

## Outputs

- `scripts/<output>.py` (or `.sh`, or whatever fits) — one script per output (or shared scripts for tightly-coupled outputs)
- `requirements.txt` — Python dependencies
- Recipes in `astra.yaml` — each output gets a `recipe:` block with `command:` and `inputs:`
- `work/notes/implement-review/round-<N>.md` — each review round's findings (one file per round; how many rounds depends on the rigor level)
- CLAUDE.md updates — append to **Paper-vs-code disagreements** for any new conflict surfaced during implementation; update **Rigor** with the post-spawn state per output (e.g. *baseline* after a cheap pass, *tightened* after heavy review).

## Step 1: write recipes + scripts

Read `astra.yaml` and `implementation-notes.md`. For each output, write a script in `scripts/` that produces it, and add a `recipe:` block to the output's entry in `astra.yaml` with `command:` and `inputs:`.

### With a code reference (`work/reference/code/` exists)

**Read the relevant code when implementing each output** — not just to resolve ambiguities but as the canonical source of truth for numerics + method. Write clean scripts following ASTRA conventions (not verbatim copies), but treat the code's behavior as authoritative when it disagrees with the paper. When you encounter a paper-vs-code disagreement that SPECIFY's code pass missed:

- **User reachable** (in the implement sub-agent's chat): ask in prose — paper method + code method + plausible impact + which one to take.
- **User unreachable**: continue with the code's behavior, append the disagreement to CLAUDE.md's **Paper-vs-code disagreements** AND `open-questions.md`, and note it in `implementation-notes.md` so REVIEW close-out can ratify or override.

Without this discipline, the implementation drifts to "looks right" rather than "matches" — the failure mode the first-paper test surfaced.

When the reference code is substantial enough that implementation is really a migration of an existing codebase, follow `/lc-from-code`'s migration workflow in **augment existing ASTRA** mode. Use its code scan, minimal parameter-plumbing, dependency/container, and baseline-preservation strategies, but apply them to this reproduction's existing `astra.yaml`. Do not create a second ASTRA project or duplicate the spec; add recipes, code-backed options, implementation notes, and missing structure to the current reproduction artifact.

### Without a code reference (`work/reference/code/` is absent)

When `code-status.yaml` records `found: false` or the cloned repo turned out to be unusable, there is no canonical code substrate to anchor against. **Write the implementation fresh from the spec** — `astra.yaml`'s decisions, findings, and prior_insights are now the only source of method-level truth, and the paper's prose (consulted via paper-expert) is the source of numerics-level truth. Don't pretend a code reference exists; don't try to find a similar paper's code as a stand-in. Implement what the spec describes, ask paper-expert when the spec compresses something you need clarified, and rely on COMPARE to surface anywhere the implementation has drifted from the paper's claims.

The code-as-canonical rule does not apply here — there is no code to be canonical. The paper is the only anchor. This is the harder path; reproductions on it converge slower and have more open questions for REVIEW close-out. Surface that honestly to the user as you go; don't dress up paper-only implementations as if they had a code anchor.

### Parallelize where feasible

When outputs are produced by independent scripts (no shared expensive computation), the implement sub-agent spawns one Task-tool sub-sub-agent per output. Each sub-sub-agent gets:

- The output's spec entry from `astra.yaml` (including its sub-analysis's `decisions:` / `findings:` for context)
- The relevant section of `implementation-notes.md`
- The matching entry in `work/reference/code-index.md`'s natural-decomposition / entry-points block — that's the pointer back to the canonical code location for the sub-analysis the output lives in
- The relevant code path(s) under `work/reference/code/`

The implement sub-agent merges scripts and recipes after the per-output sub-sub-agents finish. Tightly-coupled outputs (e.g. an MCMC producing both a chain and a summary statistic) stay in one sub-sub-agent and one script.

### Rules for the first pass

1. **One script per output** (or a shared script for tightly-coupled outputs).
2. **Parameterize by decisions.** Each decision is a CLI argument; scripts also receive `--universe <universe_id>`. See lightcone-cli's `CLAUDE.md` for the full convention.
3. **Add recipes** to each output in `astra.yaml` with `command:` and `inputs:` (dependencies). Recipe inputs use the same `<analysis>.<output>` form the narrative skill's data-flow rules require.
4. **Create `requirements.txt`** with needed packages. Do not install them — the RUN phase manages environments.
5. **Do not execute scripts** — the RUN phase handles execution via `lc run`.
6. **Validate** with `astra validate astra.yaml` after adding recipes.

## Step 2: self-review (rigor chosen per spawn)

After the first-pass implementation lands, the rigor level the orchestrator picked for this spawn (read CLAUDE.md's **Rigor** section) decides what happens next:

- **Cheap:** one minimal review pass — a single fresh Task-tool sub-agent reads `scripts/`, `astra.yaml`'s recipes, and the paper, and reports any obvious paper-vs-implementation inconsistencies. Fixes are applied once; no further iteration. If no fixes are needed, IMPLEMENT proceeds to RUN.
- **Heavy:** N rounds of fresh-context Task-tool sub-agent review + fix. Each round spawns a fresh reviewer that does not see the prior round's findings or fixes. Stop when **two consecutive rounds find no fixes**, or after 5 rounds (system cap), whichever comes first.

The discipline is the same shape ARCHITECT, SPECIFY, and LITERATURE use: each round's reviewer is fresh, prompted to check "is the implementation consistent with the paper and the code?", and outputs findings only — not edits. Fixes are applied between rounds by the implement sub-agent itself (or the orchestrator inline for trivial mechanical fixes). Pattern-matching on prior fixes defeats the cross-check; the no-bias rule is load-bearing.

### Per-round fresh sub-agent — system prompt

> You are a paper-vs-implementation review agent. Read the implementation (`scripts/`, `astra.yaml` recipes), the paper, and the code (when present), and report any inconsistencies you find. You will be one of several independent reviewers; do not assume anything has already been fixed.
>
> ### Inputs
>
> - `scripts/` — first-pass implementation
> - `astra.yaml` — the spec (recipes are part of the implementation; structural + content fields are ARCHITECT's and SPECIFY's)
> - `implementation-notes.md`
> - `work/reference/index.json` — Grep into; do not re-read whole
> - `work/reference/code-index.md` (when present) — natural decomposition + entry-points + gotchas
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
> - **Do not re-read the entire paper.** Ask paper-expert / code-expert via `SendMessage` for claim verification, or Grep into `work/reference/index.json`, `work/reference/code-index.md`, and `work/reference/source/` (or `document.md`) for specific items. The filled `astra.yaml` is your primary source for what each sub-analysis is supposed to do.
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

After each round's findings file lands, the implement sub-agent (or the orchestrator inline for trivial fixes) edits `scripts/`, `astra.yaml` recipes, `requirements.txt`, and `implementation-notes.md` per the suggested fixes. After any change to `astra.yaml`, run `astra validate astra.yaml`.

### Step 4: termination check

- **Cheap:** one pass. Done after fixes (or immediately, if `fixes_needed` was 0).
- **Heavy:**
  - If round N's `fixes_needed` was 0 AND round (N-1)'s was also 0 → done.
  - If N hits the 5-round system cap without two consecutive clean rounds, the implement sub-agent stops and reports back to the orchestrator. If the user is reachable, ask in prose: "implement-review reached round cap with N fixes still landing; continue, accept the current implementation, or revise scope?" If the user is unreachable, accept current implementation, log the unfinished tail in `open-questions.md`, and let the orchestrator decide whether to proceed or re-spawn.

The IMPLEMENT-review iterations are independent of the COMPARE → IMPLEMENT retry loop — review iterations run before RUN, on the spec/implementation alignment side; COMPARE retries run after RUN, on the result-matching side.

## Data: REAL DATA ONLY

**NEVER generate synthetic, mock, or fake data.** Every input dataset must be downloaded or queried from its real source (archive URL, database query, API, etc.). The methodology notes and `astra.yaml` inputs describe where each dataset comes from — write scripts that fetch the actual data.

The only exception is if the paper itself uses synthetic / simulated data as its input (e.g., N-body simulations, Monte Carlo samples). In that case, reproduce the paper's data generation procedure exactly as described — but this is reproducing the paper's methodology, not substituting real data with fakes.

If a dataset is behind a paywall, requires registration, or is "available upon request," write the download script with a clear error message explaining what the user needs to do manually. **Do NOT substitute synthetic data as a workaround.**

## Retry attempts (post-COMPARE)

If `comparison-report.yaml` exists from a prior COMPARE that returned `partial` or `fail`, the orchestrator may re-spawn `implement` as a **retry attempt**. Read `comparison-report.yaml` to understand what went wrong; focus on the outputs marked as non-matching. Default attempt budget is 5 (the orchestrator can override per spawn); the implement sub-agent's first move is to check whether `attempt` in the report has reached the budget. If it has, stop and report back — if the user is reachable, ask in prose ("verdict still failing after N attempts — continue, change scope, or accept partial?"); if not, accept partial, log the failure in CLAUDE.md's **Rigor** section as an open opportunity, and let the orchestrator decide.

A retry attempt re-runs the IMPLEMENT-review iterations on the changed scripts before proceeding to RUN.

## Survey signals (entry into IMPLEMENT)

- `astra.yaml` validates and `implementation-notes.md` exists ⇒ ready to implement first pass
- `scripts/` has one entry per output id; `requirements.txt` exists; recipes appear in `astra.yaml` ⇒ first-pass IMPLEMENT done
- For cheap: `work/notes/implement-review/round-1.md` with verdict `clean` (or no fixes were incorporated) ⇒ IMPLEMENT done
- For heavy: two consecutive `work/notes/implement-review/round-<N>.md` files both have verdict `clean` ⇒ IMPLEMENT done; orchestrator proceeds to RUN
- `comparison-report.yaml` returns `pass` ⇒ COMPARE → IMPLEMENT loop terminated; orchestrator proceeds to REVIEW close-out

## Notes

- **`lc run` is the canonical execution surface.** Scripts assume they will be invoked via the lightcone-cli runner. Do not hard-code working directories or assume environment activation.
- **Determinism where possible.** Set random seeds, fix library versions, prefer reproducible installations. The IMPLEMENT goal is not just "produces output once" but "reproducibly produces output across runs."
- **Tight coupling earns shared scripts.** When two outputs come from the same expensive computation (e.g. an MCMC produces both a parameter chain and a summary statistic), one script with multiple output paths is cleaner than two scripts that each re-do the work.
- **The fresh-context discipline is the same as ARCHITECT's, SPECIFY's, and LITERATURE's self-review.** A reviewer that sees the prior round's findings stops finding the next class of inconsistency. Each round must spawn a brand-new Task-tool sub-agent.
- **Minimize churn in fixes.** Targeted edits, not restructures. Big restructures defeat the round-over-round comparison the implement sub-agent uses to decide termination.
- **Commit per output as it lands.** One commit per script + recipe wiring; one commit per review-round file; one commit per fix pass. The orchestrator reads `git log` to track progress.
