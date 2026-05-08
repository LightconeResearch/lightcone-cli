# Path A — arXiv LaTeX source (primary)

When the paper has an arXiv ID, the LaTeX source tarball is the substrate. Math, ligatures, captions, tables, and bibliography all come through clean — none of the rendering artifacts that plague PDF extraction.

## Acquire the source tarball

```bash
ARXIV_ID="2503.19441"  # adapt
curl -L -o /tmp/${ARXIV_ID}.tar.gz "https://arxiv.org/src/${ARXIV_ID}"
mkdir -p work/reference/source
cd work/reference/source && tar -xzf /tmp/${ARXIV_ID}.tar.gz
```

Identify the main `.tex` file (the one with `\documentclass`):

```bash
grep -l '\\documentclass' work/reference/source/*.tex | head -1
```

Symlink that file as `work/reference/paper.tex` so downstream consumers have a stable handle:

```bash
MAIN_TEX=$(grep -l '\\documentclass' work/reference/source/*.tex | head -1)
ln -sf "source/$(basename "$MAIN_TEX")" work/reference/paper.tex
```

## Fetch the PDF

```bash
curl -L -o work/reference/paper.pdf "https://arxiv.org/pdf/${ARXIV_ID}"
file work/reference/paper.pdf  # must say "PDF document"
```

## What downstream gets

- `work/reference/source/` — the full extracted tarball (everything: `.tex`, `.bbl`, `.bib`, figure files, tables, supplementary `.tex` files).
- `work/reference/paper.tex` — symlink to the main `.tex` file so consumers don't have to re-detect it.
- `work/reference/paper.pdf` — cached PDF for evidence verification.

No conversion to markdown is needed. Claude reads LaTeX directly; converting to markdown only loses information (math collapse, label resolution, caption flattening). Consumers of `work/reference/` read `.tex` and resolve `\ref{}` against `\label{}` in the source tree.

## Notes

- **arXiv DOI form is `10.48550/arXiv.<id>`.** Useful when downstream tools want a DOI rather than an arXiv ID.
- **Equation numbers and section numbers must match the rendered paper.** When a downstream consumer cites "eq. N" or "§N", they should find the equation by content, not by counting TeX blocks. Reach for the cached PDF if you need to confirm a printed number.
- **`\input{}` and `\include{}` chains** are common — the main `.tex` may pull section content from sibling files. Downstream consumers should grep across the whole `source/` tree, not just `paper.tex`, when searching for content.
- **If the tarball download fails** (rare: typically a transient HTTP error or a paper still in moderation), retry once. If it still fails, the paper may need to come in as Path B (DOI-only). Write `work/reference/extraction-error.txt` with the cause and surface to the user.
