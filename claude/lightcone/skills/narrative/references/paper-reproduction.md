# Paper reproduction mode

A published paper exists. Reconstruct its narrative into ASTRA's
five-key shape — against an `astra.yaml` that's already built, or
alongside one being built concurrently — preserving the paper's
confidence level and sequence.

## Where the paper lives

Prefer arXiv LaTeX source. It's the most natural form to work with:
sections are delimited, captions are inline, citations resolve to a
`.bib`, equations are parseable.

### 1 · arXiv LaTeX source (default)

If the paper is on arXiv, fetch the source:

```sh
arxiv_id=<id>        # e.g. 2404.03000
mkdir -p paper
cd paper
curl -L "https://arxiv.org/e-print/${arxiv_id}" -o "${arxiv_id}.tar.gz"
tar -xzf "${arxiv_id}.tar.gz"
```

The archive unpacks to the paper's working tree — typically a main
`.tex` file, section includes, figures, a `.bib`. Identify the main
file with `grep -l '\\documentclass' *.tex`. Read sections in order;
resolve citation keys against the bundled `.bib`.

### 2 · Existing parsed paper in the project

Some reproductions ship the paper already parsed. Check for:

- `desi_dr1_paper/` or `paper/` at the project root.
- Single `.md` file (Docling output or manual conversion),
  `.pdf`, or the arXiv tarball unpacked.

If a markdown parse exists, use it as the primary source; fall back
to the PDF or the arXiv source to resolve ambiguities.

### 3 · User-provided

Ask the user where the paper is if nothing lands automatically.

If no paper is accessible, this is not a reproduction task — fall
back to `references/existing-analysis.md` (currently under
development).

## Paper-to-ASTRA mapping

Write this down before drafting a sentence.

| Paper element | ASTRA home |
|---|---|
| Abstract | `summary` |
| Introduction (motivation, related work) | `summary` + `findings` intro |
| Methods section N | corresponding sub-analysis's `narrative.methods` |
| Results | structural `findings.<id>` claims; narrative intro in `findings` |
| Discussion | `findings` narrative + `summary` implications |
| Conclusions | reinforces `summary` |
| Figures / tables | `outputs.<id>` — referenced in `findings` via anchors |
| "We chose X because Y" sentences | decision `rationale:` |

Not every paper maps cleanly section-to-sub-analysis. When it
doesn't, the sub-analysis DAG in `astra.yaml` is authoritative.
Narrate according to the DAG, harvesting the paper's prose for
content. If the spec has deliberately reorganized relative to the
paper, say so briefly in `methods`.

## Workflow

### 1 · Orient

The spec may be stable, in flux, or both — narrative drafting often
runs concurrently with spec refinement. Read what's there; expect to
revisit as the spec moves.

1. `astra.yaml` at the project root. Whole file. Note `inputs`,
   `outputs`, `decisions`, `findings`, `analyses`, existing
   `narrative:`. Notice which of the five keys are present vs. empty.
2. Each sub-analysis `astra.yaml`. Skim decisions (inherited vs.
   local), findings, outputs, existing narrative. A sub-analysis may
   use `description:` (legacy) instead of the five-key `narrative:`
   block — promoting it may be part of the job.
3. The paper — abstract, intro open/close, methods section headers,
   discussion, conclusions. Read full sections when drafting the
   corresponding ASTRA piece.
4. Any project `CLAUDE.md` or working notes.

Infer authoring state (from-scratch, extending, revising) from what
is already on disk. If the user is present, confirm via
`AskUserQuestion`:

- Scale: top-level, a specific sub-analysis, or a decision's
  `rationale:`?
- Pure reproduction, or with reproducer extensions (e.g., the
  reproduction's covariance differs from the posted table)?

If the spec is iterating, draft narrative concurrently — rationale
when a decision is added, five-key narrative when a sub-analysis
splits, findings synthesis updated when a finding is added. Narrative
and spec quality rise together when they share context.

### 2 · Draft order

Not `summary` first. `summary` compresses the rest; draft it last.

1. **`inputs`** — shortest. Name the data and its provenance. One
   short paragraph. Let the inputs structure carry the dataset
   detail.
2. **`methods`** — walk the pipeline in DAG order. Cite each
   sub-analysis and decision by anchor as part of the argument, not
   as an enumeration. If there are too many to weave coherently, the
   analysis wants more sub-analyses. Inheritance that propagates
   across sub-analyses gets called out because it's load-bearing
   end-to-end. Movement-of-learning lives here — a pivot the paper
   narrates ("we initially tried X, but…") is cheap because of
   telescoping.
3. **`findings`** — **only if findings are declared structurally.**
   If `findings:` is empty, skip this key (per narrate-what-you-
   declare). If findings exist, synthesize how they fit together —
   each cited by anchor, not an enumeration.
4. **`outputs`** — thin. Which artifacts were promoted and why;
   point to the sub-analysis that produced them.
5. **`summary`** — last. Two paragraphs. Open with the question and
   the headline finding; thread motivation, method, and implications.
   No primer material.

For sub-analyses, same order, same length target (1–3 paragraphs per
key). For a decision's `rationale:`, one paragraph: what was decided,
the insight(s) that motivated it (by anchor), what the load-bearing
alternative was and why it lost. The alternatives themselves are in
the options structure.

**Conditional keys on sub-analyses.** Only include keys whose
structural counterpart is non-empty. A reconstruction sub-analysis
with no findings gets `summary`, `methods`, `inputs`, `outputs` — no
`findings`.

### 3 · Reproduction-specific moves

- **Fidelity to source confidence.** Don't sharpen or soften. If the
  paper says "we detect," don't write "we strongly detect." If it
  hedges, preserve the hedge.
- **Harvest, don't invent.** The paper's prose is the first source.
  Paraphrase — don't lift verbatim — but preserve meaning and
  confidence register.
- **Voice seams.** If reproducer-specific content enters ("during
  reproduction we found the published covariance differs from the
  posted table"), mark the transition. A sentence mixing paper
  claims and reproduction claims without a seam confuses both.
- **Paper sequence is usually load-bearing.** DAG order should match
  the paper's section order unless the spec deliberately
  reorganized.
- **No primer material.** `summary` is not a field-introduction.
  Don't teach what BAO or weak lensing is. Readers arrive with
  context.
- **Rationales come from the paper.** "We chose reconstruction
  convention X because Y" becomes the backbone of a decision's
  `rationale`. Keep Y; cite the supporting prior insight by anchor
  if one exists.
- **Published = done.** Reproduction narrative is declarative,
  present-tense matching the paper's voice ("The analysis is
  organised as…", "The pipeline runs in…"). Not "we are measuring."
- **Scope-limited reproductions.** Real-world reproductions often
  cover a subset of the paper (e.g., DESI BAO reproducing only
  LRG1+LRG2). Name the scope in `summary` so a reader knows what's
  in and out.

## Critique pass

Run these reproduction-specific checks alongside the three-phase and
craft audits from SKILL.md.

**Fidelity audit.**

- No sharpened or softened claims relative to the paper.
- Voice seams marked where reproducer content enters.
- Rationales traceable to the paper's justifications or to a prior
  insight in the spec.
- No invented citations. Every anchor resolves to a real spec id.
- Scope (what's reproduced, what isn't) stated in `summary` if
  narrower than the paper.

**Sequence audit.**

- `methods` walks sub-analyses in DAG order; DAG order matches the
  paper's narrative sequence (or the deviation is named in prose).
- `summary` opens with the question, not a field primer.

**Structural-peer-redundancy audit.**

- Every declared decision, finding, output, and sub-analysis cited
  somewhere in the narrative (validator enforces). Citations woven
  into argument, not recited as a list.
- `findings` narrative synthesizes relationships between findings;
  `inputs` narrative names provenance. Neither catalogs fields.

**Anchor coverage audit.**

- `astra validate` warns on any declared finding / decision / output
  / sub-analysis not cited in the narrative. Review the warnings;
  either cite the element or consider whether it should be declared.

## Anti-patterns (reproduction-specific)

- **Lifting verbatim.** Copy-pasting abstract sentences into
  `summary`. Paraphrase — otherwise the narrative reads as a citation
  of itself.
- **Adding implications the paper didn't make.** Fidelity cuts both
  ways.
- **Eliding the reproducer's voice entirely.** If the reproduction
  caught something the paper missed, name it with the seam.
- **Treating paper sections as sub-analyses.** A paper's Section 3.2
  isn't automatically a sub-analysis; the DAG is the authority.
- **Listing instead of weaving.** Narrate each decision where it
  shapes the pipeline. Too many to weave coherently → the spec wants
  more sub-analyses.
- **Drafting `findings` on a sub-analysis that has no declared
  findings.** Skip the key.
