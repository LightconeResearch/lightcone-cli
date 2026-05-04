# ACQUIRE — fetch the paper and code

Acquire the paper's full text and (when available) its reference code repository. The bundle's primary acquisition path is **arXiv LaTeX source via `/managing-bibliography`**; PDF + Docling is the fallback for non-arXiv papers.

The constitution's per-phase mode controls whether this runs interactively or as a sub-agent. Default is sub-agent.

## Inputs

- The paper's DOI or arXiv ID (from the constitution)
- An optional code repo URL (from the interview, if the user knew it)

## Outputs

- `work/reference/document.md` — paper as markdown (LaTeX-rendered when arXiv source available; Docling-extracted for PDF fallback)
- `work/reference/paper.pdf` — paper PDF (still needed for evidence verification via `astra validate --verify-evidence`)
- `work/reference/figures/`, `work/reference/tables/`, `work/reference/metadata.json` — extracted artifacts (PARSE may move some of this to `work/reference/`)
- `work/reference/code/` — clone of the code repo (or absent if not found)
- `work/reference/code-status.yaml` — record of where the code came from

## Step 1: Acquire the paper text

### Path A — arXiv ID is available (preferred)

Invoke `/managing-bibliography`. Use it to download the arXiv LaTeX source tarball:

```bash
curl -L -o /tmp/<arxiv-id>.tar.gz "https://arxiv.org/src/<arxiv-id>"
mkdir -p work/reference/source && cd work/reference/source && tar -xzf /tmp/<arxiv-id>.tar.gz
ls *.tex
```

The LaTeX source gives clean equations, captions, tables, and bibliography — none of the math collapse, ligature artifacts, or caption flattening that plagues PDF extraction. Use the main `.tex` file as the primary text source. Render it to markdown if a downstream phase needs that form (`pandoc`, or just preserve TeX where it is).

Also cache the paper for ASTRA's evidence-verification surface:

```bash
astra paper add 10.48550/arXiv.<arxiv-id>
cp "$(astra paper path 10.48550/arXiv.<arxiv-id>)" work/reference/paper.pdf
```

`astra paper add` for arXiv DOIs fetches the PDF directly. The PDF stays as a backup for `astra validate --verify-evidence`, even though the LaTeX source is the primary text.

### Path B — non-arXiv paper (PDF + Docling fallback)

```bash
astra paper add <DOI>
cp "$(astra paper path <DOI>)" work/reference/paper.pdf
file work/reference/paper.pdf
```

The `file` output must say "PDF document". If it says "HTML document" or anything else, the download was blocked (CAPTCHA, paywall). Search the web for an open-access copy (NASA ADS, arXiv, Unpaywall, Semantic Scholar, the journal's open-access link), download with `curl -L -o work/reference/paper.pdf <url>`, re-validate, then `astra paper add <DOI> --pdf work/reference/paper.pdf` to register the resolved file.

If a valid PDF cannot be obtained, write a clear error to `work/reference/acquire-error.txt` and stop.

Skip Step 1 if `work/reference/paper.pdf` already exists and is a valid PDF.

## Step 2: Search for the code repository

1. Search the paper text for repository URLs — abstract, intro, conclusion, footnotes, "Code Availability" or "Data Availability" sections.
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

Spend no more than a few searches before recording failure and moving on. **Do NOT modify cloned code.**

Skip Step 2 if `work/reference/code/` already exists.

## Survey signals (entry into ACQUIRE)

Run `ls work/reference/` first. If `paper.pdf` and `document.md` (or `source/` for arXiv) are present, ACQUIRE is done. If only `paper.pdf` is present, PARSE handles the rest. If nothing is there, run ACQUIRE.

## Notes

- **arXiv DOI form is `10.48550/arXiv.<id>`.** `astra paper add` accepts that form directly.
- **Journal DOIs that 403 on Unpaywall** can be aliased to a locally-downloaded arXiv preprint via `astra paper add <JOURNAL_DOI> --pdf <path-to-arxiv-pdf>`.
- This phase's job is acquisition, not understanding. Do not start summarizing the paper here — that's SUMMARIZE.
