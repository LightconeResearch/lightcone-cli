---
name: narrative
description: >
  Author or revise the `narrative:` prose inside an ASTRA analysis
  (`astra.yaml` and its sub-analyses) plus decision `rationale:` fields.
  Five fixed keys at each scale (`summary`, `findings`, `methods`,
  `inputs`, `outputs`). Three working modes — paper reproduction
  (ready), existing-analysis retrofit (under development), and
  interactive in-flight authoring (under development). Use when the
  `narrative:` block is empty or stub, when a decision needs a
  `rationale:`, when a sub-analysis needs its own narrative, or when
  revising existing prose. Triggers on "narrative", "draft the
  narrative", "narrate this analysis", "narrate this sub-analysis",
  "rationale for this decision", "write the summary", or any request
  for reader-facing prose keyed off an astra.yaml.
---

# narrative

## What this skill writes

One field: `narrative:` on an analysis or sub-analysis, or `rationale:` on a decision.
Per-element prose (what each `Input`, `Output`, `Decision`, `Option`, or `Insight` is and why it matters) lives on those elements' own `description` / `rationale` / `notes` fields.
`narrative` is the analysis-level story that weaves the pieces together.

## What a narrative is

Science, from a single decision to a review paper, is a practice of
engaging with previous work and telling the story of what was tried
and what it means. Any honest account does three things.

**Grounding.** Where the work sits — state of the field, open
questions, prior work it responds to, upstream decisions that shape
its choices. Tells the reader why before the work shows its own
value. May foreshadow findings.

**Movement of learning.** Not the tidied retrospective ("we did X,
obtained Y") but traces of the process: what was tried, what failed,
what forced a step back. The best papers convey this; most compress
it away under length pressure. ASTRA's telescoping makes it cheap —
a sentence at the top about global-vs-per-object PSF leakage, one
level down where the nerd gets the two pages on how the team got
there. Papers don't have this affordance and so compress iteration
away; ASTRA does, and authors should spend it.

**Implications.** What the results mean and where they point.
Results are facts; what they do to the field is the argument.
Forward-look matters even when unformed — that is where science
passes the baton.

A narrative that does all three at the appropriate scale is honest.
One that presents only results and methods elides the meaning-making.

The three phases repeat at every scale. A top-level analysis
narrates them across five keys (`summary`, `methods`, `findings`,
`inputs`, `outputs`); a sub-analysis does the same; a decision
narrates in one paragraph of `rationale:`. The telescope gives the
reader a short view at their current depth and the option to drill
in — without exploding the parent.

## Length as forcing function

1–3 paragraphs per key, at any level.

Length is the mechanism that keeps analyses modular, not a style
preference. If the references don't fit in three paragraphs, the
analysis is too big — split it. The narrative is a compressor; if
it won't compress, split the thing being compressed.

## What this prose is for

ASTRA preserves the decision structure that papers compress into
linear argument; the narrative keeps that structure legible. Three
consequences:

- **Not wiki, not paper.** A wiki page summarizes ("BAO is the
  baryon acoustic oscillation feature"); a paper compresses ("we
  chose the Gaussian prior"). An ASTRA narrative **points into
  reasoning** — it names the load-bearing decision, anchors to the
  structured node that records it, and lets the reader follow. The
  prose does not re-explain the field or re-list the spec.
- **Read and queried.** The narrative is consumed by human readers
  *and* by agent retrievers. Anchor coverage and clarity are
  substrate, not style — an uncited decision is invisible to both
  readings.
- **Asymmetric load.** The three phases don't map onto ASTRA's
  structure evenly. Movement-of-learning has strong structural
  support — `decisions`, `options`, `prior_insights`, the
  sub-analysis DAG — and `methods` condenses what structure already
  carries. Grounding has partial support at the decision site;
  implications have none. On those two phases, the narrative is the
  reader's only access — carry just enough, and err toward brevity
  and certainty.

## Pick a mode first

**Paper reproduction is production-ready. Retrofit and interactive
are under active development — their references are working drafts.**

Three modes. Read the matching reference file in full before drafting.

| Mode | Reference | Status | When |
|---|---|---|---|
| **Paper reproduction** | [`references/paper-reproduction.md`](references/paper-reproduction.md) | **Ready.** | A published paper exists and the analysis mirrors it. Primarily in-house Lightcone work (DESI BAO and similar) plus end users bringing a paper to reproduce. Covers paper sourcing (arXiv LaTeX preferred), paper→ASTRA mapping, voice seams, fidelity rules. |
| **Existing-analysis retrofit** | [`references/existing-analysis.md`](references/existing-analysis.md) | Under development. | Code, results, or an in-flight project being imported into ASTRA with no source paper. Archaeological work: triage, reconstruction of intent, gaps where the record is silent. |
| **Interactive (in-flight research)** | [`references/interactive.md`](references/interactive.md) | Under development. | New research being done now; the narrative drafted alongside the work. Provisional voice, ask-first discipline. |

If unsure which applies, confirm with the user via `AskUserQuestion`.

The rest of this file is the **mode-independent substrate** every
reference relies on.

---

## Narrate what you declare

The five keys are schema-optional, but `astra validate` applies a
**conditional requirement** — a section must hold non-empty prose
when the corresponding structured data exists on the Analysis node.

| Key | Required when |
|---|---|
| `findings` | `Analysis.findings` has entries |
| `methods` | `Analysis.decisions` or `Analysis.analyses` has entries |
| `inputs` | `Analysis.inputs` has entries |
| `outputs` | `Analysis.outputs` has entries |
| `summary` | always optional (no structured counterpart) |

Three consequences worth internalizing:

- **A stub analysis with only `summary` is valid.** Use that for
  stage-zero scoping.
- **Don't write a `findings` key before findings are declared.** If the
  spec's `findings:` list is empty, the narrative's `findings` key
  should not appear — adding prose about findings that don't exist is
  fiction.
- **`summary` is the one key without a structural peer.** It's the
  "question, scope, orientation" key — the only place prose stands
  alone, not framing something structural.

---

## The spec renders alongside the narrative

ASTRA's structural content — decisions, findings, inputs, outputs,
sub-analyses, options — surfaces alongside the narrative. Structural
peers will be presented; **prose does not duplicate them.** An
abstract does not list every methods subsection; a methods section
does not re-state every appendix equation. Prose assumes its
structural peers exist and focuses on argument.

Applied to the five keys:

- `summary` **orients** — question, scope, headline shape.
- `methods` **walks the pipeline**, citing each decision and
  sub-analysis by anchor where they appear. Movement-of-learning
  lives here.
- `findings` **synthesizes** — each finding cited by anchor as part of
  the argument, not an enumeration.
- `inputs` **names provenance**.
- `outputs` **names what was promoted and why**, citing each by anchor.
- Decision `rationale:` **names why the default won**.

---

## Anchor coverage

`astra validate` checks:

- **Broken references** → error. Anchor doesn't resolve to a real id.
- **Uncited declared elements** → warning. Every declared finding,
  decision, output, and sub-analysis must be cited somewhere in the
  narrative tree.

If a declared element is genuinely not worth a prose mention, consider
whether it should be declared at all.

---

## User presence

Multi-turn back-and-forth → user present; use `AskUserQuestion` to
clarify mode, scale, and reproduction-vs-extension before drafting.
Single-shot or pipeline invocation → autonomous; make the reasonable
default inference and note it inline on the narrative. Ambiguous →
err on present and ask.

---

## Phase → key mapping

The three phases (see top) map onto the five keys unevenly:

| Key | Dominant phase |
|---|---|
| `summary` | all three, telescoped |
| `findings` | implications |
| `inputs` | grounding |
| `methods` | movement of learning |
| `outputs` | structural; phase-thin |

There is no `discussion` key. Implications distribute into `summary`
and `findings`.

---

## Anchor syntax

Markdown link syntax, `#`-target, **tree-path-first**.

| Target | Anchor |
|---|---|
| Input | `#inputs.<id>` |
| Output | `#outputs.<id>` |
| Decision | `#decisions.<id>` |
| Option within a decision | `#decisions.<id>.options.<opt>` |
| Finding | `#findings.<id>` |
| Prior insight | `#prior_insights.<id>` |
| Sub-analysis (whole node) | `#analyses.<sub>` |
| Element inside sub-analysis | `#<sub>.<category>.<id>` (e.g. `#reconstruction.decisions.algorithm`) |
| Parent scope (from a sub-analysis) | `#../decisions.<id>` |

Note the sub-analysis form: **sub-analysis first, then category**.
`#reconstruction.decisions.algorithm`, not `#decisions.reconstruction.algorithm`.
References are interpreted **relative to the hosting analysis**; use
`../` to escape to parent scope (matches decision `from_ref` syntax).

Rules:

- Anchor text is authored prose, **not** the raw id.
- Inline refs do the work of a citation; don't footnote or parenthesize.
- One ref per idea. Stacking three on a sentence means the sentence
  carries too much.
- Findings cannot currently appear in `decisions.options.insights`
  (see [astra-spec#16](https://github.com/LightconeResearch/astra-spec/issues/16)).
  When a finding motivates a decision, cite it from the decision's
  `rationale:` prose.

---

## Reserved entity names

These names cannot be used as entity IDs (they collide with the
anchor grammar): `inputs`, `outputs`, `decisions`, `findings`,
`prior_insights`, `analyses`, `options`, `content`, `narrative`.

If you find an entity using one (legacy spec), flag it; the authoring
tooling and validator will reject it.

---

## Linking relationships — structural vs narrative

| Relationship | Structural | Narrative |
|---|---|---|
| Prior insight → decision option | `decisions.<id>.options.<opt>.insights: [ids]` | inline in `methods` when the decision is discussed |
| Finding → output | `findings.<id>.evidence` → `outputs.<id>` | inline in `findings` |
| Finding → decision | *no structural link yet* (#16) | inline in decision's `rationale:` |
| Decision → decision | `decisions.<id>.from: <ref>` or `from: ../decisions.<id>` | inline in the inheriting decision's `rationale:` |

If a relationship is structural, don't duplicate it in prose — cite
it by anchor.

---

## Self-contained example

A minimal (not necessarily valid) sketch showing how the blocks fit
together. The point is the *shape*.

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
    The pipeline runs in two stages.
    [Preparation](#analyses.preparation) ingests the raw catalog and
    produces [cleaned two-point statistics
    ](#preparation.outputs.clean_stats).  [Fitting
    ](#analyses.fitting) consumes those statistics and fits model
    parameters.  Both stages inherit the parent's
    [fiducial cosmology](#decisions.fiducial_cosmology) so the
    distance-redshift relation is used end-to-end.

  findings: |
    Three findings constitute the result: a
    [headline detection](#findings.headline_detection), a
    [precision comparison with prior work
    ](#findings.precision_improvement), and
    [an anomalous feature](#findings.anomaly).  The anomaly is the
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
      results be compared directly to prior measurements.  Inherited
      by [fitting](#analyses.fitting) so the end-to-end chain uses one
      distance scale.
    default: planck2018
    options:
      planck2018:
        label: "Planck 2018-ΛCDM"
      wmap9:
        label: "WMAP9"
        excluded_reason: "Superseded; no longer the community reference."
```

What to notice:

- Anchor text is prose, not an id.
- `methods` uses the sub-analysis-first form
  (`#preparation.outputs.clean_stats`) for cross-scope refs.
- `findings` synthesizes how three findings relate; each cited by
  anchor, not recited.
- `outputs` is thin — two sentences.
- Decision rationale cites a sub-analysis by anchor when the choice
  propagates, and says why the default won without enumerating options.

For a canonical reproduction narrative in context, see
`Reproductions/DESI/desi-dr1-bao/astra.yaml` in
`LightconeResearch/Reproductions`.

---

## Craft

- **Economy.** Every sentence introduces a new idea or sharpens an
  existing one. Release real verbs: `conducted cross-correlation` →
  `cross-correlated`.
- **Epistemic honesty.** Hedges carry information about certainty.
  "This suggests" reflects real uncertainty; "may perhaps indicate" is
  decorative.
- **Show, don't label.** Describe the tension; don't announce it. Cut
  signposting: "the key insight is," "importantly," "it is worth
  noting."
- **Specificity.** Names, numbers, references over generic claims.
- **Arrive through content.** No "in this analysis we will describe…";
  the content is the opening.

---

## Anti-patterns (mode-independent)

- **Narrative-per-element.** Writing `narrative:` on findings, inputs,
  outputs, or insights. The five-key analysis narrative is the only
  home; per-element prose is `description` / `rationale` / `notes`.
- **Results-only narrative.** Methods without movement-of-learning
  elides the meaning-making. At minimum, name one pivot or abandoned
  option per scale.
- **Decision-list paragraph.** "We made the following decisions: A,
  B, C." Cite each decision where it shapes the pipeline, not as
  recitation. Too many to weave coherently → the spec wants more
  sub-analyses.
- **Wiki-style what-is framing.** "BAO is the baryon acoustic
  oscillation feature." A wiki summarizes; an ASTRA narrative points
  into reasoning. Replace with "we chose the Gaussian BAO damping
  prior over flat because flat admitted spurious minima" — with the
  anchor. Applies to every key.
- **`summary` as primer.** Teaching what the field is. Readers arrive
  with context.

---

## Lint

1. `astra validate <path>` — catches broken anchors, schema
   violations, uncited declared elements.
2. Paragraph count per key — flag anything over three.
3. Only conditionally-required keys present — if `findings:` is
   empty, `narrative.findings` is absent.

---

## Now read the mode reference

Before drafting, open the reference file that matches the user's
situation.
