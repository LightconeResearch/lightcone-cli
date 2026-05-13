# /narrative

Author the reader-facing prose in an `astra.yaml`: analysis-level
`narrative:` blocks (`summary`, `inputs`, `methods`, `findings`,
`outputs`), decision `rationale:` fields, and shorter `description:` /
`notes:` on individual entities. Always written against an existing
spec — the structure must exist when the prose lands.

Source: [`claude/lightcone/skills/narrative/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/narrative/SKILL.md).

## Modes

The skill draws on the spec plus a **second source**. Three modes,
distinguished by what that second source is:

| Mode | Second source | Status |
|---|---|---|
| **Paper reproduction** | An authoritative text (paper, thesis, technical report) | Ready |
| **Retrofit** | Project artifacts — code, notebooks, fibers, commit history | Stub |
| **Co-drafting** | The user, in conversation | Stub |

If the second source isn't obvious, the skill asks. Hybrid is allowed
(reproduction with co-drafted extensions; retrofit with co-drafted
gap-filling).

`/lc-from-paper` invokes `/narrative` during SPECIFY (paper-reproduction
mode); users can invoke it directly in any mode.

## Allowed surfaces

The five-key analysis narrative:

| Key | What it carries | Required when |
|---|---|---|
| `summary` | Question, scope, headline shape | optional, but should always exist |
| `inputs` | Provenance — the data the analysis rests on | `Analysis.inputs` non-empty |
| `methods` | Pipeline walk; cite each decision and sub-analysis by anchor | `Analysis.decisions` or `Analysis.analyses` non-empty |
| `findings` | Synthesis of declared findings, each cited by anchor | `Analysis.findings` non-empty |
| `outputs` | Which artifacts were promoted, and where they go downstream | `Analysis.outputs` non-empty |

A decision's `rationale:` is its own one-paragraph slot: what was
decided, the insight that motivated it (cite by anchor), and the
load-bearing alternative and why it lost. Per-entity prose
(`description`, `notes`) is shorter and lives on individual entries.

## Anchors

Markdown link syntax with `#`-target, **tree-path-first** — same
grammar as decision `from:` references.

| Target | Anchor |
|---|---|
| Input | `#inputs.<id>` |
| Output | `#outputs.<id>` |
| Decision | `#decisions.<id>` |
| Option | `#decisions.<id>.options.<opt>` |
| Finding | `#findings.<id>` |
| Prior insight | `#prior_insights.<id>` |
| Sub-analysis | `#analyses.<sub>` |
| Element inside a sub-analysis | `#<sub>.<category>.<id>` |
| Parent scope from a sub-analysis | `#../decisions.<id>` |

Anchor text is **authored prose**, never the raw id. One reference per
idea — stacking three on a sentence means the sentence carries too
much.

## Length and modularity

1–3 paragraphs per key, at any level. Length is the mechanism that
keeps analyses modular: **if references don't fit in three paragraphs,
the analysis is too big — split it.** The narrative is a compressor;
if it won't compress, split the thing being compressed.

## Validation

```sh
astra validate astra.yaml
```

- **Broken references** → error.
- **Uncited declared elements** → warning. Every declared finding,
  decision, output, and sub-analysis must be cited somewhere in the
  narrative tree.
- **Conditional coverage** (required-when rules above) → error.

## Anti-patterns

- **Wiki-style what-is framing.** A wiki summarizes; an ASTRA narrative
  points into reasoning.
- **Decision-list paragraph.** "We made the following decisions: A, B,
  C." Cite each where it shapes the pipeline.
- **`summary` as primer.** Teaching what the field is. Readers arrive
  with context.
- **Drafting `findings` on a sub-analysis with no declared findings.**
  Skip the key.
- **Narrative-per-element.** The five-key analysis narrative is the
  only home; per-element prose is `description` / `rationale` /
  `notes`.

Mode-specific anti-patterns live in each mode's reference under
`claude/lightcone/skills/narrative/references/`.

## Related

- [`/lc-from-paper`](lc-from-paper.md) — invokes `/narrative` during
  SPECIFY in paper-reproduction mode.
- [`/astra`](index.md#reference-skills-auto-primed-via-session-start) — full schema reference.
