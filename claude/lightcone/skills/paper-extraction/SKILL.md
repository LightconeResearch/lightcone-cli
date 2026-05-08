---
name: paper-extraction
description: >
  Turn an arXiv ID or DOI into a standardized `work/reference/` directory:
  paper substrate (arXiv LaTeX source primary, PDF + Docling fallback),
  copied figure files, per-table `.tex` files, section outline with line
  numbers, deduplicated citation keys with every location they appear,
  abstract, embedded bibliography (when present in source), and a valid
  `astra.yaml` representing the paper as an ASTRA artifact (with the
  paper's claimed numerical findings as ASTRA `findings:`). Emits a
  top-level `index.json` for the structural surface plus the `astra.yaml`
  for the semantic surface. Triggers on: "read paper", "prep paper",
  "ingest paper", "extract paper", "set up paper", "fetch arxiv", "arxiv
  id", "DOI", "find paper", or `/paper-extraction <id>`.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch
---

# paper-extraction

Turn a DOI or arXiv ID into a standardized, indexed `work/reference/` directory. One entry-point, idempotent, self-contained.

The output is a predictable surface anyone can rely on without re-parsing LaTeX. What a consumer does with that surface is their concern — paper-extraction's job ends at the index.

## When to use

- "Read [paper] end-to-end" / "I want to verify a claim in [paper]" — full source plus structured artifacts so you're reading the actual paper, not a flattened PDF
- "Set up reading materials for [paper]" — when the next thing you'll do involves browsing figures, citations, or section structure and you don't want to grep the tarball every time
- Any workflow where another skill or process needs a known directory shape per paper

## Outputs

Under `work/reference/` (idempotent — skips work already done):

```
work/reference/
├── index.json                # structural index — figures, tables, outline, citations, paths
├── astra.yaml                # ASTRA-shape representation: the paper as an ASTRA artifact, including findings
├── paper.pdf                 # always
├── paper.tex                 # Path A — symlink to the main .tex file
│   (or)
├── document.md               # Path B — Docling-extracted markdown
├── source/                   # Path A — extracted arXiv tarball (full source tree)
├── figures/                  # figure files (copied from LaTeX or rendered by Docling)
├── tables/                   # one .tex file per `\begin{table}` block (Path A)
├── bibliography-source.bib   # Path A only — copy of any .bib found in source/
└── bibliography-source.bbl   # Path A only — copy of any .bbl found in source/
```

The skill produces only the paper's own reading materials. Anything not contained in or derived from the paper itself — code repositories, supplementary datasets, related papers — is out of scope; the caller handles those.

### Two surfaces: `index.json` (structural) and `astra.yaml` (semantic)

**`index.json` is structural and machine-friendly.** Everything the script could mechanically extract: figures, tables, section outline with line numbers, citation keys with every location, abstract, paths. Read this when you want to know "what's in this paper, where do I find it." Sample shape:

```json
{
  "path": "A",                                  // or "B"
  "paper_pdf": "paper.pdf",
  "paper_tex": "paper.tex",                     // null on Path B
  "source_dir": "source",                       // null on Path B
  "document_md": null,                          // "document.md" on Path B
  "bibliography_source_bib": "bibliography-source.bib",
  "bibliography_source_bbl": null,
  "astra_yaml": "astra.yaml",
  "title": "UNIONS-3500 Weak Lensing: B-mode validation",
  "abstract": "At Stage-III sensitivities, cosmic shear B modes ...",
  "figures": [
    {"id": "fig1", "label": "fig:bao", "caption": "...", "source_path": "fig_bao",
     "file": "figures/fig_bao.pdf", "block_origin": "main.tex", "line": 412}
  ],
  "tables": [
    {"id": "tab1", "label": "tab:cosmo", "caption": "...", "file": "tables/tab-cosmo.tex",
     "block_origin": "main.tex", "line": 487}
  ],
  "outline": [
    {"level": 1, "title": "Introduction", "label": "sec:intro", "source_file": "main.tex", "line": 157}
  ],
  "citations": {
    "asgari17": [{"file": "main.tex", "line": 178}, {"file": "main.tex", "line": 561}],
    "smith2024": [{"file": "main.tex", "line": 92}]
  },
  "extraction_warnings": [
    "figure fig3: \\includegraphics{...} could not resolve to a file in source/"
  ]
}
```

**`astra.yaml` is semantic and ASTRA-validating.** Treats the paper as an ASTRA artifact: `id`, `version`, `name`, `narrative.summary`, and `findings:` carrying the paper's claimed numerical results in ASTRA's Insight + Evidence shape. Read this when you want to know "what does this paper claim, with quote evidence anchored to the source." The script writes a stub (id, version, name, narrative.summary from abstract, empty findings); Step 5 fills in `findings:`.

Why both: the structural index is queryable by any consumer (`grep`, `jq`, agent code) without needing to know about ASTRA. The ASTRA file composes directly into reproductions, MySTRA, and any other ASTRA-aware tool — and the verbosity of the Insight + Evidence shape *is* the back-pressure against hallucinated numerical claims (the agent has to find and quote the actual text).

## Workflow

### Step 1 — Survey

Always start with `ls work/reference/` and read `index.json` if present. Skip the work that's already done:

| File present | Step to skip |
|---|---|
| `source/` (Path A) or `document.md` (Path B) + `paper.pdf` | Substrate acquired (Step 2) |
| `index.json` with non-empty figures/tables/outline | Structural extraction done (Step 3) |
| `astra.yaml` exists | Stub written; never overwritten on re-run (preserves agent edits) |
| `astra.yaml` has non-empty `findings:` and `narrative.findings:` populated | Findings step done (Step 5, optional) |

If nothing is present, run the full workflow.

### Step 2 — Acquire substrate

Pick the path on entry from the input form:

- **arXiv ID** (e.g. `2503.19441`) → **Path A** (LaTeX source primary)
- **DOI** for an arXiv paper (e.g. `10.48550/arXiv.2503.19441`) → Path A (resolve to arXiv ID first)
- **Journal DOI** without arXiv preprint → **Path B** (PDF + Docling fallback)

Read [`references/arxiv-source.md`](references/arxiv-source.md) for Path A; [`references/pdf-fallback.md`](references/pdf-fallback.md) for Path B. Both end with `work/reference/paper.pdf` and a structured-text representation under `work/reference/`.

### Step 3 — Run the extraction script

`scripts/extract-paper-substrate.py` does the deterministic structural pass and writes the `astra.yaml` stub:

```bash
python3 .claude/skills/paper-extraction/scripts/extract-paper-substrate.py \
  --arxiv-id <arxiv-id>   # or --doi <doi>
```

The script detects the path automatically and produces:

- `figures/` populated with copied figure files (Path A) or untouched (Path B — Docling already populated it)
- `tables/<label-slug>.tex` — one file per `\begin{table}` block (Path A only)
- `bibliography-source.{bib,bbl}` if present in the source tarball (Path A only)
- `index.json` — the unified structural index
- `astra.yaml` — stub ASTRA representation: id, version, name (from `\title{}`), narrative.summary (from abstract), empty `findings: {}` for Step 5

The `--arxiv-id` / `--doi` argument populates the `id` and the evidence `doi:` field in `astra.yaml`. If neither is provided, the script writes placeholder text the agent can fix.

### Step 4 — Review the script's output and fix structural gaps

The script is purely deterministic. It walks the structural surface but does not understand the paper. Read `index.json`'s `extraction_warnings` and address each:

- **`figure figN: \includegraphics{X} could not resolve`** — the LaTeX referenced a file the script couldn't find. Search the source tree manually (sometimes figures live in non-standard subdirectories with non-standard extensions); copy the file into `figures/` and update the corresponding `index.json` entry's `file` so it's no longer null.
- **`figure figN: no \caption found`** — composite figures (subfloats) sometimes lack a top-level caption; verify the figure block in source and either record the per-subfigure captions in `caption` or note that the figure is composite.
- **`table tabN: no \label`** — verify the table is intentional (some `\begin{table}` blocks are non-tabular layout); rename or annotate as needed.
- **Path B caveat** — outline + citation extraction are not yet implemented for the Docling fallback; the warnings list flags this. For now, on Path B, those fields are empty.

Also eyeball `astra.yaml`'s `name:` and `narrative.summary:`. The title or abstract may contain unresolved custom `\newcommand` macros (defined elsewhere in the source); the script doesn't expand macros, so they pass through verbatim. Clean them up if you need pretty rendering downstream — none of this blocks validation.

### Step 5 — *(Optional)* Walk the paper for findings, append to `astra.yaml`

**Skip this step unless a downstream consumer needs it.** Steps 1–4 produce a complete `work/reference/` plus a valid (empty-findings) `astra.yaml` on their own. Step 5 fills in the paper's claimed numerical findings — useful when the next thing you'll do is reproduce the paper (the findings become reproduction targets) or compare against it (the findings become diff anchors). Skip when you just want to read the paper or have the structural index for browsing.

When you do run Step 5: this is the agent's central interpretive step and the one piece the script can't do.

For each **central numerical claim the paper makes about its results**, append a finding to `astra.yaml`'s `findings:` map. The shape (per ASTRA's [Insight + Evidence](https://w3id.org/ASTRA/insight) classes):

```yaml
findings:
  s8_constraint:
    id: s8_constraint
    claim: "S_8 = sigma_8 (Omega_m / 0.3)^0.5 = 0.795 ± 0.014 from the fiducial pure E/B analysis"
    created_at: "2026-04-04T00:00:00Z"
    evidence:
      - id: abstract_quote
        doi: "10.48550/arXiv.2604.03227"
        version: 1
        quote:
          exact: "we find $S_8 = 0.795 \\pm 0.014$"
  bmode_pte_fiducial:
    id: bmode_pte_fiducial
    claim: "Minimum B-mode PTE = 0.18 across configuration-space, COSEBI, and harmonic-space statistics at fiducial scale cuts"
    created_at: "2026-04-04T00:00:00Z"
    evidence:
      - id: abstract_pte
        doi: "10.48550/arXiv.2604.03227"
        version: 1
        quote:
          exact: "all three statistics pass the null test (minimum PTE $= \\configPteSixThreeCombined$)"
```

**What counts as a finding:** a numerical or specific qualitative result the paper claims, of the kind a reproduction would have to match (or document divergence from). Headline results (S_8, PTEs, χ²), structural conclusions ("we detect X at Y σ"), validated null-test outcomes. *Not* methodology choices, *not* dataset descriptions — those live elsewhere.

**Discipline:**

1. **Read the abstract and conclusions first.** The paper's own framing of its results lives there. Most central findings can be quoted from one of those two surfaces.
2. **Use `quote.exact` literally.** Copy the LaTeX text as it appears in `paper.tex` — don't paraphrase, don't expand macros, don't normalize math. The `exact` is what `astra validate --verify-evidence` will look for in the source PDF; if you paraphrase, evidence verification fails. If the quote is hard to make unique, add `prefix:` and `suffix:` (~20–100 chars before/after) per the W3C TextQuoteSelector spec.
3. **Anchor to the source.** Every finding's evidence carries a `doi:` (the paper's own DOI, e.g. `10.48550/arXiv.2604.03227`) and `version:` (paper version — `1` for v1, `2` for v2 of an arXiv preprint).
4. **`created_at`** is the timestamp of the finding's creation in this file (i.e., when the agent wrote it). ISO 8601.
5. **Add the `narrative.findings:` cross-link.** ASTRA requires that when `findings:` is non-empty, `narrative.findings:` exists and references at least one finding. Shape: `narrative: { findings: "The fiducial analysis yields the [S_8 constraint](#findings.s8_constraint); B-mode null tests pass with [minimum PTE = 0.18](#findings.bmode_pte_fiducial)." }`
6. **Validate.** Run `astra validate work/reference/astra.yaml`. If it passes, the file is a valid ASTRA artifact. Add `--verify-evidence` to confirm each `quote.exact` is actually findable in the cached PDF.

**How many findings?** Aim for the central results, not exhaustive coverage. A paper with one headline measurement (e.g. an S_8 constraint) plus a few supporting null-test outcomes typically has 3–8 findings. A paper covering multiple separate analyses may have more.


## Inputs

The skill accepts:

1. An **arXiv ID** (`YYMM.NNNNN` or pre-2007 form like `astro-ph/0607021`)
2. A **DOI** — either an arXiv DOI (`10.48550/arXiv.<id>`) or a journal DOI

The slash-command form is `/paper-extraction <arxiv-id-or-doi>`.

## What the script does vs what the agent does

**Script (`extract-paper-substrate.py`):** walks LaTeX (Path A) or Docling output (Path B) and emits two things:

1. `index.json` — figures (with copied files + line numbers + multi-graphic panels), tables (one `.tex` per block, including AAS `deluxetable`), section outline (with line numbers, in paper-reading order), citation keys (with every file+line they appear on, including biblatex commands), abstract, title, paths.
2. `astra.yaml` — a stub ASTRA artifact: `id` (derived from arxiv-id/DOI), `version`, `name` (from `\title{}`), `narrative.summary` (from abstract), empty `inputs:`/`outputs:`/`findings:`. Validates as-is.

The script handles a few realities of LaTeX papers automatically:

- **Comments are stripped** before regex passes, so commented-out `\includegraphics` / `\cite` / `\section` don't leak into extraction. Newlines are preserved so line numbers stay accurate.
- **Multi-file source** (`\input{}` / `\include{}` chains) is read in **paper-reading order** by walking `main.tex`'s input tree, not alphabetical filename order.
- **Simple `\newcommand{\name}{body}` macros** are expanded in extracted titles, abstracts, captions, and section names. Macros with arguments (`\newcommand{\foo}[1]{...}`) pass through unexpanded — handling those would require evaluating arbitrary LaTeX.
- **Standard table envs** (`table`, `table*`, `deluxetable`, `deluxetable*`) and **standard citation commands** (natbib family + biblatex `\autocite` / `\textcite` / `\parencite` / `\footcite` / `\smartcite`) are all recognized.

What the script does *not* do: understand what figures show, identify findings, infer methodology, or handle substrate acquisition (Step 2). It also doesn't expand macros with arguments, resolve `\graphicspath{}` overrides, or parse non-LaTeX abstract metadata blocks.

**Agent (Steps 4 + 5):** reads `index.json`'s `extraction_warnings` and fixes structural gaps (Step 4), then walks the paper and writes `findings:` into `astra.yaml` with quote-anchored evidence (Step 5). The verbosity of the Insight + Evidence shape *is* the back-pressure: the agent has to find and quote actual paper text, not invent.

## Discipline

- **One entry-point.** `/paper-extraction <id>` is the whole surface. Don't have callers reach into `scripts/` or `references/` directly. The skill orchestrates; consumers trust `index.json`.
- **Self-contained.** This skill takes a DOI and produces a standardized directory. It doesn't know who calls it or what they do with the result. Don't add caller-specific logic.
- **Idempotent.** Survey-first, skip-if-done. Re-invoking on the same paper does no work and produces no errors.
- **arXiv-LaTeX is primary.** When an arXiv source tarball is acquirable, Path A wins. PDF + Docling is the fallback for non-arXiv only.
- **Reading materials only.** The skill produces what's structurally in the paper itself — substrate, figures, tables, outline, citations, embedded bibliography. Adjacent assets (code repos, supplementary datasets, related papers, project bibliography management) are explicitly out of scope.
- **Script is dumb on purpose.** The deterministic pieces (figure/table blocks, section headings, `\cite{}` keys) belong to the script. Anything that requires understanding what the paper is *about* lives outside this skill — paper-extraction sets the table; it doesn't read the meal.
- **`extraction_warnings` is the agent surface.** When the script can't resolve something, it doesn't fail or guess — it warns. The agent reads the warnings and decides whether to fix or surface.

## Anti-patterns

- **Re-fetching what's already there.** Always survey `work/reference/` and read `index.json` first.
- **Adding numerical-finding extraction to the script.** Macro-based extraction (`\newcommand{\Omegam}{0.315}`) catches almost no real papers; inline-value extraction needs semantic judgment about what's a *result* vs incidental. Findings live in `astra.yaml`, written by the agent in Step 5.
- **Paraphrasing the `quote.exact` text.** Copy the paper's LaTeX text verbatim. Paraphrasing breaks `astra validate --verify-evidence` and weakens the back-pressure that justified ASTRA shape in the first place.
- **Surfacing partial state silently.** If `paper.pdf` was fetched but the LaTeX-source download failed, write `work/reference/extraction-error.txt` with a clear cause and stop, rather than producing a half-populated `work/reference/` with no signal that more was intended.
- **Knowing about the caller.** The skill's contract is the directory + index. If you're tempted to write logic that depends on a particular invoker, push that logic into the invoker instead.
