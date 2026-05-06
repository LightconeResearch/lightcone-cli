# ACQUIRE — fetch the paper, structure it, clone the code

Acquire the paper's full text, structure it for downstream consumption, and (when available) clone the reference code repository. The bundle's primary acquisition path is **arXiv LaTeX source via `/managing-bibliography`**; PDF + Docling is the fallback for non-arXiv papers. ACQUIRE folds in what was previously a separate PARSE phase — for arXiv-LaTeX, the structure is already in the tarball (no extra work); for the PDF fallback, ACQUIRE runs Docling itself.

The constitution's per-phase mode controls whether this runs interactively or as a sub-agent. Default is sub-agent — surfacing happens only on download failures.

## Inputs

- The paper's DOI or arXiv ID (from the constitution)
- An optional code repo URL (from the interview, if the user knew it)

## Outputs

Two shapes depending on the acquisition path:

**Path A — arXiv LaTeX source:**

- `work/reference/source/` — extracted arXiv tarball (the canonical text source: `.tex`, `.bbl`, figure files, etc.)
- `work/reference/paper.pdf` — paper PDF (kept as a backup for `astra validate --verify-evidence`)

**Path B — PDF + Docling fallback:**

- `work/reference/document.md` — paper as markdown (Docling-extracted)
- `work/reference/figures/` — extracted figures
- `work/reference/tables/` — extracted tables
- `work/reference/metadata.json` — figure / table index with captions and page numbers
- `work/reference/paper.pdf` — paper PDF

**Both paths:**

- `work/reference/code/` — clone of the code repo (or absent if not found)
- `work/reference/code-status.yaml` — record of where the code came from

## Step 1: Acquire and structure the paper text

### Path A — arXiv ID is available (preferred)

Invoke `/managing-bibliography`. Use it to download the arXiv LaTeX source tarball:

```bash
curl -L -o /tmp/<arxiv-id>.tar.gz "https://arxiv.org/src/<arxiv-id>"
mkdir -p work/reference/source && cd work/reference/source && tar -xzf /tmp/<arxiv-id>.tar.gz
ls *.tex
```

The LaTeX source gives clean equations, captions, tables, and bibliography — none of the math collapse, ligature artifacts, or caption flattening that plagues PDF extraction. **No conversion to markdown is needed.** Downstream phases (STUDY's section sub-agents, SPECIFY's evidence quotes) read `.tex` directly — Claude reads LaTeX fine, and rendering it to markdown only loses information. The tarball stays as `work/reference/source/`.

If you want to identify the main `.tex` file for downstream tools:

```bash
grep -l '\\documentclass' work/reference/source/*.tex
```

Cache the paper for ASTRA's evidence-verification surface:

```bash
astra paper add 10.48550/arXiv.<arxiv-id>
cp "$(astra paper path 10.48550/arXiv.<arxiv-id>)" work/reference/paper.pdf
```

`astra paper add` for arXiv DOIs fetches the PDF directly. The PDF stays as a backup for `astra validate --verify-evidence`, even though the LaTeX source is the primary text.

There is no PARSE step on Path A. Equation numbers, section numbers, figure references — all preserved in the source. STUDY's sub-agents resolve `\ref{}` against `\label{}` directly in the source tree.

### Path B — non-arXiv paper (PDF + Docling fallback)

```bash
astra paper add <DOI>
cp "$(astra paper path <DOI>)" work/reference/paper.pdf
file work/reference/paper.pdf
```

The `file` output must say "PDF document". If it says "HTML document" or anything else, the download was blocked (CAPTCHA, paywall). Search the web for an open-access copy (NASA ADS, arXiv, Unpaywall, Semantic Scholar, the journal's open-access link), download with `curl -L -o work/reference/paper.pdf <url>`, re-validate, then `astra paper add <DOI> --pdf work/reference/paper.pdf` to register the resolved file.

If a valid PDF cannot be obtained, write a clear error to `work/reference/acquire-error.txt` and stop.

Then run Docling to structure the PDF — without this, downstream phases have nothing to read but the raw PDF:

```bash
docling --output work/reference work/reference/paper.pdf
```

Docling produces `document.md`, `figures/`, `tables/`, and `metadata.json` directly into `work/reference/`. The `metadata.json` index has the shape:

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

The `label` field is the source label (where Docling can extract it) so SPECIFY's anchor work can reference the same artifact.

If Docling fails, the PDF may be corrupt — re-download before giving up.

Skip Step 1 if the path's outputs already exist (`work/reference/source/` for Path A, `work/reference/document.md` for Path B).

## Step 2: Search for the code repository

This step matters more than its size suggests. When `work/reference/code/` exists, every implementing iteration treats it as canonical for numerics + method (the canonical-resolution rule, recorded in CLAUDE.md). Without it, iterations have only the paper to anchor to and drift toward "looks right" rather than "matches."

1. Search the paper text for repository URLs — abstract, intro, conclusion, footnotes, "Code Availability" or "Data Availability" sections. (Path A: grep across `work/reference/source/*.tex`. Path B: grep `work/reference/document.md`.)
2. If none found, web search: paper title + "github", Papers With Code, or the first author's GitHub profile.
3. Clone if found:
   ```bash
   git clone --depth 1 <url> work/reference/code
   ```
4. Write `work/reference/code-status.yaml`:
   ```yaml
   found: true        # or false
   url: "https://..."  # null if not found
   cloned: true       # false if found but clone failed
   notes: "..."
   ```

Spend no more than a few searches before recording failure and moving on. **Do NOT modify cloned code** — it's the reference, not the workdir.

Skip Step 2 if `work/reference/code/` already exists.

## Survey signals (entry into ACQUIRE)

Run `ls work/reference/` first.

- If `paper.pdf` is present and either `source/` (Path A) or `document.md` (Path B) is also present, ACQUIRE is done — proceed to STUDY.
- If `paper.pdf` is present but neither structure exists, run the structuring step for the appropriate path.
- If nothing is there, run the full ACQUIRE.

## Notes

- **arXiv DOI form is `10.48550/arXiv.<id>`.** `astra paper add` accepts that form directly.
- **Journal DOIs that 403 on Unpaywall** can be aliased to a locally-downloaded arXiv preprint via `astra paper add <JOURNAL_DOI> --pdf <path-to-arxiv-pdf>`.
- **Path A is preferred whenever arXiv source is acquirable.** Math, ligatures, and caption fidelity all come through clean from the LaTeX source; PDF + Docling is the fallback for non-arXiv where there's no better source. The acquisition layer's ASTRA-side counterpart — `astra paper add` preferring LaTeX over PDF for the verification cache, and applying the same logic to bibliography references — is filed as a separate ASTRA issue; paper2astra inherits the improvement once it lands.
- **Equation numbers and section numbers must match the rendered paper.** On Path A, the printed numbers come from the rendered tarball (look at the PDF if uncertain). On Path B, Docling preserves printed numbers in its markdown output. When citing "eq. N" or "§N" in any downstream phase, find the equation or heading by content, not by a naïve count of TeX blocks or markdown headings.
- This phase's job is acquisition + structuring, not understanding. Do not start summarizing or comparing the paper here — that's STUDY.
