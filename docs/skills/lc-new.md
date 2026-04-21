# /lc-new

Create a new ASTRA analysis from a research question.

## Purpose

`/lc-new` takes a research question (or reads it from context) and produces a complete, well-scoped `astra.yaml` backed by literature evidence. It is the first thing a researcher runs in a new project.

## Workflow phases

### Phase 1 — Research Question

- Elicits the research question, outcome metric, and relevant domain from the user.
- Confirms the intended analysis approach before proceeding.

### Phase 2 — Analysis Structure

- Drafts the skeleton `astra.yaml`: inputs, outputs, decisions (without literature evidence yet).
- Identifies the key decisions that will drive the analysis.
- Checks for sub-analyses that should be separate `astra.yaml` files.

### Phase 3 — Deep Dive (Literature + Decisions)

- Searches for relevant papers using `WebSearch` and reads them via the `lc-extractor` subagent.
- For each key decision, finds papers that support or constrain the available options.
- Populates `decisions:` entries with `rationale`, `options`, and default values grounded in literature.
- Adds `prior_insights:` and `findings:` based on the papers.

### Phase 4 — Finalize

- Writes the complete `astra.yaml` with all decisions documented.
- Updates `CLAUDE.md` with analysis context.
- Creates the baseline universe (`universes/baseline.yaml`).
- Commits the result.

## Key rules

- All decisions must cite specific papers or empirical evidence — not just "common practice".
- The baseline universe must be scientifically defensible, not arbitrary.
- Sub-analyses should be created with `lc init --sub-analysis` rather than inlined.
- Never skip Phase 3 even for "obvious" methodological choices.

## Related

- [lc-build](lc-build.md) — the next step after `/lc-new`
- `claude/lightcone/guides/astra-reference.md` — full `astra.yaml` spec
