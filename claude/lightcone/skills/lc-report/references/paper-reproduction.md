# Paper-reproduction mode

An authoritative text exists — most often a published paper, but also a thesis, technical report, or preprint. The report reconstructs its narrative against the reproduction's `astra.yaml`, drawing on **the text and the spec as paired sources**: the text carries the claims and the confidence register; the spec carries the structural decomposition (which decisions are nodes, which findings are nodes, where sub-analyses sit). Neither is sufficient alone.

Read the main SKILL.md and `craft.md` first. This file adds what's specific to reproduction.

## Where the source text lives

Expect `work/reference/` — the standardized output of `/paper-extraction`:

- `work/reference/paper.tex` (arXiv source) **or** `work/reference/document.md` (Docling fallback)
- `work/reference/index.json` — section outline with line numbers, figures, tables, citations
- `work/reference/astra.yaml` — the paper as an ASTRA artifact
- `work/reference/figures/`, `work/reference/tables/`

If no authoritative text is accessible, this isn't reproduction — use co-drafting or retrofit mode instead.

## Paper-to-report mapping

Write this down before drafting a sentence.

| Paper element | Report home |
|---|---|
| Abstract | Introduction's opening (and abstract, if the report has one) — drafted last |
| Introduction (motivation, related work) | Introduction; motivating literature via `{astra:cite}` of prior insights |
| Methods section N | Methods — the matching sub-analysis's page or subsection, decisions referenced/embedded |
| Results | Results — output embeds + `{astra:value}` numbers + finding references |
| Discussion | Discussion — findings synthesized in prose |
| Conclusions | reinforces the Introduction's framing |
| Figures / tables | `:::{astra} outputs.<id>` embeds, referenced from prose via `{astra:ref}` |
| "We chose X because Y" sentences | already live in the decision's `rationale:` — embed or reference the decision; don't restate |

Not every text maps cleanly section-to-sub-analysis. When it doesn't, the sub-analysis DAG in `astra.yaml` is authoritative: structure the report by the DAG, harvesting the source text's prose for content. If the spec deliberately reorganized relative to the text, say so briefly in Methods — don't reorder silently.

## Reproduction-specific rules

- **Tell the author's story by default.** The report reproduces what the paper says, restated within the spec's structure. Decision rationales come from the paper's own justifications (they were captured in the spec during SPECIFY), not invented post-hoc.
- **Paraphrase, don't lift.** Restate the paper's claims in your own structuring rather than copying sentences verbatim — verbatim quotation calls authorship into question.
- **Preserve the confidence register.** If the paper says "we detect," don't write "we strongly detect"; if it hedges, preserve the hedge.
- **When the reproduction's results differ, report what was actually found — and flag it.** A covariance that diverges from the posted table, a coefficient at different precision, a null where the paper claimed detection: the report carries the reproduction's numbers (via `{astra:value}`, which guarantees this), and the divergence is surfaced to the user for phrasing input, never papered over. The comparison verdict and open-question resolutions from REVIEW are the source for these passages.
- **Voice seams.** When reproducer-specific content enters, mark the transition: *"During reproduction we found the published covariance differs from the posted table."* The sentence before speaks in the paper's voice; the sentences after speak in the reproducer's. A sentence that silently mixes them confuses both.
- **Declarative present tense.** Published = done: "the pipeline runs in two stages," not "we are measuring."
- **Name the scope.** Reproductions often cover a subset of the paper (one tracer, one figure family). State what's in and out in the Introduction so a reader knows.

## Critique pass

Run all four audits before declaring the report done (in addition to the `myst build` loop from SKILL.md):

1. **Fidelity.** Claims match the paper except where reproduction results actually differ — and those divergences are flagged with seams, phrasing ratified by the user. No sharpened or softened hedges. Every number comes through `{astra:value}`.
2. **Sequence.** Methods walks sub-analyses in DAG order; DAG order matches the paper's narrative sequence, or the deviation is named in prose. The Introduction opens with the question, not a field primer.
3. **Coverage.** Every declared finding, load-bearing decision, and promoted output is referenced somewhere. Grep the report pages for each spec id to check.
4. **Redundancy.** Prose says why elements matter; embeds say what they are. No paragraph re-describes what its neighboring embed renders.

## Anti-patterns (reproduction-specific)

- **Lifting the abstract verbatim** into the Introduction. Paraphrase — otherwise the report reads as a citation of itself.
- **Adding implications the paper didn't make.** Fidelity cuts both ways.
- **Eliding the reproducer's voice entirely.** If the reproduction caught something the paper missed, name it with a seam — that's the reproduction's value.
- **Restating decision rationales in prose.** The rationale lives on the decision in the spec; embed the decision. Report prose adds only what the argument needs around it.
