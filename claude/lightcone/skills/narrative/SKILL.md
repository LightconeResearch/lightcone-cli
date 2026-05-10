---
name: narrative
description: >
  Authors prose throughout an `astra.yaml` — analysis-level
  `narrative:` blocks (five fixed keys: `summary`, `findings`,
  `methods`, `inputs`, `outputs`), decision `rationale:` fields, and
  shorter `description:` / `notes:` prose on individual entities. The
  five-key narrative is the most substantive case; the same
  architectural and syntactical frame applies wherever prose appears
  in the spec.
  Always written against an existing `astra.yaml`; what differs
  between modes is the second source paired with the spec — an
  authoritative text (paper reproduction), project artifacts
  (retrofit), or dialogue with the user (co-drafting). Triggers on
  "narrative", "draft the narrative", "narrate this analysis",
  "rationale for this decision", "write the summary", "describe this
  input", or any request for reader-facing prose keyed off an
  `astra.yaml`.
---

# narrative

This skill covers prose authoring across an `astra.yaml`. The prose surfaces are:

- **Analysis `narrative:` blocks** — five keys (`summary`, `inputs`, `methods`, `findings`, `outputs`) on each analysis and sub-analysis.
- **Decision `rationale:` fields** — one paragraph per decision.
- **Per-entity prose** — shorter `description:` / `notes:` on individual inputs, outputs, options, insights.

ASTRA's structural content surfaces alongside the prose in renderers like lightcone-ui. **Prose does not duplicate the structure** — it cites into it. An anchor is a citation; a sentence pointing to a decision is a small argument; prose is the layer where decisions, sub-analyses, findings, and outputs become a connected story.

## Modes

Prose cites the spec's structure (decisions, findings, outputs, sub-analyses) by anchor, so the structure must exist when the prose lands: write the spec first, write both concurrently, or revise narrative after spec changes settle.

There are three modes, distinguished by what's available beyond the spec itself. Every mode draws on the under-construction `astra.yaml`; what differs is the **second source** paired with it.

| Mode | Second source | Status | Reference |
|---|---|---|---|
| **Paper reproduction** | An authoritative text source (paper, thesis, technical report, …) | Ready | [`references/paper-reproduction.md`](references/paper-reproduction.md) |
| **Retrofit** | Project artifacts — code, notebooks, fibers, commit history | Stub | [`references/existing-analysis.md`](references/existing-analysis.md) |
| **Co-drafting** | The user, in conversation | Stub | [`references/co-drafting.md`](references/co-drafting.md) |

If the second source isn't obvious from context, ask: is there an authoritative text (paper, thesis, technical report) to draw from? If not, are we harvesting from existing artifacts, or working from the user's own framing? Hybrid is allowed — a reproduction with co-drafted extensions, a retrofit with co-drafted gap-filling.

The rest of this file is the mode-independent substrate every reference relies on. Read it through, then open the matching reference.

---

## The five keys

| Key | What it carries | Required when |
|---|---|---|
| `summary` | Question, scope, headline shape — the only key without a structural peer. | optional in the schema, but should always exist |
| `inputs` | Provenance — the data the analysis rests on. | `Analysis.inputs` is non-empty |
| `methods` | Pipeline walk; cite each decision and sub-analysis by anchor. | `Analysis.decisions` or `Analysis.analyses` is non-empty |
| `findings` | Synthesis of declared findings; each cited by anchor. | `Analysis.findings` is non-empty |
| `outputs` | Which artifacts were promoted, and where they go downstream. | `Analysis.outputs` is non-empty |

`astra validate` enforces the right column. **Narrate what you declare:** if `findings:` is empty, `narrative.findings` should not appear. A stub analysis with only `summary` is valid.

A decision's `rationale:` is its own one-paragraph slot — what was decided, the insight that motivated it (cite by anchor), and what the load-bearing alternative was and why it lost. The alternatives themselves live in the options structure.

## Length

1–3 paragraphs per key, at any level (root, sub-analysis, decision).

Length is the mechanism that keeps analyses modular, not a style preference. **If references don't fit in three paragraphs, the analysis is too big — split it.** The narrative is a compressor; if it won't compress, split the thing being compressed.

## Anchors

Markdown link syntax with `#`-target, **tree-path-first** — same grammar as decision `from:` references.

| Target | Anchor |
|---|---|
| Input | `#inputs.<id>` |
| Output | `#outputs.<id>` |
| Decision | `#decisions.<id>` |
| Option within a decision | `#decisions.<id>.options.<opt>` |
| Finding | `#findings.<id>` |
| Prior insight | `#prior_insights.<id>` |
| Sub-analysis (whole node) | `#analyses.<sub>` |
| Element inside sub-analysis | `#<sub>.<category>.<id>` |
| Parent scope (from a sub-analysis) | `#../decisions.<id>` |

The sub-analysis form is **sub-analysis first, then category**: `#reconstruction.decisions.algorithm`, not `#decisions.reconstruction.algorithm`. References resolve relative to the hosting analysis; use `../` to escape to parent scope.

Rules:

- Anchor text is **authored prose**, not the raw id.
- Inline references do the work of a citation; don't footnote or parenthesize.
- One reference per idea. Stacking three on a sentence means the sentence carries too much.
- Prior insights motivate decision options via `decisions.<id>.options.<opt>.insights:`. Findings cannot appear there (validator-enforced); if a finding motivates a decision, cite it from the decision's `rationale:` prose.

### Reserved IDs

These names cannot be used as entity IDs (they collide with the anchor grammar): `inputs`, `outputs`, `decisions`, `findings`, `prior_insights`, `analyses`, `options`, `content`, `narrative`. The validator rejects them.

## Data flow

Make the data-flow linkage navigable in the prose itself. Anchors are the trail — a reader follows the flow inline, without leaving the narrative.

1. **`narrative.outputs` says where each output goes next.** A sub-analysis's outputs are usually consumed by other sub-analyses or roll up into root findings. When you write the `outputs` prose, name those downstream destinations by anchor. Example, in the `reconstruction` sub-analysis's `outputs` key:

   > *"`xi_post_recon_lrg1` feeds [the post-reconstruction BAO fit](#analyses.bao_fit.outputs.bao_fit_post_iso_ap_lrg1) and supports the [headline detection finding](#findings.bao_detection_chi2_lrg1)."*

   Anchor downstream consumers where you can. When no anchor is reachable from the current scope (typically a sibling sub-analysis), bare `<analysis>.<output>` text is acceptable.

2. **The root narrative is the end-to-end view.** When the project has sub-analyses, the root analysis's `methods` (or `summary`) traces the pipeline from raw inputs to final outputs — as much overview as fits in a few paragraphs. The root is the place a reader can land cold and get the shape of the work; details telescope into the sub-analyses. A condensed example:

   > *"raw catalogs → [reconstruction](#analyses.reconstruction) → [clustering](#analyses.clustering) → root [BAO fit](#outputs.bao_fit_post_iso_ap_lrg1)."*

## Validation

```sh
astra validate astra.yaml
```

- **Broken references** → error. Anchor doesn't resolve to a real id.
- **Uncited declared elements** → warning. Every declared finding, decision, output, and sub-analysis must be cited somewhere in the narrative tree. If an element genuinely isn't worth a prose mention, consider whether it should be declared at all.
- **Conditional coverage** → error. The required-when rule above.

## User presence

Multi-turn back-and-forth → user present; use `AskUserQuestion` to clarify mode, scale, and any mode-specific framing before drafting. Single-shot or pipeline invocation → autonomous; make the reasonable default inference and note it inline on the narrative. Ambiguous → err on present and ask.

---

## Craft

- **Economy.** Every sentence introduces a new idea or sharpens an existing one. Release real verbs: `conducted cross-correlation` → `cross-correlated`.
- **Anchor text is prose, not an id.** `[the post-reconstruction catalogs](#analyses.reconstruction)`, not `[reconstruction](#analyses.reconstruction)`.
- **One reference per idea.** Three anchors on one sentence means the sentence carries too much; split it or drop one.
- **Specificity.** Names, numbers, references over generic claims.
- **Arrive through content.** No "in this analysis we will describe…"; the content is the opening.

### Real subjects, real verbs

"We measure the BAO peak with the LRG sample" reads as agency. "The measurements of the BAO peak reveal a 7σ detection" reads as zombie-noun abstraction. The test: can you picture someone or something physically doing the verb? If not, rewrite.

Valid subjects:

- **We** — for decisions and actions ("we chose the Gaussian damping prior")
- **The thing itself** — for states and properties ("the covariance is dominated by shot noise")
- **Passive voice** — when the actor is obvious ("a redshift cut is applied")
- **Results / data as epistemic subjects** — for what the data shows ("the measurement shows a 7σ peak"; "Figure 2 reveals…")
- **Physics doing physics** — for physical processes ("lensing distorts shapes"; "higher-order effects produce B-modes")

Anthropomorphized abstractions fail the test: "the methodology validates," "this analysis demonstrates," "the catalogue evolution follows." Rewrite to a real subject doing a real verb.

## Anti-patterns (mode-independent)

- **Wiki-style what-is framing.** "BAO is the baryon acoustic oscillation feature." A wiki summarizes; an ASTRA narrative points into reasoning. Replace with the load-bearing statement and an anchor: "we chose the Gaussian BAO damping prior over flat because flat admitted spurious minima — see [the prior comparison](#decisions.bao_damping_prior)."
- **Decision-list paragraph.** "We made the following decisions: A, B, C." Cite each decision where it shapes the pipeline, not as recitation. Too many to weave coherently → the spec wants more sub-analyses.
- **`summary` as primer.** Teaching what the field is. Readers arrive with context.
- **Drafting `findings` on a sub-analysis with no declared findings.** Skip the key.
- **Narrative-per-element.** Writing `narrative:` on findings, inputs, outputs, or insights. The five-key analysis narrative is the only home; per-element prose is `description` / `rationale` / `notes`.

Mode-specific anti-patterns live in each mode's reference.

---

## Self-contained example

A minimal (not necessarily valid) sketch showing how the blocks fit together. The point is the *shape*.

```yaml
id: example_analysis
version: "0.1.0"
name: "Example analysis"

narrative:
  summary: |
    We measure <quantity> in <sample>.  The feature is
    [detected at high significance](#findings.headline_detection) and
    [exceeds prior precision by 1.2×](#findings.precision_improvement),
    with [an anomalous feature at <location>](#findings.anomaly)
    motivating follow-up.

  inputs: |
    Primary data are [the <dataset>](#inputs.primary_data); validation
    uses [<mocks>](#inputs.validation_mocks).

  methods: |
    The pipeline runs in two stages.  [Preparation](#analyses.preparation)
    ingests the raw catalog and produces [cleaned two-point statistics
    ](#preparation.outputs.clean_stats).  [Fitting](#analyses.fitting)
    consumes those statistics and fits model parameters.  Both stages
    inherit the parent's [fiducial cosmology](#decisions.fiducial_cosmology)
    so the distance-redshift relation is used end-to-end.

  findings: |
    Three findings constitute the result: a
    [headline detection](#findings.headline_detection), a
    [precision comparison with prior work](#findings.precision_improvement),
    and [an anomalous feature](#findings.anomaly).  The anomaly is the
    most-discussed qualitative feature.

  outputs: |
    Two artifacts are promoted to the top level:
    [the final measurement table](#outputs.final_table) and
    [the headline figure](#outputs.headline_figure), both produced by
    [fitting](#analyses.fitting).

decisions:
  fiducial_cosmology:
    label: "Fiducial cosmology"
    rationale: |
      Planck 2018-ΛCDM is the community reference; distance-redshift
      conversion is downstream of this choice, and fixing it lets
      results be compared directly to prior measurements.  Inherited by
      [fitting](#analyses.fitting) so the end-to-end chain uses one
      distance scale.
    default: planck2018
    options:
      planck2018:
        label: "Planck 2018-ΛCDM"
      wmap9:
        label: "WMAP9"
        excluded_reason: "Superseded; no longer the community reference."
```

For a canonical reproduction narrative in context, see `Reproductions/DESI/desi-dr1-bao/astra.yaml` in the [LightconeResearch/Reproductions](https://github.com/LightconeResearch/Reproductions) repo.

---

## Now read the mode reference

Open the reference file that matches the user's situation. Each carries the mode's draft order, mode-specific moves, critique pass, and mode-specific anti-patterns.
