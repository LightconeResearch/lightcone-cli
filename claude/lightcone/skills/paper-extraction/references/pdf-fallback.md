# Path B — PDF + Docling (fallback for non-arXiv)

When the paper does not have an arXiv preprint, the PDF is the only substrate. Docling produces a structured representation (markdown + figures + tables + metadata) that downstream consumers read instead of the raw PDF.

This path is a **fallback**. Whenever Path A is available, prefer it.

## Acquire the PDF

Resolve the DOI to a PDF. The straightforward path:

```bash
curl -L -o work/reference/paper.pdf "https://doi.org/<DOI>"
file work/reference/paper.pdf
```

The `file` output must say "PDF document". If it says "HTML document" or anything else, the download was blocked (CAPTCHA, paywall, journal redirect):

1. Search for an open-access copy: NASA ADS, arXiv, Unpaywall, Semantic Scholar, or the journal's open-access link.
2. Download with `curl -L -o work/reference/paper.pdf <url>`.
3. Re-check with `file work/reference/paper.pdf`.

If a valid PDF cannot be obtained, write a clear error to `work/reference/extraction-error.txt` and stop. Do not try to extract structure from a non-PDF.

## Run Docling

```bash
docling --output work/reference work/reference/paper.pdf
```

Docling produces, directly into `work/reference/`:

- `document.md` — paper as markdown
- `figures/` — extracted figures (one file per figure)
- `tables/` — extracted tables (one file per table)
- `metadata.json` — figure / table index with captions, page numbers, and labels (where Docling can extract them)

The `metadata.json` shape Docling emits:

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

The `label` field is the source label where Docling can extract it; consumers reading `index.json` use it to anchor references back to the paper.

If Docling fails, the PDF may be corrupt — re-download once, then surface to the user if it still fails.

## What downstream gets

- `work/reference/document.md` — paper as markdown.
- `work/reference/figures/`, `work/reference/tables/` — already populated by Docling.
- `work/reference/metadata.json` — Docling's own index; the extraction script reads this and folds figures + tables into the unified `work/reference/index.json`.
- `work/reference/paper.pdf` — the PDF.

No `paper.tex` and no `source/` on Path B. Consumers detect the path by reading `index.json`'s `path` field (`"A"` or `"B"`).

## Notes

- **Outline extraction and citation-invocation extraction don't run on Path B.** No LaTeX source means no `\section{}` or `\cite{}` markers to walk in the paper body. Bibliography resolution *does* run — the script parses the references section at the tail of `document.md`, synthesizes `<lastname>_<year>` keys (with letter-suffix disambiguation for collisions), and resolves DOIs the same way as Path A. So the `citations:` block is populated with citation text + DOI, but each entry's `locations:` array is empty (the paper-side `\cite`-style invocations weren't extracted from prose). `extraction_warnings` flags both gaps.
- **Journal DOIs that 403 on Unpaywall** sometimes have an arXiv preprint twin. When that's available, treat the paper as Path A using the arXiv ID — the LaTeX-source surface is far cleaner than any PDF extraction.
