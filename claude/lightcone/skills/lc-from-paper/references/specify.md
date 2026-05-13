# SPECIFY — fill the stub `astra.yaml`, two passes per sub-analysis

Read the stub `astra.yaml` from ARCHITECT and fill in `decisions:`, `prior_insights:`, `findings:` per sub-analysis, weaving the existing narrative with `astra-anchor:` references as entries land. SPECIFY is the **first material-disagreement seam** — paper-vs-code conflicts surface here, and they're often the highest-value moments for the user to weigh in on at REVIEW.

SPECIFY is what a ralph iteration does when the workdir signals "stub `astra.yaml` present + sub-analyses' `decisions:` / `prior_insights:` / `findings:` blocks still empty." Iterations run detached in tmux; the user isn't reachable interactively, so the canonical-resolution default (code wins where paper and code disagree on a material choice) applies and disagreements are logged to CLAUDE.md's **Paper-vs-code disagreements** section plus `open-questions.md` for REVIEW close-out.

The structure runs **two passes per sub-analysis** (paper, then code, when code exists), then iteration-boundary review. The two passes are the cross-check: the paper pass authors what the paper says; the code pass surfaces where the code says something different; the difference is gold (it's where the reproduction has to make a decision).

Per-sub-analysis work is parallelizable when sub-analyses are independent. Each sub-analysis's two passes (paper, then code) run sequentially within that sub-analysis; across sub-analyses the iteration can fan out parallel work as one-level-deep sub-agents from inside its main session. When SPECIFY needs paper- or code-side context, Grep into `work/reference/source/` / `document.md` for paper text or read targeted modules under `work/reference/code/`; the structural index at `work/reference/index.json` and the code inventory at `work/reference/code-index.md` give you the orientation to know where to look. Don't try to absorb the paper or code whole.

## Inputs

- `astra.yaml` — the stub from ARCHITECT (sub-analyses, inputs, outputs, narrative; empty `decisions:` / `prior_insights:` / `findings:` blocks)
- `constitution.md` — Goal (scope), Fidelity intent, Quality bar
- `CLAUDE.md` — Rules; **Paper-vs-code disagreements** for prior-iteration entries
- `work/reference/index.json` — paper-extraction's structural index: figures, tables, section outline, citations. The `citations:` block maps each cited paper's BibTeX key (Path A) or synthetic `<lastname>_<year>` key (Path B) to `{locations, citation, doi}`. SPECIFY uses this to write each `prior_insights:` placeholder's `doi:` so LITERATURE knows which paper to fetch.
- `work/reference/code-index.md` (when code present) — code inventory: module map, candidate decisions with file:line, entry-points, data dependencies, gotchas.
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — paper text. Grep into for specific facts; read targeted spans by offset/limit when you need more context. Don't re-read whole.
- `work/reference/figures/`, `work/reference/tables/`, `work/reference/metadata.json` — extracted artifacts (Path B only)
- `work/reference/code/` (if present) — original code, canonical reference for numerics + method. Read the modules that `code-index.md` points at for the sub-analysis you're filling.
- `work/notes/notes.md` — user-supplied context (read by every iteration if present)

## Outputs

- `astra.yaml` — **filled form**: each sub-analysis's `decisions:` populated with decision-level `rationale:` prose plus options (the paper's choice is identified by `default:`); `findings:` populated as full `Insight` blocks with paper-anchored `evidence:` (the target paper's DOI + `quote: {exact, prefix, suffix}` + `location: {page: N}`); `prior_insights:` populated as citation **placeholders** — each a syntactically-complete `Insight` (`id`, `claim`, `created_at`, `evidence: [{id, doi}]`) whose placeholder Evidence carries the cited paper's DOI looked up from `work/reference/index.json#citations[<cite-key>].doi` **but no `quote:` selector yet** — LITERATURE fills those in. Each option that draws on a placeholder cites it via `Option.insights: [<insight_id>, ...]` (the back-reference that links options to prior_insights in the ASTRA grammar). `narrative:` keys updated to weave `astra-anchor:` references into prose as entries land. `astra validate astra.yaml` returns clean (Evidence with `doi:` and no `quote:` is structurally valid at this stage); `astra validate astra.yaml --verify-evidence` runs after LITERATURE has authored the quotes.
- `universes/baseline.yaml` — selects the paper's choices (where paper and code disagree per the canonical-resolution rule, see "Material conflicts" below)
- `implementation-notes.md` — concise practical guidance for the IMPLEMENT phase: tricky algorithms, numerical gotchas, data-format quirks, things the spec can't capture. Bullets, not essays.
- `targets/targets.md` — small target ledger COMPARE consumes: per output (already declared by ARCHITECT), a brief entry with type, priority, paper value, expected match criteria, and the path to the reference figure / table / metric (when applicable, copy the reference file into `targets/` so the directory is self-contained)
- `CLAUDE.md` updates — append entries to **Paper-vs-code disagreements** for each material conflict surfaced
- `constitution.md` updates — Open dimensions when something material warrants user ratification at REVIEW

## Substrate skills to invoke

- **`/narrative`** — narrative authoring (any of the five `narrative.{summary,inputs,methods,findings,outputs}` keys, plus decision `rationale:` fields) is owned by the narrative skill. Invoke it during the **paper pass** when authoring or extending narrative prose. The narrative skill teaches reserved entity names, the tree-path anchor grammar, the conditional-narrative requirement (which keys are required when), the five-key authoring order, paper-reproduction fidelity discipline, and the new downstream-consumer discipline (lightcone-cli#108). Do not duplicate that content.

Your responsibility in this phase is the **content**: build out the `decisions:` / `prior_insights:` / `findings:` for each sub-analysis (each with its own evidence shape — detailed below), and weave `astra-anchor:` references back into the narrative as entries land. ARCHITECT already settled the structure.

## The two-pass-per-sub-analysis structure

For each sub-analysis (parallelizable across independent sub-analyses):

### Pass A — paper pass

Read the paper's section(s) covering this sub-analysis. Author:

1. **`decisions:`** — every choice in this sub-analysis where a different defensible option could plausibly shift a numerical result: algorithmic methods, thresholds, statistical approaches, data selection criteria, calibration choices. Use `when`, `incompatible_with`, and `requires` constraints for non-independent decisions.

   For each decision, the paper-pass authors:
   - **Decision-level fields:** `label:` (short human-readable name), `rationale:` (the paper's stated reasoning — use `/narrative` for the prose), `default:` (the option the paper actually selects), and `options:` (the map of option entries below).
   - **Options:** the chosen option plus any sibling alternatives the paper discusses. Each option carries `label:` (required) and an optional `description:`. Per the 0.0.10 grammar, options do **not** carry their own `rationale:` or `evidence:` block — the decision's `rationale:` covers the reasoning; paper-text evidence flows through `findings:` (for the paper's own quantitative claims) or via `Option.insights` back-references into `prior_insights:` (for citation-backed support).
   - **Option ↔ prior_insights linkage:** when the option's support derives from cited literature, list the relevant `prior_insights:` ids in `Option.insights: [<insight_id>, ...]`. The placeholder block under `prior_insights:` (authored in step 2 below) is the back-end of this link — LITERATURE fills in the verbatim cited-paper quote later. **Scope rules** (astra-tools ≥ 0.2.9): bare ids resolve **node-locally only** — the prior_insight must be declared in the same sub-analysis as the option. For a citation declared at an ancestor scope, use explicit upward refs: `[../id]` for the parent, `[../../id]` for the grandparent, etc. (same `../` grammar as `Input.from` and `Decision.from`). The natural shape — declare each cited paper at the sub-analysis that uses it, reference with a bare id from same-scope options — keeps everything node-local and needs no `../`.

   Read `.claude/guides/decision-guide.md` (in lightcone-cli's plugin bundle) for the full definition of what counts. **Only exclude pure tooling choices** (language, library, file format) and fixed constraints. A typical sub-analysis has 2–6 decisions; if a sub-analysis has fewer than 2, revisit `work/reference/index.json` and reconsider.

   ```yaml
   decisions:
     <decision_id>:
       label: "<short human-readable name>"
       rationale: "<the paper's stated reasoning, weaving astra-anchors into prose>"
       default: <chosen_option_id>
       options:
         <option_id>:
           label: "<short name>"
           description: "<optional longer description>"
           insights: [<prior_insight_id>, ...]   # back-refs to prior_insights this option draws on
   ```

2. **`prior_insights:`** — for every `\cite{<key>}` (Path A) or rendered citation invocation (Path B) the paper invokes that bears on a decision in this sub-analysis, record a **placeholder**. The placeholder is a syntactically-complete `Insight` (`id`, `claim`, `created_at`, `evidence`) whose `evidence` array contains a single Evidence entry carrying the cited paper's `doi` but **no `quote:` selector** — LITERATURE fetches the cited paper, finds the supporting quote, and writes the resolved `quote: {exact, prefix, suffix}` (+ `location: {page: N}`) onto that Evidence entry. The decision↔insight linkage is the back-reference on the option (`Option.insights`, step 1 above), not a forward link on the insight. The placeholder shape:

   ```yaml
   prior_insights:
     <insight_id>:
       id: <insight_id>
       claim: "<what the cited paper supports about the decision>"
       created_at: "<SPECIFY-iteration ISO-8601 timestamp, e.g. 2026-05-11T09:00:00Z>"
       evidence:
         - id: <evidence_id>
           doi: "<DOI from work/reference/index.json#citations[<cite-key>].doi>"
           # quote: omitted at SPECIFY time — LITERATURE fills the TextQuoteSelector in
   ```

   Evidence with `doi:` and no `quote:` is structurally valid in 0.0.10 (`quote:` is optional on Evidence); the placeholder passes `astra validate` and waits for LITERATURE to fill the quote. `astra validate --verify-evidence` should only be run after LITERATURE has resolved every placeholder.

   When the citation's DOI is unresolved (`citations[<key>].doi: null` — flagged in `extraction_warnings`), the placeholder still needs a `doi:` (Evidence requires exactly one of `doi` or `artifact`). In that case, omit the Evidence entry entirely or fall back to an artifact reference if the gap will be resolved internally — and log the unresolved citation to `open-questions.md` so the user can supply the DOI at REVIEW close-out. Don't pre-emptively fetch the cited paper or guess its content; LITERATURE does that with fresh context per paper.

3. **`findings:`** — paper-level claims and quantitative results scoped to this sub-analysis. Each is a full `Insight` (`id`, `claim`, `created_at`, `evidence`) with at least one paper-anchored Evidence entry: `doi:` of the target paper itself + a verbatim `quote: {exact, prefix, suffix}` (TextQuoteSelector) + a `location: {page: N}` (FragmentSelector, page from the rendered PDF). For findings tied to a specific declared output, the Evidence may use `artifact: <output_id>` instead of (or in addition to) the DOI-based quote. Pull the verbatim claims for each output's expected value from the paper text + the result loci in `work/reference/index.json`.

   ```yaml
   findings:
     <finding_id>:
       id: <finding_id>
       claim: "<the paper's quantitative claim, 1–2 sentences>"
       created_at: "<ISO-8601 timestamp>"
       evidence:
         - id: <evidence_id>
           doi: "<target paper's DOI>"
           quote:
             exact: "<verbatim quote from the paper>"
             prefix: "<~20–100 chars BEFORE the quote, real surrounding text>"
             suffix: "<~20–100 chars AFTER the quote, real surrounding text>"
           location: { page: <N> }
   ```

4. **Weave `astra-anchor:` references into the existing narrative.** ARCHITECT wrote `narrative:` prose without anchors because the entries didn't exist. Now they do — extend the narrative to point at the new `decisions:` / `prior_insights:` / `findings:` entries via the tree-path anchor grammar. Use `/narrative` for this pass; it carries the discipline.

5. **Verify finding quotes against the paper source by Grep.** For each `findings:` Evidence entry with a `quote:`, Grep the paper source to confirm the `exact:` text is verbatim and the `prefix:` / `suffix:` are real surrounding text. `astra validate --verify-evidence` will run the deterministic check across every quote later (after LITERATURE resolves the `prior_insights:` placeholders); a manual Grep now catches typos and paraphrases before the code pass.

### Pass B — code pass (when `work/reference/code/` exists)

Read the code that implements this sub-analysis (`work/reference/code-index.md`'s natural-decomposition rows point at the relevant modules / scripts). Augment / amend:

1. **Code-as-canonical material disagreements.** For each decision authored in the paper pass, locate its implementation in the code. Where paper and code disagree:
   - **Material** = a different choice would plausibly change a numeric result the paper reports.
   - **Stylistic / cosmetic / pure-tooling** = not material; record in `implementation-notes.md` and move on.

   For **material** disagreements: take **code as canonical** per the canonical-resolution rule (the iteration runs detached; the user isn't reachable interactively). Append the conflict to CLAUDE.md's **Paper-vs-code disagreements** section AND to `open-questions.md` so the user sees it at REVIEW close-out, with the verbatim paper quote + the `path:line` code anchor + a plausible-impact one-liner ("changes the BAO peak amplitude by ~5%"). Let `universes/baseline.yaml` select the code's method. Preserve both options in the `astra.yaml` `decisions:` entry; the user can flip the baseline at REVIEW close-out.

2. **Code-revealed insights and findings.** Things the code does that the paper doesn't describe (a calibration version, a cut stricter than stated, a hyperparameter the paper compressed). These earn `findings:` entries with Evidence using `artifact: <output_id>` (referencing a declared output) plus an optional `source_commit:` (the git SHA that produced it). When the insight isn't tied to a formal output, drop it into `implementation-notes.md` as a bullet rather than synthesizing a degenerate finding.

3. **Decision-option augmentation.** Where the code reveals an option the paper didn't mention but is defensible (a sibling implementation alternative used in the codebase or referenced in a comment), add it as a sibling option to the relevant `decisions:` entry. Do not pre-emptively author every code variant; only the ones that bear on a real choice.

### Reviewing prior SPECIFY work as part of survey

There is no separate review phase. Every iteration that enters and finds a SPECIFY-filled sub-analysis on disk reads it critically before doing anything else. If you see real issues — missing decision, paraphrased quote, dropped disagreement, broken anchor — fix them inline, commit (`specify: fix <sub-analysis-id> <what>`), and exit. When a fresh-context read finds nothing to fix in a sub-analysis, the iteration moves on (next sub-analysis, or next phase if every sub-analysis is clean).

The cross-check questions on entry: are the decisions covering everything material? Are the evidence quotes verbatim? Are the findings actually traceable to the paper or code? Did any material disagreement get silently dropped?

#### What to check

1. **Decision coverage.** Does this sub-analysis's `decisions:` block cover every choice in the paper-side index's decision clusters? Cosmetic / pure-tooling choices should NOT be decisions; anything material that's missing should be added.
2. **Decision options.** Each decision has the option the paper selects (named in `default:`) plus any sibling alternatives the paper discusses or the code reveals. The decision-level `rationale:` is grounded in the paper's stated reasoning (or the code's, where canonical-resolution applied). Per the 0.0.10 grammar, options do not carry per-option `rationale:` or `evidence:`; cited support is back-referenced via `Option.insights` into a `prior_insights:` entry.
3. **Evidence verification.** Every `findings:` Evidence entry uses `TextQuoteSelector` with a verbatim `exact:` quote, real surrounding-text `prefix:` / `suffix:`, and a `location: {page: N}` (1-indexed). Quotes that are paraphrased or whose `prefix:` / `suffix:` are editorial parentheticals will fail `--verify-evidence`. `prior_insights:` placeholders intentionally have `evidence: [{id, doi}]` without a `quote:` at this stage — LITERATURE authors the quotes — so do not flag a missing quote on placeholder entries. After LITERATURE resolves the placeholders, run `astra validate astra.yaml --verify-evidence`.
4. **Findings traceability.** Each `findings:` Insight's `evidence:` resolves either to a real paper claim (target-paper DOI + verbatim `quote:` + page) or to a real declared output via `artifact: <output_id>` (with optional `source_commit:` and `snapshot:`).
5. **Material-disagreement surfacing.** Where paper and code disagree on a material choice, the spec records both options under the relevant `decisions:` entry, `universes/baseline.yaml` selects the code's option (canonical-resolution default), and the conflict is appended to CLAUDE.md's *Paper-vs-code disagreements* section plus `open-questions.md` for the user to resolve at REVIEW close-out. Flag any material disagreement that got silently dropped, that didn't make it into the disagreements log, or where the baseline picked the paper without the canonical-resolution rule applying.
6. **Narrative anchors.** The sub-analysis's `narrative:` weaves `astra-anchor:` references to the new `decisions:` / `prior_insights:` / `findings:` entries — the tree-path grammar must be valid, and entries actually exist at the referenced paths.
7. **`narrative:` voice fidelity.** Hedges and qualifiers from the paper survive (per the narrative skill's discipline). Editorial commentary added beyond what the paper supports gets flagged.
8. **No synthetic data.** Unless the paper itself uses synthetic data, every input has a real acquisition source — no mock / synthetic substitutes anywhere in the sub-analysis's inputs, decisions, or implementation-notes.

Apply fixes inline as you find them — `astra.yaml`, `universes/baseline.yaml`, `implementation-notes.md`, the disagreements log in CLAUDE.md as needed. The diff against the prior commit is the record of what changed. After any change to `astra.yaml`:

```bash
astra validate astra.yaml
astra validate astra.yaml --verify-evidence  # after LITERATURE has resolved the prior_insights placeholders
```

Commit the diff (`specify: fix <sub-analysis-id> <what>`) and exit.

#### What NOT to do

- **Don't flag missing `recipes:`.** Recipes are IMPLEMENT's, not SPECIFY's.
- **Don't re-read the entire paper.** Use Grep on `work/reference/source/` (or `document.md`) for the specific claims you want to verify; lean on `work/reference/index.json`.
- **Don't declare the sub-analysis done in the iteration where you landed fixes.** The next fresh-context iteration reads it cold; if nothing needs fixing, it moves on, which is the "done" signal.

When every sub-analysis is clean and the SPECIFY-final outputs (target ledger, baseline universe, implementation-notes) are in place, SPECIFY produces its final artifacts:

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
- **Targeted reads, not whole-paper absorption.** Use `work/reference/index.json` and `work/reference/code-index.md` for structural lookups; Grep into `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) for specific verbatim quotes; read targeted code modules under `work/reference/code/` for canonical method details. Don't re-read the whole paper or whole code base.
- **The narrative skill is the prose author, not the structure author.** SPECIFY weaves anchors into the prose ARCHITECT wrote — the structural surface is fixed, the anchored references are SPECIFY's contribution.

## Survey signals (entry into SPECIFY)

- `astra.yaml` exists with stub form (sub-analyses + inputs + outputs + narrative; empty decisions / prior_insights / findings) ⇒ ready to specify
- For each sub-analysis: `decisions:` populated with decision-level `rationale:` + options (paper's choice at `default:`); `findings:` populated as full Insight blocks with paper-anchored Evidence (DOI + `quote: {exact, prefix, suffix}` + `location: {page}`); `prior_insights:` populated as citation placeholders (`id`, `claim`, `created_at`, `evidence: [{id, doi}]` with `quote:` omitted — LITERATURE fills the quotes next); `Option.insights` back-references wired up where options draw on placeholders ⇒ paper pass done
- For each sub-analysis: when `work/reference/code/` exists, code-pass material-disagreement entries land in `decisions:` (with both options) and `universes/baseline.yaml` selects the canonical-resolution choice; `implementation-notes.md` carries non-material gotchas ⇒ code pass done
- For each sub-analysis: a fresh-context iteration reads the slice and finds nothing to fix ⇒ that sub-analysis is done; the next iteration moves on
- `astra validate astra.yaml` returns clean (placeholders whose Evidence carries `doi:` without `quote:` are valid at this stage) ⇒ structural side validated; `--verify-evidence` waits until LITERATURE has authored the `quote:` + `location:` selectors
- `targets/targets.md` exists with each entry mapped to a spec home ⇒ target-ledger done
- `implementation-notes.md` exists ⇒ practical-guidance side done
- All of the above ⇒ SPECIFY complete; proceed to IMPLEMENT

## Notes

- **Material disagreements** are appended to CLAUDE.md's **Paper-vs-code disagreements** section AND `open-questions.md`. CLAUDE.md is the at-a-glance summary every iteration sees; `open-questions.md` is the user-resolution accumulator. Both lead to the same place: the user resolves at REVIEW close-out.
- **The narrative skill is the prose author, not the structure author.** SPECIFY's job is content correctness; `/narrative` invocation comes during the paper pass when authoring or extending the narrative prose to weave in anchor references.
- **The target ledger is a derivation, not a separate phase's output.** Treat `targets/targets.md` as a small index produced alongside the filled `astra.yaml`, not a heavyweight artifact. The depth lives in `astra.yaml`'s `outputs:` / `findings:` / `decisions:`.
- **Two-pass discipline is the cross-check.** Skipping the code pass (when code exists) loses the canonical-resolution surface and lets paper-vs-code material disagreements slip through. The fresh-context review can recover *some* of these but not all — the disciplined sequence (paper → code → review) catches more.
- **Per-sub-analysis parallelism is opt-in.** When sub-analyses are independent (no shared decision blocks, no cross-sub-analysis findings), the iteration can fan out one-level-deep sub-agents (one per sub-analysis from inside its main session) to run their passes in parallel. When they share material decisions or findings (rare), serialize across iterations.
- **Commit per sub-analysis as it lands.** Each sub-analysis's filled-in `astra.yaml` slice + its targets/implementation-notes/baseline updates earn one commit; subsequent fix passes commit separately. The next iteration reads `git log` to track progress; small commits keep the trail readable.
