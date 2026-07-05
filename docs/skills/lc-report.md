# /lc-report

Author or extend the project's MyST report — the external write-up
(`index.md` + `myst.yml`, scaffolded by `lc init`) that references
`astra.yaml` elements *by tree-path* through the
[MySTRA](https://github.com/LightconeResearch/MySTRA) plugin, instead of
restating them. Figures, decisions, findings, and measured numbers stay
single-sourced in the spec and in `results/`, so the report cannot drift
from what the pipeline actually produced.

Source: [`claude/lightcone/skills/lc-report/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/lc-report/SKILL.md).

This skill replaces the retired `narrative` skill: ASTRA's `narrative`
field was removed from the spec
([astra-spec RFC-0002](https://github.com/LightconeResearch/astra-spec/blob/main/rfcs/0002-decouple-reports.md))
in favor of external reports that reference analysis elements by path.

## Allowed tools

```text
Read, Write(*.md), Edit(*.md), Write(myst.yml), Edit(myst.yml),
Glob, Grep, Bash(myst:*), Bash(astra:*), Bash(lc:*), AskUserQuestion
```

Writes are locked to report pages and `myst.yml` — spec changes go
through the normal `astra.yaml` discipline, and `results/` is never
touched.

## Reference surfaces

The skill teaches the full MySTRA vocabulary (its
`references/mystra-syntax.md` carries the exact grammar):

| Surface | Use |
|---------|-----|
| `{astra}` role | mention an element inline |
| `{astra:ref}` | numbered figure/table reference |
| `{astra:cite}` / `{astra:cite:t}` | cite the literature behind an insight |
| `{astra:value}` | pull a measured number from materialized results |
| `{astra}` directive | embed a figure, decision, finding, or registry as a block |
| dotted-filename pages | one page per sub-analysis, root page as the end-to-end view |

The golden rule: **never hard-type a measured number, restate a
decision, or re-describe an output** — if it exists in `astra.yaml` or a
result product, reference it by path.

## Modes

What differs between modes is the second source paired with the spec:

- **Paper reproduction** — an authoritative text at `work/reference/`;
  the skill's `references/paper-reproduction.md` carries the
  paper-to-report mapping, paraphrase and confidence-register rules, and
  voice seams for reproducer-found divergences. `lc-from-paper`'s REVIEW
  close-out invokes the skill in this mode.
- **Co-drafting** — the user, in conversation; ask-first discipline and
  provisional voice for in-flight work.
- **Retrofit** — project artifacts (code, notebooks, commits); gaps are
  marked explicitly, never fabricated.

## Workflow

1. **Setup.** Read the spec tree, note the active universe, check
   materialization (`lc status`), and check the report scaffold —
   creating `myst.yml` + `index.md` if the project was never `lc init`'d.
2. **Structure.** Sections mirror the analysis (Introduction / Methods /
   Results / Discussion); multi-page along sub-analyses when the tree
   warrants it.
3. **Draft.** Methods → Results → Discussion → Introduction last (the
   opening compresses the rest). Every declared finding, load-bearing
   decision, and promoted output gets referenced somewhere.
4. **Validate.** `myst build --html`, then fix every `[mystra]` warning,
   error admonition, and inline `⟨value: …⟩` token, and rebuild until
   clean. Broken references render visibly; none survive in a finished
   report.

## Anti-patterns called out in the prompt

- Hard-typed numbers where `{astra:value}` can pull them live.
- Restating a decision's options in prose instead of embedding it.
- Registry dumping (`:::{astra} outputs` on every page) instead of
  composing.
- Wiki-style field primers as introductions.
- Skipping the `myst build` validation loop.

## Related

- [`/lc-new`](lc-new.md) suggests `/lc-report` once the spec is stable;
  [`/lc-from-code`](lc-from-code.md) offers it after outputs
  materialize; [`/lc-from-paper`](lc-from-paper.md)'s REVIEW close-out
  authors the report as a standard step.
- [MySTRA](https://github.com/LightconeResearch/MySTRA) — the MyST
  plugin that resolves `{astra}` references at build time.
