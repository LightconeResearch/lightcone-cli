# SPECIFY — author the ASTRA spec (and formalize the targets)

Read the paper and accumulated notes; produce the structured ASTRA spec, the baseline universe, the implementation notes, and the small target ledger COMPARE consumes. SPECIFY is the **first user-ratification seam** — material paper-vs-code conflicts surface here, target-formalization happens here, and the default mode is interactive so the user can ratify.

The constitution's per-phase mode defaults to **interactive** for this phase, but the user can flip it. When SPECIFY runs as a sub-agent, it falls back to the canonical-resolution rule (code wins where paper and code disagree) and surfaces unresolved conflicts to `<paper-slug>/open-questions.md`.

## Inputs

- `work/notes/methodology.md` — consolidated decision map, results inventory, data sources (from STUDY)
- `work/notes/study/<NN>-<slug>.md` — per-section paper-vs-code agreement-check files (the source of truth for evidence quotes and code locations; methodology.md points back to these)
- `work/notes/literature.yaml` (if present) — prior insights with evidence quotes and decision links (from LITERATURE)
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — paper text (Grep into; do not re-read whole)
- `work/reference/figures/`, `work/reference/tables/`, `work/reference/metadata.json` — extracted artifacts (Path B only; Path A keeps figures inside the source tarball)
- `work/reference/code/` (if present) — original code, canonical reference for numerics + method
- The per-paper constitution — names the user's intended replication targets (figures, tables, numbers) in its **Desired State**; SPECIFY formalizes them
- `work/notes/notes.md` — user-supplied context (read by every phase if present)

## Outputs

1. **`astra.yaml`** — the full ASTRA specification, with every replication target placed into its appropriate ASTRA home (see "Target formalization" below)
2. **`universes/baseline.yaml`** — exactly the paper's choices (where paper and code disagree, see "Material conflicts" below)
3. **`implementation-notes.md`** — concise practical guidance for the IMPLEMENT phase: tricky algorithms, numerical gotchas, data-format quirks, things the spec can't capture. Bullets, not essays.
4. **`targets/targets.md`** — small target ledger COMPARE consumes; for each target a brief entry with type, priority, paper value, expected match criteria, and the path to the reference figure/table/metric (when applicable, copy the reference file into `targets/` so the directory is self-contained)

## Substrate skills to invoke

- **`/narrative`** — narrative authoring (any of the five `narrative.{summary,inputs,methods,findings,outputs}` keys, plus decision `rationale:` fields) is owned by the narrative skill. Invoke it when authoring the prose. The narrative skill teaches reserved entity names, the tree-path anchor grammar, the conditional-narrative requirement (which keys are required when), the five-key authoring order, paper-reproduction fidelity discipline, and the new downstream-consumer discipline (lightcone-cli#108). Do not duplicate that content.

Your responsibility in this phase is the **structure**: build a spec whose entities are narrative-ready (human-readable labels, no ID collisions with reserved names, sub-analysis IDs as noun phrases) so `/narrative` can author cleanly downstream.

## Decisions

The notes identify many candidate decisions. Include every choice where a different defensible option could plausibly shift a numerical result — algorithmic methods, thresholds, statistical approaches, data selection criteria, calibration choices.

Read `.claude/guides/decision-guide.md` (in lightcone-cli's plugin bundle) for the full definition of what counts. **Only exclude pure tooling choices** (language, library, file format) and fixed constraints. Use `when`, `incompatible_with`, and `requires` constraints for non-independent decisions. A typical analysis has 8–20 decisions; if you have fewer than 5, revisit `methodology.md` and reconsider what you excluded.

## Prior insights from literature

If `work/notes/literature.yaml` exists, incorporate its `prior_insights` into `astra.yaml`. Use the `decision_links` mapping to attach each insight to the relevant decision options, so the multiverse captures evidence-backed alternative choices from the literature.

## Target formalization

There is no separate target-extraction phase — the targets the user named in INTERVIEW (recorded in the constitution's **Desired State**) get formalized into `astra.yaml`'s structure here. The work has two layers:

**Layer 1 — place each target into its ASTRA home.** Targets are coverage obligations, not necessarily outputs. Map each target to the right ASTRA home:

- **Figures, tables, equations-as-artifacts, generated data products** → `outputs`
- **Paper-level claims and quantitative results** → `findings` with source-anchored evidence
- **Constants and configuration values** → `inputs`, `decisions`, `universes/baseline.yaml`

The methodology.md "Results inventory" already split primary vs secondary; use that split to set priorities. For each result in the inventory, find the corresponding figure / table / in-text metric (Path A: `\label{}` in the source; Path B: `metadata.json` index) and place it. Read the per-section files in `work/notes/study/` for the verbatim claim quotes — those become the `findings` evidence.

**Layer 2 — write `targets/targets.md` as a small ledger for COMPARE.** Only an index, not a derivation of the spec; the depth lives in `astra.yaml`. For each target, a brief entry:

- What it is (one line); the reference file's path (relative to `targets/` when the file is copied into `targets/`, or pointing at `work/reference/figures/...` when not)
- Type: `metric` | `figure` | `table`
- Priority: `primary` | `secondary` (from the methodology.md split)
- Expected value / trend (paper-side); how to judge a match (numerical tolerance for metrics; shape / axis ranges / key features for figures; specific values for tables)
- Spec home: which `outputs:` entry in `astra.yaml` this target maps to, so COMPARE can find the reproduced result at `results/<universe>/<output_id>/`

Copy reference figure / table files from `work/reference/` into `targets/` so COMPARE has a self-contained reference set. For Path A, files are in `work/reference/source/` (extract by `\includegraphics{}` filename); for Path B, in `work/reference/figures/` / `work/reference/tables/`.

Out-of-scope targets stay in `targets/targets.md` with an explicit reason and should not be forced into the spec. Keep the target ledger's "spec home" pointers specific enough that a later reviewer can tell which claim was discharged where.

---

## Material conflicts — the user-ratification seam

When `methodology.md` or `code-analysis.md` mentions a paper-vs-code disagreement, **classify it before writing**:

- **Material**: a different choice would plausibly change a numeric result the paper reports.
- **Stylistic / cosmetic / pure-tooling**: not material — record in `implementation-notes.md` and move on.

For **material** conflicts, behaviour depends on whether SPECIFY is running interactively:

- **Interactive SPECIFY** (default): pause and surface via `AskUserQuestion`. Present:
  - The paper's stated method (with quote / section reference)
  - The code's actual method (with file / line reference)
  - The plausible impact ("changes the BAO peak amplitude by ~5%")
  - Three options: paper, code, *something else* (custom, with the user's choice spelled out)

- **Sub-agent SPECIFY** (rare; the constitution lists this only when the user explicitly chose it): take **code as canonical** per the canonical-resolution rule, append the conflict to `<paper-slug>/open-questions.md` so the user sees it at the next session boundary, and let `universes/baseline.yaml` select the code's method. The user can flip the baseline at the next interactive seam.

**Default on user silence in interactive SPECIFY is code when `work/reference/code/` exists, otherwise paper.** This is the canonical-resolution rule: where paper and code disagree, code wins for numerics + method. (Older versions of this skill defaulted to paper; the new default reflects what the first-paper test surfaced — the code is what produced the published numbers.)

Either way, the override is preserved in `astra.yaml` as:

- A `decisions:` entry with both options preserved
- The `universes/baseline.yaml` selecting whichever option won (chosen by the user, or canonical default on silence)
- A finding (or an insight if the conflict matters for replication discipline broadly) that records the conflict with quote / line evidence

This makes the override surface in any later review of the spec — *"the paper says X, the code does Y, the user chose Z, here's why."* The fidelity-of-prose side of this (voice seams, hedge preservation, evidence-quote verification) is the `/narrative` skill's job.

---

## Sub-analysis structure

Split into sub-analyses **only if the paper has genuinely independent analysis stages**. Examples:

- A reconstruction stage that produces a catalog consumed by a clustering stage which produces inputs to a BAO fit — three sub-analyses.
- A monolithic analysis that runs end-to-end with no clean intermediate handoff — one analysis.

Sub-analysis IDs should be **noun phrases** (not verb phrases): `reconstruction`, `clustering`, `bao_fit`. Avoid reserved names (`inputs`, `outputs`, `decisions`, `findings`, `prior_insights`, `analyses`, `options`, `content`, `narrative`).

When sub-analyses exist, the root narrative MUST include a top-down end-to-end data-flow paragraph (per the narrative skill's data-flow rules — closes lightcone-cli#108).

## Other rules

- **Do NOT add executable implementation code or invented run commands.** Do add concise provenance / recipe descriptions where ASTRA fields support them, especially for paper-derived calculations, figure generation, imported constants, and values that IMPLEMENT will need to regenerate.
- **Equation and section numbers must match the rendered paper / PDF**, not a naïve count of TeX blocks or markdown headings. When citing "eq. N" or "§N", find the equation or heading by content in the rendered paper and use the printed number.
- **When adding finding evidence**, verify the quoted text against the paper source by Grep or PDF search. `astra validate --verify-evidence` currently verifies `prior_insights` evidence; artifact-anchored `findings` evidence still needs a manual quote check.
- **Validate** with `astra validate astra.yaml` and fix until it passes.
- **Work primarily from `work/notes/`** — SUMMARIZE has already distilled the paper. Use `work/reference/document.md` only to look up specific details (Grep for terms, or read targeted sections with offset/limit). Do not read the entire markdown at once.

## Survey signals (entry into SPECIFY)

- `work/notes/methodology.md` exists ⇒ ready to specify
- `astra.yaml` exists; `astra validate astra.yaml` returns clean ⇒ structural SPECIFY done
- `targets/targets.md` exists with each entry mapped to a spec home ⇒ target-formalization done
- `implementation-notes.md` exists ⇒ practical-guidance side done
- All four ⇒ SPECIFY complete; proceed to REVIEW

## Notes

- **Material conflicts that the user explicitly defers** are appended to `<paper-slug>/open-questions.md` (the running report read at session boundaries). The next iteration sees them and either re-surfaces them or notes their continued deferral; the user resolves at SUMMARIZE_RUN.
- **The narrative skill is the prose author, not the structure author.** SPECIFY's job is structural correctness; `/narrative` invocation comes after the structural skeleton exists.
- **The target ledger is a derivation, not a separate phase's output.** Treat `targets/targets.md` as a small index produced alongside `astra.yaml`, not a heavyweight artifact. The depth lives in `astra.yaml`'s `outputs:` / `findings:` / `decisions:` and in the per-section study files.
