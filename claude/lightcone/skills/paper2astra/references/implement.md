# IMPLEMENT — write scripts and recipes

Read `astra.yaml` (the spec) and `implementation-notes.md` (practical guidance). Write scripts in `scripts/` that produce each output, then add recipes to `astra.yaml` so the asset graph is wired end to end.

The constitution's per-phase mode is **user choice** for this phase — defaults to sub-agent. Most implementation is mechanical (translate spec → script), but algorithm choices on tricky steps may want ratification.

## Inputs

- `astra.yaml` — the structural spec
- `implementation-notes.md` — tricky algorithms, numerical gotchas, data-format quirks
- `work/notes/methodology.md` — for context when the spec compresses
- `work/reference/code/` (if present) — **canonical reference. Read on every iteration when implementing.** Where paper and code disagree, code wins for numerics, plotting, and method.

## Outputs

- `scripts/<output>.py` (or `.sh`, or whatever fits) — one script per output (or shared scripts for tightly-coupled outputs)
- `requirements.txt` — Python dependencies
- Recipes in `astra.yaml` — each output gets a `recipe:` block with `command:` and `inputs:`

## Task

Read `astra.yaml` and `implementation-notes.md`. Write scripts in `scripts/` that produce each output, then add recipes to `astra.yaml`.

If `work/reference/code/` exists, **read the relevant code on every iteration** — not just to resolve ambiguities but as the canonical source of truth for numerics + method. Write clean scripts following ASTRA conventions (not verbatim copies), but treat the code's behavior as authoritative when it disagrees with the paper. When you encounter a paper-vs-code disagreement that the running spec hasn't resolved:

- **Interactive IMPLEMENT** (rare; usually sub-agent): surface via `AskUserQuestion`.
- **Sub-agent IMPLEMENT** (default): continue with the code's behavior, append the disagreement to `<paper-slug>/open-questions.md`, and note it in `implementation-notes.md` so the next interactive seam can ratify or override.

Without this discipline, iterations drift to "looks right" rather than "matches" — the failure mode the first-paper test surfaced.

## Data: REAL DATA ONLY

**NEVER generate synthetic, mock, or fake data.** Every input dataset must be downloaded or queried from its real source (archive URL, database query, API, etc.). The methodology notes and `astra.yaml` inputs describe where each dataset comes from — write scripts that fetch the actual data.

The only exception is if the paper itself uses synthetic / simulated data as its input (e.g., N-body simulations, Monte Carlo samples). In that case, reproduce the paper's data generation procedure exactly as described — but this is reproducing the paper's methodology, not substituting real data with fakes.

If a dataset is behind a paywall, requires registration, or is "available upon request," write the download script with a clear error message explaining what the user needs to do manually. **Do NOT substitute synthetic data as a workaround.**

## Rules

1. **One script per output** (or a shared script for tightly-coupled outputs).
2. **Parameterize by decisions.** Each decision is a CLI argument; scripts also receive `--universe <universe_id>`. See lightcone-cli's `CLAUDE.md` for the full convention.
3. **Add recipes** to each output in `astra.yaml` with `command:` and `inputs:` (dependencies). Recipe inputs use the same `<analysis>.<output>` form the narrative skill's data-flow rules require.
4. **Create `requirements.txt`** with needed packages. Do not install them — the RUN phase manages environments.
5. **Do not execute scripts** — the RUN phase handles execution via `prism run` (now `lc run`).
6. **Validate** with `astra validate astra.yaml` after adding recipes.

## Retry attempts

If `comparison-report.yaml` exists from a prior COMPARE that returned `partial` or `fail`, the IMPLEMENT iteration is a **retry attempt**. Read `comparison-report.yaml` to understand what went wrong; focus on the outputs marked as non-matching. The constitution carries the attempt budget (default 5); the iteration's first move is to check whether `attempt` in the report has reached the budget. If it has, surface to the user via `AskUserQuestion` ("verdict still failing after N attempts — continue, change scope, or accept partial?") rather than burning more cycles.

## Survey signals (entry into IMPLEMENT)

- `astra.yaml` validates and `implementation-notes.md` exists ⇒ ready to implement
- `scripts/` has one entry per output id; `requirements.txt` exists; recipes appear in `astra.yaml` ⇒ first-pass IMPLEMENT done
- `comparison-report.yaml` returns `pass` ⇒ IMPLEMENT loop terminated; proceed to SUMMARIZE_RUN

## Notes

- **`lc run` is the canonical execution surface.** Scripts assume they will be invoked via the lightcone-cli runner. Do not hard-code working directories or assume environment activation.
- **Determinism where possible.** Set random seeds, fix library versions, prefer reproducible installations. The IMPLEMENT goal is not just "produces output once" but "reproducibly produces output across runs."
- **Tight coupling earns shared scripts.** When two outputs come from the same expensive computation (e.g. an MCMC produces both a parameter chain and a summary statistic), one script with multiple output paths is cleaner than two scripts that each re-do the work.
