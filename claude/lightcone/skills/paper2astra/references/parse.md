# PARSE — structure the paper

Turn the acquired paper into structured artifacts the rest of the pipeline can consume: markdown text, individual figures, individual tables, and a metadata index. This is mostly a deterministic pre-processing step.

The constitution's per-phase mode controls interactive vs sub-agent. Default is sub-agent.

## Inputs

- `work/reference/source/` — arXiv LaTeX source tree (Path A from ACQUIRE), or
- `work/reference/paper.pdf` — PDF (Path B fallback)

## Outputs

- `work/reference/document.md` — paper as markdown
- `work/reference/figures/` — extracted figures (PNG / PDF / vector)
- `work/reference/tables/` — extracted tables (CSV when machine-readable, MD otherwise)
- `work/reference/metadata.json` — index of figures and tables with captions and page numbers

## Path A — arXiv LaTeX source (when `work/reference/source/` exists)

The LaTeX source is already structured — sections are `\section{}`, equations are TeX, figures cite their files by name, tables are `tabular` environments. Convert to markdown while preserving equation TeX:

```bash
# Find the main file (usually has \documentclass at the top)
grep -l '\\documentclass' work/reference/source/*.tex

# Convert with pandoc, preserving math and structure
pandoc -f latex -t markdown -o work/reference/document.md work/reference/source/<main>.tex
```

Adjust pandoc invocation if the main file uses `\input{}` heavily — pandoc resolves them when run from the right cwd. Verify the output by reading the first ~200 lines and checking the section structure looks sensible.

Extract figure files from the source tree into `work/reference/figures/`:

```bash
mkdir -p work/reference/figures
# Copy referenced figure files; common extensions are .pdf .png .eps .jpg
find work/reference/source -type f \( -name "*.pdf" -o -name "*.png" -o -name "*.eps" -o -name "*.jpg" \) \
    -not -path "*/aux/*" -exec cp {} work/reference/figures/ \;
```

For tables, the LaTeX `tabular` blocks remain as TeX inside the rendered markdown. If a downstream phase needs them as CSV, extract them on demand.

Build `work/reference/metadata.json` — index of figures and tables. The structure:

```json
{
  "figures": [
    {"id": "fig1", "caption": "...", "file": "figures/fig1.pdf", "label": "fig:bao"}
  ],
  "tables": [
    {"id": "tab1", "caption": "...", "file": "tables/tab1.csv", "label": "tab:results"}
  ]
}
```

The `label` field is the LaTeX `\label{}` so SPECIFY's anchor work and EXTRACT_TARGETS' selection can both reference the same artifact.

## Path B — PDF fallback (when `work/reference/source/` does not exist)

Use Docling — the lightcone-cli stack ships its CLI:

```bash
# Run Docling against the PDF; outputs into work/reference/
docling --output work/reference work/reference/paper.pdf
```

Docling produces `document.md`, `figures/`, `tables/`, and `metadata.json` with the same shape Path A produces.

If Docling fails, the PDF may be corrupt — re-run ACQUIRE's download step before giving up.

## Survey signals (entry into PARSE)

If `work/reference/document.md` exists and `work/reference/metadata.json` exists, PARSE is done — proceed to SUMMARIZE.

## Notes

- **Path A is preferred whenever arXiv source was acquired.** PDF + Docling is the fallback for non-arXiv papers, not the default. The bundle's design philosophy is that math, ligatures, and caption fidelity are easier from LaTeX source than from re-extracted PDF text.
- **Equation numbers and section numbers must match the rendered paper.** Whether you use Path A or Path B, downstream phases (SPECIFY's evidence quotes, COMPARE's references) cite "eq. N" or "§N" by the printed number. Verify by spot-checking against the PDF.
