# /figure-comparison

Build a self-contained HTML report (`.lightcone/comparison.html`) that
places paper reference artifacts on the left and reproduced artifacts
on the right, with red flags wherever a counterpart is missing. Images
are base64-embedded so the HTML is portable. Run from a project folder
containing `astra.yaml`.

Source: [`claude/lightcone/skills/figure-comparison/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/figure-comparison/SKILL.md).

Argument hint: `[path to paper reference dir, e.g. work/reference/]`.

## Allowed tools

```
Read, Write, Glob, Grep,
Bash(ls:*), Bash(wc:*), Bash(grep:*), Bash(find:*), Bash(file:*),
Bash(python3:*), Bash(python:*), Bash(base64:*),
AskUserQuestion, Agent
```

Read-only over the build artifacts. The skill never invokes the
pipeline itself — if `results/<universe>/` is empty, it tells the user
to run `lc run` first and stops.

## Setup

1. **Confirm project root.** Reads `astra.yaml` in the cwd. If missing,
   asks the user to `cd` to the ASTRA project.
2. **Confirm results exist.** Default universe is `baseline`, unless
   `comparison-report.yaml` names another universe or the user
   supplied one. Checks `ls results/<universe>/`.
3. **Locate the paper reference substrate.** In order: a path passed as
   an argument, then `work/reference/` from lc-from-paper's layout —
   `index.json` (the canonical structural index of figures and tables,
   written by `/paper-extraction` on both paths), plus `source/` for
   arXiv TeX or `document.md` for the Docling fallback, plus the
   extracted `figures/` and `tables/`. Legacy locations are tried only
   after lc-from-paper paths fail. A stray `metadata.json` from the
   Docling path is ignored — its content is already folded into
   `index.json`.

## Scope resolution

The skill picks its target set in priority order:

1. **`comparison-report.yaml`** — the highest-priority scope when
   lc-from-paper has run COMPARE. Records exactly what to compare,
   including `type`, `priority`, paper/reproduced values, file paths,
   and match status.
2. **`targets/targets.md`** — the SPECIFY-phase scope ledger, used
   when COMPARE hasn't run yet.
3. **Default paper-driven flow** — when neither scope file exists,
   builds a best-effort report from `astra.yaml`'s `description` and
   `findings:` plus `work/reference/`.

## Resolving the reproduced side

Every `lc run` output is a **directory** —
`results/<universe>/<output_id>/` containing the artifact file(s) plus
`.lightcone-manifest.json`. The skill globs *inside* that directory for
the first suitable type-specific extension (images, tables, values),
always ignoring `.lightcone-manifest.json` and `.snakemake_timestamp`.
An explicit `reproduced_file` from the scope file wins; filename-stem
similarity across `results/<universe>/*/` is the last resort, and an
unmatched target renders as a red `NOT PRODUCED` panel rather than being
guessed at.

## Output

A single `.lightcone/comparison.html` with paper artifacts on the left
and reproduced artifacts on the right. Helper scripts and intermediate
manifests also live under `.lightcone/` so they don't pollute the
baseline results. The skill never creates `.lightcone/` — it exists in
any `lc init`-ed project, and its absence means the project isn't
initialized.

The HTML embeds figure images as base64 — paste it into email, drop
it on a shared drive, or send it through Slack without breaking links.

## When to invoke

- From `/lc-from-paper`'s REVIEW close-out (mandatory).
- Standalone, any time after `lc run` succeeds, to see how the
  reproduction stacks up against the paper.

## Hard rules

- **Read-only over build artifacts.** Never run the pipeline; if
  outputs are missing, stop and ask the user to build first.
- **Don't compare directly against a whole PDF.** When only
  `work/reference/paper.pdf` exists, ask the user to run
  `/paper-extraction` first (in lc-from-paper projects this happens
  during ORIENT).
- **Preserve scope ordering.** `comparison-report.yaml` wins over
  `targets/targets.md` wins over the default flow.

## Related

- [`/lc-from-paper`](lc-from-paper.md) — invokes `/figure-comparison`
  during REVIEW (mandatory).
- [`/paper-extraction`](paper-extraction.md) — produces the
  `work/reference/` substrate this skill reads.
- [`/check-sentence-by-sentence`](check-sentence-by-sentence.md) —
  the other REVIEW close-out, paper-vs-code rather than
  artifact-vs-artifact.
