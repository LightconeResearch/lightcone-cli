# SPECIFY — fill the stub `astra.yaml`, two passes per sub-analysis

Read the stub `astra.yaml` from ARCHITECT and fill in `decisions:`, `prior_insights:`, `findings:` per sub-analysis, weaving the existing narrative with `astra-anchor:` references as entries land. SPECIFY is the **first user-ratification seam** — material paper-vs-code conflicts surface here; the default mode is interactive so the user can ratify.

This phase replaces the old SPECIFY's monolithic shape. The new structure runs **two passes per sub-analysis** (paper, then code, when code exists), then a rigor-dialed self-review pass. The two passes are the cross-check: the paper pass authors what the paper says; the code pass surfaces where the code says something different; the difference is gold (it's where the reproduction has to make a decision).

The constitution's per-phase mode defaults to **interactive** for this phase, but the user can flip it. When SPECIFY runs as a sub-agent, it falls back to the canonical-resolution rule (code wins where paper and code disagree on a material choice) and surfaces unresolved conflicts to `<paper-slug>/open-questions.md`.

Per-sub-analysis work is parallelizable when sub-analyses are independent. Each sub-analysis's two passes (paper, then code) run sequentially within that sub-analysis; across sub-analyses the work fans out.

## Inputs

- `astra.yaml` — the stub from ARCHITECT (sub-analyses, inputs, outputs, narrative; empty `decisions:` / `prior_insights:` / `findings:` blocks)
- `work/notes/architect/paper-index.md` — paper-side decision clusters, result loci, citations
- `work/notes/architect/code-index.md` (when code present) — module map, natural decomposition, entry-points, gotchas
- `work/notes/literature.yaml` (if present) — prior insights with evidence quotes and decision links (from LITERATURE)
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — paper text (Grep into; do not re-read whole)
- `work/reference/figures/`, `work/reference/tables/`, `work/reference/metadata.json` — extracted artifacts (Path B only)
- `work/reference/code/` (if present) — original code, canonical reference for numerics + method
- The per-paper constitution — its **Desired State** + the per-phase mode + the rigor / frugality dial
- `work/notes/notes.md` — user-supplied context (read by every phase if present)

## Outputs

- `astra.yaml` — **filled form**: each sub-analysis's `decisions:`, `prior_insights:`, `findings:` populated with `evidence:` selectors; `narrative:` keys updated to weave `astra-anchor:` references into prose as entries land; validates with `astra validate astra.yaml --verify-evidence` when literature.yaml is present
- `universes/baseline.yaml` — selects the paper's choices (where paper and code disagree per the canonical-resolution rule, see "Material conflicts" below)
- `implementation-notes.md` — concise practical guidance for the IMPLEMENT phase: tricky algorithms, numerical gotchas, data-format quirks, things the spec can't capture. Bullets, not essays.
- `targets/targets.md` — small target ledger COMPARE consumes: per output (already declared by ARCHITECT), a brief entry with type, priority, paper value, expected match criteria, and the path to the reference figure / table / metric (when applicable, copy the reference file into `targets/` so the directory is self-contained)
- `work/notes/specify-review/<sub-analysis>-round-<N>.md` — each rigor-dialed review round's findings (rigor only; one file per round per sub-analysis)

## Substrate skills to invoke

- **`/narrative`** — narrative authoring (any of the five `narrative.{summary,inputs,methods,findings,outputs}` keys, plus decision `rationale:` fields) is owned by the narrative skill. Invoke it during the **paper pass** when authoring or extending narrative prose. The narrative skill teaches reserved entity names, the tree-path anchor grammar, the conditional-narrative requirement (which keys are required when), the five-key authoring order, paper-reproduction fidelity discipline, and the new downstream-consumer discipline (lightcone-cli#108). Do not duplicate that content.

Your responsibility in this phase is the **content**: build out the `decisions:` / `prior_insights:` / `findings:` for each sub-analysis with verbatim paper quotes anchored to the paper as evidence, and weave `astra-anchor:` references back into the narrative as entries land. ARCHITECT already settled the structure.

## The two-pass-per-sub-analysis structure

For each sub-analysis (parallelizable across independent sub-analyses):

### Pass A — paper pass

Read the paper's section(s) covering this sub-analysis. Author:

1. **`decisions:`** — every choice in this sub-analysis where a different defensible option could plausibly shift a numerical result: algorithmic methods, thresholds, statistical approaches, data selection criteria, calibration choices. Use `when`, `incompatible_with`, and `requires` constraints for non-independent decisions.

   For each decision, the paper-pass authors:
   - The chosen option with its name + a `rationale:` block (use `/narrative` for the prose).
   - Sibling alternatives mentioned in the paper, each as a separate option.
   - `evidence:` for the chosen option using `TextQuoteSelector` against the paper text — verbatim quote + `prefix` / `suffix` from real surrounding text + page or section anchor.

   Read `.claude/guides/decision-guide.md` (in lightcone-cli's plugin bundle) for the full definition of what counts. **Only exclude pure tooling choices** (language, library, file format) and fixed constraints. A typical sub-analysis has 2–6 decisions; if a sub-analysis has fewer than 2, revisit `work/notes/architect/paper-index.md` and reconsider.

2. **`prior_insights:`** — incorporate insights from `work/notes/literature.yaml` (when present) that bear on this sub-analysis's decisions. Use the `decision_links` mapping to attach each insight to the relevant decision options, so the multiverse captures evidence-backed alternative choices from the literature.

3. **`findings:`** — paper-level claims and quantitative results scoped to this sub-analysis, each with source-anchored `evidence:` (verbatim quote against the paper). Pull the verbatim claims for each output's expected value from the paper text + the result loci in `paper-index.md`.

4. **Weave `astra-anchor:` references into the existing narrative.** ARCHITECT wrote `narrative:` prose without anchors because the entries didn't exist. Now they do — extend the narrative to point at the new `decisions:` / `prior_insights:` / `findings:` entries via the tree-path anchor grammar. Use `/narrative` for this pass; it carries the discipline.

5. **Verify evidence quotes against the paper source by Grep** — `astra validate --verify-evidence` currently verifies `prior_insights` evidence; artifact-anchored `findings` evidence still needs a manual quote check before the code pass.

### Pass B — code pass (when `work/reference/code/` exists)

Read the code that implements this sub-analysis (`work/notes/architect/code-index.md`'s natural-decomposition rows point at the relevant modules / scripts). Augment / amend:

1. **Code-as-canonical material disagreements.** For each decision authored in the paper pass, locate its implementation in the code. Where paper and code disagree:
   - **Material** = a different choice would plausibly change a numeric result the paper reports.
   - **Stylistic / cosmetic / pure-tooling** = not material; record in `implementation-notes.md` and move on.

   For **material** disagreements, behavior depends on whether SPECIFY is interactive:
   - **Interactive SPECIFY** (default): pause and surface via `AskUserQuestion`. Present the paper's stated method (with quote + section), the code's actual method (with `path:line`), the plausible impact ("changes the BAO peak amplitude by ~5%"), and three options: paper, code, *something else* (custom, with the user's choice spelled out). **Default on user silence is code when `work/reference/code/` exists, otherwise paper.**
   - **Sub-agent SPECIFY** (rare; the constitution lists this only when the user explicitly chose it): take **code as canonical** per the canonical-resolution rule, append the conflict to `<paper-slug>/open-questions.md` so the user sees it at the next session boundary, and let `universes/baseline.yaml` select the code's method. The user can flip the baseline at REVIEW (close-out).

   Either way, the override is preserved in `astra.yaml` as a `decisions:` entry with both options preserved, plus the `universes/baseline.yaml` selecting whichever option won. A `findings:` entry (or an insight if the conflict matters for replication discipline broadly) records the conflict with quote + line evidence.

2. **Code-revealed insights and findings.** Things the code does that the paper doesn't describe (a calibration version, a cut stricter than stated, a hyperparameter the paper compressed). These earn `findings:` entries with `path:line` evidence anchors against the code (when an output corresponds), or `implementation-notes.md` bullets (when no formal output corresponds).

3. **Decision-option augmentation.** Where the code reveals an option the paper didn't mention but is defensible (a sibling implementation alternative used in the codebase or referenced in a comment), add it as a sibling option to the relevant `decisions:` entry. Do not pre-emptively author every code variant; only the ones that bear on a real choice.

4. **Surface paper-vs-code material disagreements** to `<paper-slug>/open-questions.md` (sub-agent) or via `AskUserQuestion` (interactive) per the canonical-resolution rule above. The verbatim paper quote + the `path:line` code anchor + the plausible-impact one-liner should both make it into the open-questions entry so the user sees enough to decide at REVIEW (close-out).

### Pass C — rigor-dialed self-review

After the paper + code passes land for a sub-analysis, a fresh-context sub-agent cross-checks: are the decisions covering everything material? Are the evidence quotes verbatim? Are the findings actually traceable to the paper or code? Did any material disagreement get silently dropped?

Self-review depth follows the constitution's frugality / rigor dial — same shape as ARCHITECT's review pass and IMPLEMENT's:

- **Frugal:** skip self-review, or run a single fresh sub-agent pass and incorporate its fixes once.
- **Rigor:** N rounds — each round runs a fresh reviewer; fixes are incorporated; the next round runs another fresh reviewer that has not seen the fixes. Iterate until two consecutive rounds find no fixes (the strong-termination criterion the loop already uses), or a 5-round system cap. Each round runs a brand-new sub-agent that does NOT see prior rounds' findings or fixes — pattern-matching on prior fixes defeats the cross-check.

#### Per-round fresh sub-agent — system prompt

> You are a SPECIFY reviewer for one sub-analysis. Read the relevant slice of `astra.yaml`, the paper, and the code (when present), and report any inconsistencies you find. You will be one of several independent reviewers; do not assume anything has already been fixed.
>
> ### Inputs
>
> - `astra.yaml` — focus on `analyses.<sub-analysis-id>` (`decisions:`, `prior_insights:`, `findings:`, `narrative:`, `inputs:`, `outputs:`)
> - `universes/baseline.yaml`
> - `implementation-notes.md`
> - `work/notes/architect/paper-index.md` — the decision clusters and result loci that scoped the work
> - `work/notes/architect/code-index.md` (when code present)
> - `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — paper text (Grep into; do not re-read whole)
> - `work/reference/code/` (when present) — canonical reference for numerics + method
> - `work/notes/literature.yaml` (if present) — for evidence verification
>
> ### What to check
>
> 1. **Decision coverage.** Does this sub-analysis's `decisions:` block cover every choice in the paper-side index's decision clusters? Cosmetic / pure-tooling choices should NOT be decisions; anything material that's missing should be added.
> 2. **Decision options.** Each decision has the chosen option plus any sibling alternatives the paper discusses or the code reveals. The chosen option's `rationale:` is grounded in the paper's stated reasoning (or the code's, where canonical-resolution applied).
> 3. **Evidence verification.** Every `evidence:` block uses `TextQuoteSelector` with a verbatim `exact:` quote, real surrounding-text `prefix:` / `suffix:`, and a real page or section anchor. Quotes that are paraphrased or whose prefix / suffix are editorial parentheticals will fail `--verify-evidence`. Run `astra validate astra.yaml --verify-evidence` when literature.yaml is present.
> 4. **Findings traceability.** Each `findings:` entry's `evidence:` resolves to a real paper claim (verbatim quote + source anchor) or a real code location (`path:line`).
> 5. **Material-disagreement surfacing.** Where paper and code disagree on a material choice, the spec records both options under the relevant `decisions:` entry. `universes/baseline.yaml` selects the code's option (canonical-resolution default), unless an interactive seam recorded a different user choice. Flag any material disagreement that got silently dropped or where the spec picked the paper without an explicit user override.
> 6. **Narrative anchors.** The sub-analysis's `narrative:` weaves `astra-anchor:` references to the new `decisions:` / `prior_insights:` / `findings:` entries — the tree-path grammar must be valid, and entries actually exist at the referenced paths.
> 7. **`narrative:` voice fidelity.** Hedges and qualifiers from the paper survive (per the narrative skill's discipline). Editorial commentary added beyond what the paper supports gets flagged.
> 8. **No synthetic data.** Unless the paper itself uses synthetic data, every input has a real acquisition source — no mock / synthetic substitutes anywhere in the sub-analysis's inputs, decisions, or implementation-notes.
>
> ### What NOT to do
>
> - **Do not edit `astra.yaml`** or any other file. Your output is a findings file; a SPECIFY-fix pass responds to the findings. Editing here defeats the multi-round-fresh-context discipline.
> - **Do not flag missing `recipes:`.** Recipes are IMPLEMENT's, not SPECIFY's.
> - **Do not re-read the entire paper.** Use Grep on `work/reference/source/` (or `document.md`) for the specific claims you want to verify; lean on `work/notes/architect/paper-index.md`.
> - **Do not invent problems.** If the sub-analysis is consistent with paper + code, say so briefly.
> - **Do not assume a prior reviewer has been here.** You are fresh. First-principles read only.
>
> ### Output format — `work/notes/specify-review/<sub-analysis>-round-<N>.md`
>
> ```markdown
> # Specify-review round <N> — <sub-analysis-id>
>
> Reviewer ran fresh against astra.yaml's <sub-analysis-id> slice, paper, and code.
>
> ## Findings
>
> ### <category — e.g. "Decision coverage" / "Evidence" / "Material disagreement">
>
> - **<one-line finding>**
>   - **What's wrong**: <quote or location of the spec problem>
>   - **Where to fix**: <`astra.yaml#analyses.<sub-id>.path/to/key` or `implementation-notes.md`>
>   - **Suggested fix**: <one-line concrete change>
>   - **Source**: <paper §X.Y "quote" + index row, or code `path:line`>
>
> ## Verdict
>
> - **fixes_needed**: <count>
> - **clean** | **needs-fixes**
> ```

#### SPECIFY-fix pass between rounds

After each round's findings file lands, a SPECIFY-fix pass (or the orchestrator inline for trivial mechanical fixes) edits `astra.yaml` for the sub-analysis, plus `universes/baseline.yaml` and `implementation-notes.md` per the suggested fixes. After any change to `astra.yaml`:

```bash
astra validate astra.yaml
astra validate astra.yaml --verify-evidence  # when literature.yaml exists
```

#### Termination

- `weak` (frugal): one pass per sub-analysis. Done after fixes (or immediately, if `fixes_needed` was 0).
- `strong` (rigor):
  - If round N's `fixes_needed` was 0 AND round (N-1)'s was also 0 → done.
  - If round N is the first round (N=1), spawn round 2 unconditionally so we can compare.
  - If round N produced fixes, spawn round (N+1) as a fresh sub-agent that does not see round N's findings or the fixes.
  - If N hits the system cap of 5 rounds without two consecutive clean rounds, surface to the user via `AskUserQuestion`: "SPECIFY review for <sub-analysis-id> reached round cap with N fixes still landing; continue, accept the current spec, or revise the constitution?" Default on user silence: accept the current sub-analysis spec, log the unfinished tail in `<paper-slug>/open-questions.md`, and proceed.

When all sub-analyses' reviews terminate, SPECIFY produces the final outputs:

## Target-ledger output

After every sub-analysis is filled and self-reviewed, write `targets/targets.md` as a small ledger COMPARE consumes. Only an index, not a derivation of the spec; the depth lives in `astra.yaml`. For each `outputs:` entry across all sub-analyses (already declared by ARCHITECT), a brief entry:

- What it is (one line); the reference file's path (relative to `targets/` when the file is copied into `targets/`, or pointing at `work/reference/figures/...` when not)
- Type: `metric` | `figure` | `table`
- Priority: `primary` | `secondary` (from ARCHITECT's tagging)
- Expected value / trend (paper-side); how to judge a match (numerical tolerance for metrics; shape / axis ranges / key features for figures; specific values for tables)
- Spec home: which `analyses.<sub-id>.outputs.<output-id>` entry in `astra.yaml` this target maps to, so COMPARE can find the reproduced result at `results/<universe>/<output_id>/`

Copy reference figure / table files from `work/reference/` into `targets/` so COMPARE has a self-contained reference set. For Path A, files are in `work/reference/source/` (extract by `\includegraphics{}` filename); for Path B, in `work/reference/figures/` / `work/reference/tables/`.

Out-of-scope targets stay in `targets/targets.md` with an explicit reason and should not be forced into the spec.

---

## Other rules

- **Do NOT add executable implementation code or invented run commands.** Do add concise provenance / recipe descriptions where ASTRA fields support them, especially for paper-derived calculations, figure generation, imported constants, and values that IMPLEMENT will need to regenerate.
- **Equation and section numbers must match the rendered paper / PDF**, not a naïve count of TeX blocks or markdown headings. When citing "eq. N" or "§N", find the equation or heading by content in the rendered paper and use the printed number.
- **Validate** with `astra validate astra.yaml` after each pass.
- **Work primarily from `work/notes/architect/`** — the index files distilled the relevant scope per sub-analysis. Use `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) only to look up specific details (Grep for terms, or read targeted sections with offset/limit). Do not re-read the whole paper.
- **The narrative skill is the prose author, not the structure author.** SPECIFY weaves anchors into the prose ARCHITECT wrote — the structural surface is fixed, the anchored references are SPECIFY's contribution.

## Survey signals (entry into SPECIFY)

- `astra.yaml` exists with stub form (sub-analyses + inputs + outputs + narrative; empty decisions / prior_insights / findings) ⇒ ready to specify
- For each sub-analysis: `decisions:` / `findings:` populated AND, if literature.yaml exists, `prior_insights:` populated ⇒ paper pass done
- For each sub-analysis: when `work/reference/code/` exists, code-pass material-disagreement entries land in `decisions:` (with both options) and `universes/baseline.yaml` selects the canonical-resolution choice; `implementation-notes.md` carries non-material gotchas ⇒ code pass done
- For frugal: each sub-analysis has at least a `work/notes/specify-review/<sub>-round-1.md` with verdict `clean` (or no fixes were incorporated) ⇒ SPECIFY review done
- For rigor: each sub-analysis has two consecutive `<sub>-round-<N>.md` files with verdict `clean` ⇒ SPECIFY review done
- `astra validate astra.yaml --verify-evidence` returns clean (when literature.yaml exists) ⇒ evidence side validated
- `targets/targets.md` exists with each entry mapped to a spec home ⇒ target-ledger done
- `implementation-notes.md` exists ⇒ practical-guidance side done
- All of the above ⇒ SPECIFY complete; proceed to IMPLEMENT

## Notes

- **Material conflicts that the user explicitly defers** are appended to `<paper-slug>/open-questions.md` (the running report read at session boundaries). The next iteration sees them and either re-surfaces them or notes their continued deferral; the user resolves at REVIEW (close-out).
- **The narrative skill is the prose author, not the structure author.** SPECIFY's job is content correctness; `/narrative` invocation comes during the paper pass when authoring or extending the narrative prose to weave in anchor references.
- **The target ledger is a derivation, not a separate phase's output.** Treat `targets/targets.md` as a small index produced alongside the filled `astra.yaml`, not a heavyweight artifact. The depth lives in `astra.yaml`'s `outputs:` / `findings:` / `decisions:`.
- **Two-pass discipline is the cross-check.** Skipping the code pass (when code exists) loses the canonical-resolution surface and lets paper-vs-code material disagreements slip through. The fresh-context self-review can recover *some* of these but not all — the disciplined sequence (paper → code → self-review) catches more.
- **Per-sub-analysis parallelism is opt-in.** When sub-analyses are independent (no shared decision blocks, no cross-sub-analysis findings), spawn one Task-tool sub-agent per sub-analysis to run its passes in parallel. When they share material decisions or findings (rare), serialize.
