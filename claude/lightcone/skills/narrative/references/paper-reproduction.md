# Paper reproduction mode

An authoritative text source exists — most often a published paper, but also a thesis, technical report, posted preprint, or other canonical account of the work. Reconstruct its narrative into ASTRA's five-key shape, drawing on **the text and the under-construction `astra.yaml` as paired sources**: the text carries the claims and the confidence register; the spec carries the structural decomposition (which decisions are nodes, which findings are nodes, where sub-analyses sit). Neither is sufficient alone.

The spec may be stable, in flux, or both — paper-reproduction often runs concurrently with spec refinement. The narrative tracks both: when a decision is added, write its `rationale:`; when a sub-analysis splits, draft its five keys; when a finding is declared, fold it into the parent's `findings` synthesis.

Read the main SKILL.md first. This file adds what's specific to reproduction.

## Where the source text lives

The skill expects `work/reference/` to exist — the standardized output of [`/paper-extraction`](../../paper-extraction/SKILL.md). If it doesn't, run `/paper-extraction` first. The predictable shape:

- `work/reference/paper.tex` (Path A — symlink to main `.tex`) **or** `work/reference/document.md` (Path B — Docling output)
- `work/reference/index.json` — section outline with line numbers, figures, tables, citation locations
- `work/reference/astra.yaml` — the paper as an ASTRA artifact (claimed findings as ASTRA findings)
- `work/reference/figures/`, `work/reference/tables/`, `work/reference/source/` (Path A only)

If no authoritative text is accessible at all, this isn't reproduction — fall back to `references/existing-analysis.md` or `references/co-drafting.md`.

## Paper-to-ASTRA mapping

Write this down before drafting a sentence.

| Paper element | ASTRA home |
|---|---|
| Abstract | `summary` |
| Introduction (motivation, related work) | `summary` + `findings` intro |
| Methods section N | the matching sub-analysis's `narrative.methods` |
| Results | structural `findings.<id>` claims; narrative intro in `findings` |
| Discussion | `findings` narrative + `summary` implications |
| Conclusions | reinforces `summary` |
| Figures / tables | `outputs.<id>` — referenced in `findings` via anchors |
| "We chose X because Y" sentences | the relevant decision's `rationale:` |

Not every text maps cleanly section-to-sub-analysis. When it doesn't, the sub-analysis DAG in `astra.yaml` is authoritative: narrate according to the DAG, harvesting the source text's prose for content. If the spec deliberately reorganized relative to the text, say so briefly in `methods`.

## Workflow

### 1 · Orient

Read both sources before drafting. The spec carries the structural decomposition; the text carries the claims.

1. **`astra.yaml` at the project root** — whole file. Note `inputs`, `outputs`, `decisions`, `findings`, `analyses`, existing `narrative:`. Notice which of the five keys are present vs. empty.
2. **Each sub-analysis `astra.yaml`** — skim decisions (inherited vs. local), findings, outputs, existing narrative.
3. **The source text** — abstract, intro open/close, methods section headers, discussion, conclusions. Read full sections when drafting the corresponding ASTRA piece. Use `work/reference/index.json` to navigate; the parsed `paper.tex` (Path A) or `document.md` (Path B) is the primary source.
4. **Project `CLAUDE.md` and any working notes** — paper-specific conventions, gotchas, scope decisions.

If the user is present, surface the orienting questions — `AskUserQuestion` is useful when several land together; one question at a time is fine when only one is open:

- **Scale:** top-level, a specific sub-analysis, or a decision's `rationale:`?
- **Pure reproduction, or with reproducer extensions** (e.g., the reproduction's covariance differs from the posted table)?
- **Approach:** start with a specific question first — a methods subsection, a particular figure's choices, a discussion claim worth tracing into the decisions — or one-shot the whole narrative? Sets the session shape.

### 2 · Draft order

Not `summary` first. `summary` compresses the rest; draft it last.

1. **`inputs`** — shortest. Name the data and its provenance. One short paragraph. Let the inputs structure carry the dataset detail.
2. **`methods`** — walk the pipeline in DAG order. Cite each sub-analysis and decision by anchor as part of the argument, not as an enumeration. If too many to weave coherently, the analysis wants more sub-analyses. Inheritance that propagates across sub-analyses gets called out explicitly because it's load-bearing end-to-end. A pivot the paper narrates ("we initially tried X, but…") is cheap to preserve because of telescoping.
3. **`findings`** — only if findings are declared structurally. Synthesize how they relate; each cited by anchor, not enumerated.
4. **`outputs`** — thin. Which artifacts were promoted and why; cite the sub-analysis that produced them; name downstream consumers (see Data flow in SKILL.md).
5. **`summary`** — last. 1–2 paragraphs. Open with the question and the headline finding; thread motivation, method, and implications. No primer material.

For each decision, write a one-paragraph `rationale:`: what was decided, the prior insight that motivated it (cite by anchor), what the load-bearing alternative was and why it lost.

For sub-analyses, same order, same length target.

**Conditional keys.** Only include keys whose structural counterpart is non-empty. A reconstruction sub-analysis with no findings gets `summary`, `methods`, `inputs`, `outputs` — no `findings`.

### 3 · Reproduction-specific moves

- **Tell the author's story by default.** The narrative reproduces what the paper says, restated within the ASTRA structure — anchored to what's referable in the spec (decisions, findings, prior insights). Decision rationales come from the paper's "we chose X because Y" sentences, not invented post-hoc.
- **Paraphrase, don't lift.** Restate the paper's claims in your own structuring rather than copying sentences verbatim — verbatim quotation calls authorship into question. Preserve meaning and confidence register; don't sharpen or soften (if the paper says "we detect," don't write "we strongly detect"; if it hedges, preserve the hedge).
- **Two sources, paired.** The authoritative text carries claims, confidence register, and sequence. The under-construction `astra.yaml` carries the structural decomposition. Draft against both; let the spec's structure shape what each key covers, and let the text shape what's said.
- **When the reproduction's results differ, adapt — and flag.** Where the reproduction landed on different findings (a covariance that diverges from the posted table, a coefficient with different precision, a null where the paper claimed detection), the narrative needs to report what was actually found, not what was claimed. This wants human input on phrasing; surface the divergence to the user rather than papering over it.
- **Voice seams.** When reproducer-specific content enters the narrative, mark the transition. *"During reproduction we found the published covariance differs from the posted table"* is a seam; the sentence before it can speak in the paper's voice, the sentences after it speak in the reproducer's. A sentence that silently mixes them confuses both.
- **Walk the paper's sequence in `methods`.** Traverse sub-analyses in DAG order — and the DAG order should match the paper's section order. If the spec deliberately reorganized (split one section into two sub-analyses, or merged two sections into one), name the deviation briefly in `methods`. Don't reorder silently.
- **Published = done.** Reproduction narrative is declarative, present-tense matching the paper's voice ("The analysis is organised as…", "The pipeline runs in…"). Not "we are measuring."
- **Scope-limited reproductions.** Real-world reproductions often cover a subset of the paper (e.g., DESI BAO reproducing only LRG1+LRG2). Name the scope in `summary` so a reader knows what's in and out.

### 4 · Critique pass

Run all four audits before declaring the narrative done.

**Fidelity audit.**

- Claims match the paper, **except where reproduction results actually differ.** If the reproduction landed on different findings, the narrative reports what was found — and the divergence has been surfaced to the user for phrasing input, not silently softened or sharpened.
- Voice seams marked where reproducer content enters.
- Rationales traceable to the paper's justifications or to a prior insight in the spec.
- No invented citations. Every anchor resolves to a real spec id.
- Scope (what's reproduced, what isn't) stated in `summary` if narrower than the paper.

**Sequence audit.**

- `methods` walks sub-analyses in DAG order; DAG order matches the paper's narrative sequence (or the deviation is named in prose).
- `summary` opens with the question, not a field primer.

**Anchor coverage audit.**

- `astra validate` warns on any declared finding / decision / output / sub-analysis not cited in the narrative. Review the warnings; either cite the element or consider whether it should be declared.

**Structural-peer-redundancy audit.**

- Citations woven into argument, not recited as a list.
- `findings` narrative synthesizes relationships between findings; `inputs` narrative names provenance. Neither catalogs fields.

## Anti-patterns (reproduction-specific)

- **Lifting verbatim.** Copy-pasting abstract sentences into `summary`. Paraphrase — otherwise the narrative reads as a citation of itself.
- **Adding implications the paper didn't make.** Fidelity cuts both ways.
- **Eliding the reproducer's voice entirely.** If the reproduction caught something the paper missed, name it with the seam.
- **Treating paper sections as sub-analyses.** A paper's Section 3.2 isn't automatically a sub-analysis; the DAG is the authority.
- **Listing instead of weaving.** Narrate each decision where it shapes the pipeline. Too many to weave coherently → the spec wants more sub-analyses.

## When reproduction shifts modes

- **Hybrid with co-drafting.** If the reproduction adds a sub-analysis the paper didn't have (a reproducer-specific extension), that sub-analysis's narrative is co-drafted, not reproduced. Use the seams.
- **Hybrid with retrofit.** If the reproduction inherits code or fibers from a prior iteration, those carry rationale that didn't make it into the paper — harvest from artifacts as in retrofit mode for those sections.
