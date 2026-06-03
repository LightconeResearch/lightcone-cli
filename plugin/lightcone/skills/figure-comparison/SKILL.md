---
name: figure-comparison
description: >
  Build a self-contained HTML report comparing the figures, tables, and
  numerical results in lc-from-paper's `work/reference/` paper substrate
  against artifacts produced under `results/<universe>/`. When
  `comparison-report.yaml` or `targets/targets.md` exists, use that scoped
  target set first; otherwise fall back to paper-driven inventory from arXiv
  TeX or Docling/Pandoc artifacts under `work/reference/`. Images are
  base64-embedded; missing matches are flagged. Use when the user says
  "compare results", "side-by-side comparison", "build comparison HTML", or
  "did we reproduce the paper". Run from the project folder containing
  astra.yaml.
argument-hint: "[path to paper reference dir, e.g. work/reference/]"
---

# /figure-comparison

Generate a single self-contained HTML report (`.lightcone/comparison.html`)
that places paper reference artifacts from `work/reference/` on the left
and the project's reproduced artifacts from `results/<universe>/` on the
right, with red flags wherever a counterpart is missing. Images are embedded
as base64 so the HTML is portable. The helper script and intermediate
manifest also live under `.lightcone/` so they don't pollute the baseline
results.

## Setup

1. **Confirm project root.** Read `astra.yaml` in the cwd. If missing, ask:

   > "I do not see an `astra.yaml` here. Please `cd` to the ASTRA project
   > and re-invoke."

   Stop until resolved.

2. **Confirm results exist.** Default universe is `baseline`, unless
   `comparison-report.yaml` names reproduced files under another universe or
   the user supplied a universe explicitly. Check `ls results/<universe>/`.
   If the directory is missing or empty, ask:

   > "I cannot find populated results under `results/<universe>/`. Build the
   > universe first (`lc run --universe <universe>` or equivalent), then
   > re-invoke."

   Stop. Do NOT attempt to run the pipeline yourself -- this skill is
   read-only over the build artifacts.

3. **Locate the paper reference substrate.** The user may have passed a
   path. Resolve it in this order:

   1. If the argument is a directory containing `metadata.json`,
      `document.md`, `figures/`, or `tables/`, use that directory as the
      paper reference root.
   2. If the argument is an arXiv source directory containing `.tex` files,
      use it as `source_root`, and use its parent `work/reference/` as the
      paper reference root when that parent exists.
   3. If no argument was supplied, prefer lc-from-paper's layout:
      - `work/reference/source/` when arXiv TeX source exists. Use the TeX
        files there for labels/captions and the parsed artifacts under
        `work/reference/{figures,tables,metadata.json}` for renderable
        reference files.
      - `work/reference/document.md` plus
        `work/reference/{figures,tables,metadata.json}` when no TeX source
        exists. This is the PDF + Docling fallback from lc-from-paper.
   4. Only after lc-from-paper paths fail, look for a legacy unzipped arXiv
      dir in cwd: a directory containing both a `*.tex` file and figure
      files (`*.pdf`, `*.png`, `*.eps`). Common names: `paper_source/`,
      `arxiv_source/`, `*_Original_Paper/`.

   If no usable reference substrate is found, ask:

   > "Where is the paper reference directory? In a lc-from-paper project this
   > should usually be `work/reference/`, containing `document.md`,
   > `metadata.json`, and extracted `figures/` / `tables/`."

   If only `work/reference/paper.pdf` exists, ask the user to run the PARSE
   phase first so Docling or the TeX parser populates `work/reference/`.
   Do not compare directly against a whole PDF.

## Phase 1 -- Understand the paper's main results

Read, in this order:

1. **Scoped comparison artifacts, if present.**
   - If `comparison-report.yaml` exists, treat it as the highest-priority
     scope because it records what lc-from-paper actually compared. Use its
     `outputs:` entries, including `type`, `priority`, `paper_value`,
     `reproduced_value`, `reference_file`, `reproduced_file`, `match`, and
     `notes` when present.
   - Else if `targets/targets.md` exists, treat it as the scope ledger. Use
     only the targets it names, including out-of-scope notes, priorities,
     reference paths, expected values/trends, and output/spec-home pointers.
   - If neither file exists, use the default paper-driven flow below and
     build a best-effort report from `astra.yaml` plus `work/reference/`.

2. **`astra.yaml`** -- specifically `narrative.summary`, `narrative.outputs`,
   `narrative.findings`, `outputs:`, and `findings:` if present. Use it to
   map scoped targets to output IDs and to harvest declared findings. Do not
   assume ASTRA outputs have a dedicated filename-hint field; result paths
   come from the output ID and the result resolver in Phase 2.

3. **The paper reference substrate**, in this order:
   - Read `work/reference/metadata.json` when present. It is the primary
     index for paper figures and tables; its paths are relative to
     `work/reference/` and usually point into `figures/` or `tables/`.
   - If `work/reference/source/` exists, grep its TeX files for
     `\includegraphics`, `\label{fig:...}`, `\caption{...}`, and
     `\begin{table}` to recover labels/captions that metadata may have
     missed.
   - If only `work/reference/document.md` exists, use the markdown plus
     `metadata.json` as the source of captions, table text, and in-text
     numerical claims. This is the Docling/Pandoc fallback; preserve its
     line numbers and do not pretend it is TeX.
   - Grep the abstract, results, and discussion sections of the TeX or
     markdown source for in-text numerical claims that look like primary
     results -- typically a quantity with value + uncertainty (e.g.
     `$X = a \pm b$ unit`). Prefer values that `astra.yaml`'s `findings:`
     already names; do not try to extract every number in the paper.

   Do NOT read the paper wholesale. For long papers (>500 lines), read
   only the abstract, results, and discussion sections.

If the paper is large or has many sections and neither `comparison-report.yaml`
nor `targets/targets.md` exists, **delegate the figure / table / value
enumeration to a single subagent** with
`subagent_type="general-purpose"` -- pass it the paper path, the output
schema below, and ask it to return only the inventory. One subagent is
enough; do not fan out. Multiple subagents would have to re-read the
same file.

## Phase 2 -- Build the comparison manifest

Produce a manifest in memory (you'll write it as JSON in Phase 3) with
three sections: `figures`, `tables`, `values`. Each entry pairs a
paper-side artifact with a project-side artifact.

Build entries in this priority order:

1. **From `comparison-report.yaml` if present.** One manifest entry per
   `outputs.<output_id>` item. Use `type` to route it to `figures`,
   `tables`, or `values`. Use `reference_file` as the paper-side path and
   `reproduced_file` as the project-side path when present. Preserve the
   report's `paper_value`, `reproduced_value`, `match`, and `notes` in the
   manifest so the HTML reflects the completed COMPARE verdict.
2. **Else from `targets/targets.md` if present.** One manifest entry per
   in-scope target. Use each target's reference path under `targets/`, its
   expected values/trends, and its output/spec-home pointer. If the ledger
   marks a target out of scope, omit it from the HTML unless the user asked
   for out-of-scope targets too.
3. **Else use the default paper-driven inventory.** Enumerate figures,
   tables, and values from `astra.yaml` plus `work/reference/`, and fall back
   to filename-stem similarity only when no scoped ledger exists.

For project-side result paths, resolve every output ID with this order:
- Use an explicit `reproduced_file` from `comparison-report.yaml` or an
  explicit reproduced path/glob from `targets/targets.md`, if present and
  the file exists.
- Search for flat files at `results/<universe>/<output_id>.<ext>` with the
  first suitable type-specific extension: images (`.png`, `.jpg`, `.jpeg`,
  `.pdf`, `.eps`), tables (`.csv`, `.parquet`, `.md`, `.txt`), values
  (`.json`, `.yaml`, `.yml`, `.txt`, `.md`).
- If still unmatched and no scoped ledger exists, fall back to filename-stem
  similarity within `results/<universe>/`.
- If no match is found, use `project_path: null` and render a red
  `NOT PRODUCED` panel. Do not include unrelated result files; the report is
  target-driven when target/report files exist, and paper-driven otherwise.

For tables: use `work/reference/metadata.json` and `work/reference/tables/`
when present. If TeX source exists, capture the raw LaTeX of the `tabular`
block and any `\caption{...}`. If only `work/reference/document.md` exists,
capture the Docling/Pandoc markdown table or the extracted table artifact
under `work/reference/tables/`. The project side is whatever artifact
carries the same content -- typically a CSV / parquet / markdown file at
`results/<universe>/<output_id>.<ext>`. If `astra.yaml` declares no matching
output, use `project_path: null`. **If the paper contains no tables at all,
leave the manifest's `tables` list empty; the helper must omit the entire
Tables section from the HTML in that case (no header, no "no tables"
placeholder).**

For values: each entry is `{name, paper_value, paper_uncertainty?,
project_value?, project_value_source?, paper_quote}`. Pull
`paper_value` from the in-text claim or `astra.yaml`'s
`findings.*.paper_value`. Pull `project_value` from
`astra.yaml`'s `findings.*.replicated_value` if present, otherwise from
a scoped `comparison-report.yaml` entry or a flat result summary file at
`results/<universe>/<output_id>.<ext>` that you can read statically.
**Never compute or re-derive values yourself.** If no project value can
be located statically, leave it null and flag in the HTML.

When `comparison-report.yaml` or `targets/targets.md` exists, the values list
is scoped to that file. Otherwise, be exhaustive about values, not selective.
A common failure mode is the values section ending up with only 1--3 entries,
which makes the report feel thin. Aim for **every** numerical claim that the
paper asserts and the project tracks. Concretely, harvest from:
- Every entry under `findings:` in `astra.yaml` -- one manifest entry
  per finding, even when several findings share a parent quantity.
- The paper's abstract: every `<value> ± <unc> <unit>` it reports.
- The paper's results and discussion sections: every fitted parameter,
  every feature location ("dip near x = X₁", "peak at x = X₂"), every
  reported sample size after a specific cut, every bin width or step
  used as a result-defining choice, every reported accuracy / score /
  metric.
- Any explicit reproduction targets in `astra.yaml`'s `narrative.findings`.

It is fine to repeat one quantity in multiple manifest entries when the
paper reports it under different conditions (preliminary vs. final,
per-subset, per-bin median, per-method variant). Each condition is its
own row. Feature locations are values too: encode "feature located at
domain coordinate X" as
`{name: "<short feature name>", paper_value: "<X>", paper_unit:
"<unit>"}`. **Target ≥6 value entries on a typical paper.** If you end
up with fewer than 4, you are filtering too aggressively -- re-read
`astra.yaml`'s `findings:` and the paper's results section.

## Phase 3 -- Generate the HTML

Use a small Python helper rather than embedding base64 inline through
your tool calls -- multi-MB image base64 strings would balloon your
context.

Use the existing `.lightcone/` directory in the project root. Do not create
directories in this skill. All three files this skill writes -- manifest,
helper, and final HTML -- live there.

1. **Write the manifest** as JSON to
   `.lightcone/comparison_manifest.json`. Schema:

   ```json
   {
     "project_name": "...",
     "paper_path": "work/reference/document.md",
     "scope_source": "comparison-report.yaml",
     "universe": "baseline",
     "results_path": "results/baseline",
     "figures": [
       {
         "paper_label": "fig:main_result",
         "paper_caption": "...",
         "paper_path": "targets/main_result.pdf",
         "project_output_id": "primary_metric_plot",
         "project_path": "results/baseline/primary_metric_plot.png"
       }
     ],
     "tables": [
       {
         "paper_label": "tab:summary",
         "paper_caption": "...",
         "paper_latex": "\\begin{tabular}{...}\\end{tabular}",
         "project_output_id": "...",
         "project_path": "results/baseline/summary_table.csv"
       }
     ],
     "values": [
       {
         "name": "primary_metric",
         "paper_value": "12.5",
         "paper_uncertainty": "0.4",
         "paper_unit": "<unit>",
         "paper_quote": "we find $\\mathrm{metric} = 12.5 \\pm 0.4$ <unit>",
         "project_value": "12.47",
         "project_uncertainty": "0.41",
         "project_value_source": "results/baseline/metric.json"
       }
     ]
   }
   ```

   `figures`, `tables`, and `values` may each be `[]`. Empty lists mean
   the helper skips that section entirely. There is no
   `unmatched_baseline` field -- baseline files the paper does not
   reference are not in scope for this report.

   Use `null` for any missing field. Paths are relative to the project
   root.

2. **Write the helper script** to `.lightcone/build_comparison.py`.
   The helper must:
   - Read the manifest JSON.
   - For each figure entry: emit one `<section class="row">` per figure,
     with the structure described in **"Required HTML structure"**
     below -- a single `<div class="row-head">` containing a
     `<div class="row-title">` and one row-level status badge, followed
     by a `<div class="row-grid">` of two `<figure class="cell">`s
     (paper, project). One badge per row, in flow inside `.row-head`.
     **Never emit per-cell absolutely-positioned badges.**
     Read `paper_path` and `project_path` as bytes, base64-encode, and
     embed each image inside its cell. **PDFs must be converted to PNG
     before base64-encoding -- never embed PDFs as PDF data URIs.** Use
     `<img src="data:image/png;base64,...">` uniformly for every
     figure cell. Conversion order to try, falling back if a tool is
     unavailable:
       1. `pdf2image` (Python) -- `convert_from_path(path, dpi=150)[0]`
       2. `pypdfium2` -- render page 1 at 150 DPI to a PIL image
       3. shell out to `pdftoppm -png -r 150 -f 1 -l 1 <pdf> <stem>`
          and read the resulting PNG
       4. shell out to `magick <pdf>[0] -density 150 <png>` (ImageMagick)
     If none are available, the helper renders a small ⚠️ panel that
     says `PDF preview unavailable -- install pdf2image or pdftoppm`
     and links to the `.pdf` file path. Do not fall back to embedding
     the PDF binary. PNG / JPG inputs skip conversion and are
     base64-encoded directly. For any non-image type, embed as a
     UTF-8 text block. Missing path → render a red panel saying
     `❌ NOT PRODUCED` with the expected output ID. Captions live as
     `<figcaption>` inside each cell, never as a row-spanning element.
   - For each table entry: paper side renders the captured LaTeX inside
     `<pre>` plus the caption; project side renders the project file
     (CSV/parquet → first ~20 rows as an HTML table; markdown → render
     as `<pre>`; missing → red ❌ panel). Same row structure as figures.
   - For each value entry: emit one `<section class="row value-row">`
     per value -- **same card layout as figures, not a `<table>`.**
     The row has a `.row-head` (value name + single status badge),
     a `.row-grid` of two `.cell`s (paper | project), and a trailing
     `.value-note` with the σ delta. The paper cell shows the value
     (with uncertainty and unit) and the `paper_quote` as a
     `<blockquote>`. The project cell shows the value and the
     `project_value_source` as a small `<code>` line. Compute a simple
     status -- ✅ if both values exist and the project value lies within
     ±1 paper-uncertainty of the paper value; ⚠️ if both exist but
     disagree by more than that; ❌ if either is missing. If
     `paper_uncertainty` is null, fall back to a 5%-tolerance
     comparison: ✅ if `|prj − paper| ≤ max(0.05·|paper|, 0.05)`. Do
     NOT do anything more sophisticated; you cannot run code. **Do not
     render values as a single HTML `<table>`** -- the report's whole
     point is side-by-side cards.
   - Emit a single self-contained HTML file with inline CSS in the
     **Vellum** aesthetic (see below): the `<body>` carries the
     parchment background and grain, and **all content lives inside a
     single `<div class="page">` that is the lighter `--surface` cream
     card with soft drop shadows.** This is non-negotiable -- the cream
     page card on top of the parchment body is the headline visual. Two
     content columns (paper | project) per row, the project name in the
     `<h1>`, and a top-of-page summary line counting found / missing
     for each non-empty section. **Skip any section whose manifest list
     is empty** -- omit its header and content entirely; do not emit a
     "no tables found" placeholder.
   - Write the HTML to `.lightcone/comparison.html` and print the
     absolute path on stdout.

### Required HTML structure (figures and values)

The helper MUST produce this exact shape for every figure / value row.
Per-cell absolute badges, value-as-table, and missing `.row-head` are
all forbidden -- they break the layout (overlapping the cell heading,
losing the row-level status, breaking the visual rhythm with figures).

```html
<section class="row"><!-- or "row value-row" for values -->
  <div class="row-head">
    <div class="row-title">
      <code>fig:main_result</code> &mdash; <span class="row-id">primary_metric_plot</span>
    </div>
    <span class="badge badge-ok">✅ matched</span>
  </div>
  <div class="row-grid">
    <figure class="cell">
      <div class="cell-label">PAPER</div>
      <img src="data:image/png;base64,...">
      <figcaption>Caption from paper.</figcaption>
    </figure>
    <figure class="cell">
      <div class="cell-label">PROJECT &middot; <code>results/baseline/...</code></div>
      <img src="data:image/png;base64,...">
      <figcaption>output_id</figcaption>
    </figure>
  </div>
  <!-- value rows only: -->
  <div class="value-note">Δ = 0.03 &lt;unit&gt; (0.07σ)</div>
</section>
```

Status states for the row badge: `badge-ok` (matched), `badge-warn`
(partial / off-target / no σ), `badge-miss` (missing on either side).
Exactly one badge per row.

3. **Run the helper:** `python3 .lightcone/build_comparison.py`
   from the project root. If `python3` is missing, try `python`. If
   the helper imports anything beyond the standard library (e.g.
   `pyarrow` to read parquet, or `pandas` to render tables), have it
   gracefully fall back to "preview not available -- file exists at
   `<path>`" rather than failing. The helper must work with stdlib
   alone for the figure path; the parquet / pandas previews are
   nice-to-haves.

4. After the helper runs, **read back** the HTML's first ~50 lines and
   the absolute file size to verify it was produced and isn't trivially
   small (>10 KB sanity check). Then report to the user the path and a
   one-line summary:

   > Comparison HTML at `.lightcone/comparison.html` -- N figures
   > (K matched, J missing), N tables (...), N values (...).

## Vellum aesthetic

The helper must style the page in the **Vellum** aesthetic: a
weathered-parchment look that reads like a printed scientific paper,
not a web app. The helper bakes all of this into inline `<style>` --
no external assets, no CDN fetches, no JS.

**Palette (CSS custom properties on `:root`):**

```css
--paper:        #F2EDE5;  /* aged-paper page background */
--surface:      #FAFAF7;  /* lighter "protected" prose surface */
--ink:          #2E2A26;  /* warm near-black body text */
--ink-muted:    #6B635A;  /* brown-gray secondary text */
--gold:         #9A7B35;  /* antique gold -- links, accents, the author's hand */
--teal:         #4F7A6F;  /* faded ink: healthy / resolved (✅) */
--amber:        #B0823A;  /* faded ink: attention / partial (⚠️) */
--mauve:        #8A5C6B;  /* faded ink: error / missing (❌) */
--rule:         #D9CFC0;  /* hairlines and table borders */
--shadow:       rgba(46, 42, 38, 0.10);  /* soft ink-toned drop shadow */
```

Saturated colors are forbidden. Use only this palette plus tints/shades
of these tokens. Status icons (✅ ⚠️ ❌) are kept but their containers
adopt the corresponding faded ink (`--teal`, `--amber`, `--mauve`) for
borders and small badges -- never as full background fills.

**Typography:**

- Body prose: `EB Garamond`, fall back through `Garamond, "Times New
  Roman", Georgia, serif`. No system-ui, no sans-serif anywhere.
- Annotations, code, captions, file paths, numerical values:
  `JetBrains Mono`, fall back through `"IBM Plex Mono", "SFMono-Regular",
  Menlo, Consolas, monospace`.
- Body line-height ~1.55, comfortable measure (~70ch on prose blocks).
- Headings serif, semibold not bold; `<h1>` slightly tracked-out (small
  positive `letter-spacing`) for a hand-set feel. Section headings
  may use a small caps treatment (`font-variant: small-caps`).
- Do not load webfonts. The HTML must stay self-contained and offline-safe;
  rely on the fallback chains above.

**Texture and the page card:**

- The `<body>` background is `--paper` plus a barely-there fractal-noise
  grain. Generate the grain with an inline SVG `<feTurbulence>` filter
  baked into a `data:image/svg+xml;base64,...` URL used as
  `background-image`. Keep opacity low (~0.04--0.06) so the grain reads
  as paper fiber, not as visible noise.
- **Body padding around the page.** The `<body>` itself has padding
  (e.g. `padding: 4rem 2rem;`) so the parchment + grain breathes around
  the page card -- never edge-to-edge.
- **The page card is mandatory.** All content lives inside a single
  `<div class="page">` styled as:

  ```css
  .page {
    max-width: 64rem;
    margin: 0 auto;
    background: var(--surface);
    box-shadow: 0 1px 2px var(--shadow), 0 8px 24px var(--shadow);
    padding: 4rem 4rem 5rem;
  }
  ```

  The cream `--surface` card on top of the parchment `--paper` body is
  the single most important visual signature of the report. If you find
  yourself with `.page { background: transparent }` or no
  `box-shadow`, you have failed.
- Cells inside the page card sit on the same `--surface` with their own
  softer shadow (`0 1px 2px var(--shadow)`), creating two stacked
  layers of depth: parchment → page card → cell card.

**Surfaces and overlays:**

- Comparison rows are two-column on desktop (paper | project), single
  column on narrow viewports. Each cell is `--surface` with the soft
  ink shadow.
- Hover/active states are expressed as **candlelight-lift** (a warm
  cream highlight, e.g. `background: #FFF8E8;`) or **ink-sink** (a warm
  black inset, e.g. `background: #2E2A26; color: var(--paper);`) --
  never flat blue/gray fills.
- Hairlines between sections use `--rule`, never solid black.

**Chrome and links:**

- Links: `--gold`, no underline by default; underline appears as a
  1px `--gold` border-bottom on hover. The underline is the "author's
  hand" -- thin, deliberate.
- Buttons / interactive chrome: minimal. This is a report, not an app.
  Avoid icons beyond ✅ ⚠️ ❌ and small unicode dingbats.

**Whitespace and rhythm:**

- Generous outer margins; the page should feel narrow and read like a
  paginated paper. Max content width around 64rem.
- Section transitions get vertical room -- ~3rem between major
  sections, ~1.5rem between rows.
- Captions sit below figures in `--ink-muted` mono, italic if EB
  Garamond italics are loaded.

**Status badges (figure / table / value rows):**

- **One badge per row, in the `.row-head` flex container alongside the
  row title.** Never per-cell, never absolutely positioned. The badge
  uses `display: inline-flex` (or default inline) and lives in flow.
- Render the status (✅ matched / ⚠️ partial / ❌ missing) as a small
  monospace badge in the row head, using the corresponding semantic color
  as a 1px border + the same color tinted at 12% as the background. The
  icon plus a 1--3 word label ("matched", "missing", "off by 2.1σ"). Never
  a saturated banner.
- Status reflects the **row as a whole**, not each cell individually:
  ✅ when both paper and project artifacts are present (and, for
  values, the project number is within tolerance); ⚠️ when both are
  present but the value disagrees beyond tolerance, or a paper figure
  has no project counterpart that you'd still like to flag as partial;
  ❌ when either side is missing.

**The overall feel:** scholarly, low-contrast, hand-made, generous
whitespace, chrome recedes, the page itself carries the eye. If a
choice feels modern (sharp shadows, saturated badges, system-ui type,
solid-fill buttons), it is wrong.

## Restrictions

- You MUST NOT run the pipeline, recipes, `lc run`, or any code that
  computes new results. The results directory is read-only input here.
- You MUST NOT modify project source code, `astra.yaml`, or anything in
  `scripts/` or `results/`. The only files this skill writes are
  `.lightcone/comparison_manifest.json`,
  `.lightcone/build_comparison.py`, and
  `.lightcone/comparison.html`. Assume `.lightcone/` already exists; never
  write into `results/`.
- You MUST NOT fabricate values. If a paper number is not stated in the
  paper source, `targets/targets.md`, `comparison-report.yaml`, or
  `astra.yaml`, leave it null. If a project number is not recorded in a
  result file or comparison report, leave it null. Flag, don't fill.
- You MUST embed every image as base64 -- the HTML must be portable to
  another machine without breaking image references.
- You MUST NOT write the HTML by hand with inlined base64 strings; use
  the helper script. (Multi-MB base64 in tool-call arguments is what
  this rule prevents.)

## Anti-patterns

- **Running the pipeline to fill in a missing value** -- the whole point
  is to surface what is missing; never paper over a gap.
- **Embedding PDFs as PDFs** -- PDFs must be rasterized to PNG before
  base64-encoding. Browsers can technically render PDF data URIs, but
  they break consistent layout, scale poorly, and force a viewer
  chrome we cannot style. Convert to PNG via `pdf2image` /
  `pypdfium2` / `pdftoppm` / ImageMagick (in that fallback order); if
  none are available, render a ⚠️ placeholder rather than embedding
  the PDF.
- **Statistical comparison beyond ±1σ** -- this skill is a static visual
  comparison plus a coarse value check. Do not compute KS tests, Δχ²,
  or anything else. The user can eyeball the figures.
- **Reading the paper wholesale** -- limit reads to abstract, results,
  discussion; or delegate the inventory pass to one subagent.
- **Bundling matching into the helper script** -- the helper's job is
  rendering, not deciding which paper figure pairs with which baseline
  file. Do all matching in Phase 2 (manifest construction) so a human
  can audit the pairings by reading the JSON.
- **Silent overwrites** -- if `.lightcone/comparison.html` already
  exists, mention it in the summary line ("overwrote previous report").
- **Modern web-app styling** -- saturated brand colors, system-ui type,
  flat-fill buttons, sharp drop shadows, dark-mode toggles, animated
  transitions. The Vellum aesthetic is non-negotiable; if you find
  yourself reaching for `#0d6efd` or `font-family: system-ui`, stop.
- **Missing page card.** The single `<div class="page">` with
  `background: var(--surface)` + soft drop shadow is the headline
  visual. A page that lets the parchment grain reach edge-to-edge with
  no cream card on top is broken. Always check the rendered HTML has
  `.page { background: var(--surface); box-shadow: ... }`.
- **Per-cell absolutely-positioned badges.** Status badges live inside
  one `.row-head` per row, in flow next to the row title -- never
  `position: absolute; top: 0.7rem; right: 0.8rem;` inside each cell.
  The absolute positioning overlaps the cell heading and emits a
  "rendered" badge per existing file regardless of the row's overall
  comparison state, which destroys the at-a-glance status signal.
- **Values rendered as a `<table>`.** Values must use the same card
  layout as figures (`.row` → `.row-head` + `.row-grid` of two
  `.cell`s). Collapsing the values section to an HTML table looks like
  a spreadsheet and breaks visual rhythm with the figures section.
- **Thin values list.** Aim for ≥6 value entries on a typical paper.
  If the manifest ends up with 1--3 values, the report feels empty;
  re-harvest from `astra.yaml`'s `findings:` and the paper's results
  section before generating.
