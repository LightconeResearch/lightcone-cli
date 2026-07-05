---
name: lc-report
description: >
  Authors and extends the project's MyST report — the external write-up
  (`index.md` + `myst.yml`, scaffolded by `lc init`) that references
  `astra.yaml` elements by tree-path through the MySTRA plugin instead
  of restating them. Use whenever the user wants to write up an
  analysis: "draft the report", "write up the results", "document the
  analysis", "draft the introduction/methods/results", "turn this into
  a paper", or when results have materialized and a write-up is the
  natural next step. Also use to fix or extend an existing report page.
  Not for per-element spec prose (`description:`/`rationale:` live in
  `astra.yaml` — see /astra) and not for the HTML figure side-by-side
  (/figure-comparison).
allowed-tools: Read, Write(*.md), Edit(*.md), Write(myst.yml), Edit(myst.yml), Glob, Grep, Bash(myst:*), Bash(astra:*), Bash(lc:*), AskUserQuestion
---

# /lc-report

Author the project's report as MyST Markdown wired to the analysis through the MySTRA plugin. The plugin reads `astra.yaml` at build time and resolves `{astra}` references against it, so the report and the analysis cannot drift: figures, decisions, findings, and numbers stay single-sourced in the spec and in `results/`.

Three layers, each with one source of truth — keep them separate:

| Layer | Source of truth |
|---|---|
| **Data** — what a decision/output/finding *is* | `astra.yaml` + `universes/` + `results/` |
| **Composition** — what appears, where, in what order | the report pages (`index.md`, …) |
| **Presentation** — how it looks | the MyST theme |

**The golden rule: never hard-type a measured number, restate a decision, or re-describe an output.** If it exists in `astra.yaml` or a result product, reference it by path. Every restated fact is a place the report can drift out of sync.

Read [`references/mystra-syntax.md`](references/mystra-syntax.md) before drafting — it carries the exact path grammar, roles, directives, and options. Read [`references/craft.md`](references/craft.md) before writing prose.

## Setup

1. **Read the spec.** `astra.yaml` at the project root, plus each sub-analysis's `astra.yaml`. Note inputs, outputs, decisions, findings, prior insights, and the sub-analysis tree — these are the elements the report will reference.
2. **Note the active universe.** MySTRA resolves against the first `.yaml` file in `universes/` — it selects decision options and which `results/<universe>/` artifacts resolve.
3. **Check materialization.** `lc status` — outputs that aren't materialized still embed (the spec entry renders) but `{astra:value}` reads from result files, so live numbers need `lc run` first.
4. **Check the report scaffold.** `lc init` writes `myst.yml` + `index.md` at the project root. If they're missing (e.g. a reproduction workdir that never ran `lc init`), create them — the minimal `myst.yml` is in [`references/mystra-syntax.md`](references/mystra-syntax.md).
5. **Check the MyST CLI.** `myst --version`. If absent, tell the user: `npm install -g mystmd` (or `uv tool install mystmd`). You can still draft without it, but you cannot validate.

## Pick the mode

Every mode drafts against the existing `astra.yaml`; what differs is the **second source** the prose draws on. If the mode isn't obvious from context, ask.

| Mode | Second source | Guidance |
|---|---|---|
| **Paper reproduction** | An authoritative text (paper, thesis, report) at `work/reference/` | Read [`references/paper-reproduction.md`](references/paper-reproduction.md) — mapping table, fidelity rules, voice seams |
| **Co-drafting** | The user, in conversation | Ask before drafting: what's the research question, the current headline finding, what moved along the way, what would you claim today? Use provisional voice for in-flight work — hedge what's uncertain, claim what's settled, and mark volatile sections (e.g. *"(Provisional — revisit after `bao_fitting`.)"*) |
| **Retrofit** | Project artifacts — code, notebooks, commits, README | Harvest what the artifacts record; where a rationale isn't recoverable, say so explicitly (*"(Reconstructed 2026-07: original rationale not recorded.)"*) — never fabricate one |

Hybrids are normal: a reproduction with co-drafted extensions, a retrofit with gaps the user fills in conversation.

## Structure the report

Sections mirror the analysis, not a template. The default single-page shape:

- **Introduction** — the research question, its context, why it matters. Cite prior insights with `{astra:cite}` where the literature motivates the work.
- **Methods** — walk the pipeline in DAG order. Reference each decision where it shapes the pipeline and embed the load-bearing ones as blocks; the option tabs and rationale render from the spec.
- **Results** — embed the output figures/tables/metrics as blocks; pull measured numbers into prose with `{astra:value}`; embed or reference findings where the results support claims.
- **Discussion / Conclusions** — synthesis; findings referenced inline, implications in your own prose.

**Multi-page when the analysis has sub-analyses.** One page per sub-analysis, named by dotted filename (`reconstruction.md`, `reconstruction.features.md`), listed in `myst.yml`'s `toc:`. The root page is the end-to-end view — it traces raw inputs to final outputs and embeds each sub-analysis's nav card (`:::{astra} reconstruction` `:::`); details telescope into the sub-analysis pages. A reader lands cold on `index.md` and gets the shape of the whole work.

## Draft order

Not introduction-first — the opening compresses the rest, so write it last.

1. **Methods** — the pipeline walk. Weave decision references into the argument; don't recite them as a list. Too many decisions to weave coherently means the report wants a sub-analysis page, not a longer paragraph.
2. **Results** — one block embed per promoted output, numbers in prose via `{astra:value}`, findings woven in where the evidence lands.
3. **Discussion** — how the findings relate; what they mean.
4. **Introduction** (and abstract, if wanted) — last. Open with the question and the headline finding; no field primer.

**Coverage:** every declared finding, load-bearing decision, and promoted output should be referenced somewhere in the report. An element genuinely not worth a mention is a hint it shouldn't be declared — surface that to the user rather than padding prose around it.

## Validate — build, fix, repeat

MySTRA verifies references against `astra.yaml` and `results/` at build time. After drafting (and after any spec change), run:

```bash
myst build --html
```

Then fix everything it surfaces, and build again until clean:

- `[mystra]` warnings in the build output (unknown ids, invalid `when:` refs, scope problems).
- Error admonitions in the rendered page — a directive path that didn't resolve renders an error box naming the path and reason.
- Inline error tokens from broken `{astra:value}` — e.g. `⟨value: no column "alpha2" in "bao_table"⟩` for a missing result file, unknown column, or filter matching no row.

Broken references never crash the build — they render visibly. Never leave one in a finished report. For interactive review, offer the user `myst start` (live preview at `localhost:3000`; note it only watches `.md` files — after editing `astra.yaml`, re-save a page to re-render).

## Restrictions

- **Only touch report pages and `myst.yml`.** Spec changes go through the normal `astra.yaml` discipline (see /astra), not through this skill. Never edit anything under `results/`.
- **Never hard-type a value that a `{astra:value}` reference can pull.** If a number isn't reachable (not a declared output), that's a spec gap to surface, not a license to type it.
- **Paraphrase, don't lift.** In reproduction mode, restate the source's claims in your own structure; preserve its confidence register (don't sharpen "we detect" into "we strongly detect", don't soften a hedge).

## Anti-patterns

- **Hard-typed numbers.** "α = 0.0696" typed in prose goes stale the first time the pipeline reruns. Use `{astra:value}`.
- **Restating instead of embedding.** Three paragraphs describing what a decision's options are duplicates the spec; embed the decision and let the prose say why it matters.
- **Registry dumping.** `:::{astra} outputs` on every page is an inventory, not a report. Registries are for appendices and navigation; the body places individual elements where the argument needs them.
- **Wiki-style primer.** "BAO is the baryon acoustic oscillation feature…" — readers arrive with context. Open with the load-bearing statement and a reference.
- **Decision-list paragraph.** "We made the following decisions: A, B, C." Reference each decision where it shapes the pipeline.
- **Skipping the build loop.** A report that was never built can hide broken paths behind plausible-looking syntax. Build before declaring done.
- **Stale filters.** A `{astra:value}` `where=` filter written against an old table layout renders an error token — fix the filter, don't paper over it with a typed number.
