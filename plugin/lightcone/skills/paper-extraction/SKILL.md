---
name: paper-extraction
description: >
  Turn an arXiv ID or DOI into a standardized `work/reference/` directory:
  paper substrate (arXiv LaTeX source primary, PDF + Docling fallback),
  copied figure files, per-table `.tex` files, section outline with line
  numbers, deduplicated citation keys with every location they appear plus
  each cited paper's full citation text and resolved DOI, abstract,
  embedded bibliography (when present in source), and a valid
  `astra.yaml` representing the paper as an ASTRA artifact (with the
  paper's claimed numerical findings as ASTRA `findings:`). Emits a
  top-level `index.json` for the structural surface plus the `astra.yaml`
  for the semantic surface. Triggers on: "read paper", "prep paper",
  "ingest paper", "extract paper", "set up paper", "fetch arxiv", "arxiv
  id", "DOI", "find paper", or `/paper-extraction <id>`.
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
├── index.json                # structural index — figures, tables, outline, citations (with DOIs), paths
├── astra.yaml                # ASTRA-shape representation: the paper as an ASTRA artifact, including findings
├── paper.pdf                 # always
├── paper.tex                 # Path A — symlink to the main .tex file
│   (or)
├── document.md               # Path B — Docling-extracted markdown
├── source/                   # Path A — extracted arXiv tarball (full source tree)
├── figures/                  # figure files (copied from LaTeX or rendered by Docling)
├── tables/                   # one .tex file per `\begin{table}` block (Path A)
├── bibliography-source.bib   # Path A only — copy of any .bib found in source/
├── bibliography-source.bbl   # Path A only — copy of any .bbl found in source/
└── .doi-cache.json           # Crossref/ADS lookup cache for re-run idempotency
```

The skill produces only the paper's own reading materials. Anything not contained in or derived from the paper itself — code repositories, supplementary datasets, related papers — is out of scope; the caller handles those.

### Two surfaces: `index.json` (structural) and `astra.yaml` (semantic)

**`index.json` is structural and machine-friendly.** Everything the script could mechanically extract: figures, tables, section outline with line numbers, citation keys with every location *plus the cited paper's full citation text and resolved DOI*, abstract, paths. Read this when you want to know "what's in this paper, where do I find it." Sample shape:

```json
{
  "schema_version": 1,
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
    "asgari17": {
      "locations": [{"file": "main.tex", "line": 178}, {"file": "main.tex", "line": 561}],
      "citation": "Asgari, M., et al. (2017) KiDS-450: Tomographic cross-correlation cosmic shear results. MNRAS 464, 1676-1692",
      "doi": "10.1093/mnras/stw2606"
    },
    "planck18_lensing": {
      "locations": [{"file": "main.tex", "line": 92}],
      "citation": "Planck Collaboration, et al. (2020) Planck 2018 results. VIII. Gravitational lensing. A&A 641, A8",
      "doi": "10.1051/0004-6361/201833886"
    }
  },
  "extraction_warnings": [
    "figure fig3: \\includegraphics{...} could not resolve to a file in source/",
    "citation kuijken:2011: could not resolve DOI; tried doi-field, eprint-field, Crossref, ADS"
  ]
}
```

The `citations:` block maps each cited paper's BibTeX key (Path A) or synthetic `<lastname>_<year>` key (Path B) to `{locations, citation, doi}`. Downstream consumers (e.g. lc-from-paper's SPECIFY when authoring `prior_insights:` placeholders, LITERATURE when discovering which DOIs to fetch) read the DOI directly from `citations[key].doi`. Unresolvable entries keep `citation: null` and/or `doi: null` and are flagged in `extraction_warnings`.

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
- `index.json` — the unified structural index, including the enriched `citations:` block (each cited key carries `{locations, citation, doi}`; DOI resolution covers ~96% of typical-paper bibliographies)
- `astra.yaml` — stub ASTRA representation: id, version, name (from `\title{}`), narrative.summary (from abstract), empty `findings: {}` for Step 5
- `.doi-cache.json` — Crossref/ADS lookup cache; re-runs skip the network for already-seen entries

The `--arxiv-id` / `--doi` argument populates the `id` and the evidence `doi:` field in `astra.yaml`. If neither is provided, the script writes placeholder text the agent can fix.

The DOI resolver tries, in order: the entry's `doi:` field → an `eprint:`-derived arXiv DOI → Crossref bibliographic query (free, no API key needed) → ADS title search (only if `ADS_API_TOKEN` env var or `~/.ads/dev_key` is present — graceful skip when absent). Title hits from Crossref are gated by a similarity check against the queried title to drop noisy false matches.

### Step 4 — Review the script's output and fix structural gaps

The script is purely deterministic. It walks the structural surface but does not understand the paper. Read `index.json`'s `extraction_warnings` and address each:

- **`figure figN: \includegraphics{X} could not resolve`** — the LaTeX referenced a file the script couldn't find. Search the source tree manually (sometimes figures live in non-standard subdirectories with non-standard extensions); copy the file into `figures/` and update the corresponding `index.json` entry's `file` so it's no longer null.
- **`figure figN: no \caption found`** — composite figures (subfloats) sometimes lack a top-level caption; verify the figure block in source and either record the per-subfigure captions in `caption` or note that the figure is composite.
- **`table tabN: no \label`** — verify the table is intentional (some `\begin{table}` blocks are non-tabular layout); rename or annotate as needed.
- **`citation <key>: could not resolve DOI`** — the entry has no `doi:` / `eprint:` field, and neither Crossref nor ADS (when available) returned a match. The entry stays in `citations:` with `doi: null`; a downstream consumer can flag it for human resolution or skip it. If many entries are unresolved, check that the title field is clean (sometimes `.bib` titles carry uncleaned LaTeX commands that drag down the Crossref similarity gate). Delete `.doi-cache.json` to force re-resolution.
- **`citation <key>: cited in source but no matching entry in bibliography-source.{bib,bbl}`** — a `\cite{<key>}` invocation has no corresponding bib record. Usually a typo in the LaTeX source; flag it and move on. The entry stays in `citations:` with `citation: null, doi: null`, locations preserved.
- **Path B caveat** — outline extraction is not yet implemented for the Docling fallback. Bibliography resolution works on Path B by parsing the references section at the tail of `document.md` and synthesizing keys (`<lastname>_<year>`), but citation *invocations* from rendered prose aren't yet extracted — Path B citations carry empty `locations: []`. The warnings list flags this.

Also eyeball `astra.yaml`'s `name:` and `narrative.summary:`. The title or abstract may contain unresolved custom `\newcommand` macros (defined elsewhere in the source); the script doesn't expand macros, so they pass through verbatim. Clean them up if you need pretty rendering downstream — none of this blocks validation.

### Step 5 — *(Optional)* Walk the paper for findings, append to `astra.yaml`

**Skip unless a downstream consumer needs `findings:` populated.** Steps 1–4 produce a complete `work/reference/` and a valid (empty-findings) `astra.yaml` on their own. Reproductions and diff workflows need findings; reading and browsing don't.

When you do run Step 5: for each **central numerical claim the paper makes about its results** — headline measurements, structural conclusions ("we detect X at Y σ"), validated null-test outcomes — append a finding to `astra.yaml`'s `findings:` map. *Not* methodology choices or dataset descriptions; those live elsewhere. Shape (per ASTRA's [Insight + Evidence](https://w3id.org/ASTRA/insight) classes):

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
```

When `findings:` is non-empty, `narrative.findings:` must reference at least one finding — e.g. `narrative: { findings: "The fiducial analysis yields the [S_8 constraint](#findings.s8_constraint)." }`.

See `examples/unions-bmodes-astra.yaml` for a fully populated `astra.yaml` (six findings, narrative, evidence anchored to the published version).

**Discipline:**

- **Read the abstract and conclusions first.** Most central findings can be quoted from one of those two surfaces.
- **`quote.exact` is verbatim.** Copy LaTeX as it appears in `paper.tex` — don't paraphrase, don't expand macros, don't normalize math. `astra validate --verify-evidence` searches for this string in the cached PDF; paraphrasing breaks the gate. If the quote isn't unique, add `prefix:` / `suffix:` (~20–100 chars) per W3C TextQuoteSelector.
- **Every evidence carries `doi:`** (the paper's own DOI, e.g. `10.48550/arXiv.2604.03227`) and `version:` (the arXiv version: `1` for v1, `2` for v2).
- **Validate.** `astra validate work/reference/astra.yaml` confirms shape; `--verify-evidence` confirms each `quote.exact` is actually findable in the cached PDF.


## Inputs

The skill accepts:

1. An **arXiv ID** (`YYMM.NNNNN` or pre-2007 form like `astro-ph/0607021`)
2. A **DOI** — either an arXiv DOI (`10.48550/arXiv.<id>`) or a journal DOI

The slash-command form is `/paper-extraction <arxiv-id-or-doi>`.

## What the script does vs what the agent does

**Script (`extract-paper-substrate.py`):** walks LaTeX (Path A) or Docling output (Path B) and emits two things:

1. `index.json` — figures (with copied files + line numbers + multi-graphic panels), tables (one `.tex` per block, including AAS `deluxetable`), section outline (with line numbers, in paper-reading order), citation keys (with every file+line they appear on, including biblatex commands, *plus the cited paper's full citation text and resolved DOI*), abstract, title, paths.
2. `astra.yaml` — a stub ASTRA artifact: `id` (derived from arxiv-id/DOI), `version`, `name` (from `\title{}`), `narrative.summary` (from abstract), empty `inputs:`/`outputs:`/`findings:`. Validates as-is.

The script handles a few realities of LaTeX papers automatically:

- **Comments are stripped** before regex passes, so commented-out `\includegraphics` / `\cite` / `\section` don't leak into extraction. Newlines are preserved so line numbers stay accurate.
- **Multi-file source** (`\input{}` / `\include{}` chains) is read in **paper-reading order** by walking `main.tex`'s input tree, not alphabetical filename order.
- **Simple `\newcommand{\name}{body}` macros** are expanded in extracted titles, abstracts, captions, and section names. Macros with arguments (`\newcommand{\foo}[1]{...}`) pass through unexpanded — handling those would require evaluating arbitrary LaTeX.
- **Standard table envs** (`table`, `table*`, `deluxetable`, `deluxetable*`) and **standard citation commands** (natbib family + biblatex `\autocite` / `\textcite` / `\parencite` / `\footcite` / `\smartcite`) are all recognized.
- **Bibliography parsed in-script.** `.bib` files (preferred — `@type{key, field = value}` entries with brace-protected lastnames recognized) and `.bbl` files (rendered `\bibitem{key}` blocks) are parsed for Path A; the references section at the tail of `document.md` is parsed for Path B (synthesizing `<lastname>_<year>` keys with letter-suffix disambiguation). DOIs are resolved against Crossref + (optionally) ADS, cached for idempotency, and joined back against `\cite{}`-extracted locations.

What the script does *not* do: understand what figures show, identify findings, infer methodology, or handle substrate acquisition (Step 2). It also doesn't expand macros with arguments, resolve `\graphicspath{}` overrides, parse non-LaTeX abstract metadata blocks, or extract citation invocations from rendered prose (Path B `locations:` arrays are empty as a result).

**Agent (Steps 4 + 5):** reads `index.json`'s `extraction_warnings` and fixes structural gaps (Step 4), then walks the paper and writes `findings:` into `astra.yaml` with quote-anchored evidence (Step 5). The verbosity of the Insight + Evidence shape *is* the back-pressure: the agent has to find and quote actual paper text, not invent.

## Discipline

- **One entry-point.** `/paper-extraction <id>` is the whole surface. Don't have callers reach into `scripts/` or `references/` directly. The skill orchestrates; consumers trust `index.json`.
- **Self-contained.** This skill takes a DOI and produces a standardized directory. It doesn't know who calls it or what they do with the result. Don't add caller-specific logic.
- **Idempotent.** Survey-first, skip-if-done. Re-invoking on the same paper does no work and produces no errors. DOI lookups cache to `.doi-cache.json`; re-runs don't re-hit the network for already-seen entries.
- **arXiv-LaTeX is primary.** When an arXiv source tarball is acquirable, Path A wins. PDF + Docling is the fallback for non-arXiv only.
- **Reading materials only.** The skill produces what's structurally in the paper itself — substrate, figures, tables, outline, citations (with resolved DOIs), embedded bibliography. Adjacent assets (code repos, supplementary datasets, related papers, project bibliography *management* — i.e. authoring new entries, curating across papers) are explicitly out of scope; *resolving* the bibliography that's already in the paper is in scope.
- **Script is dumb on purpose.** The deterministic pieces (figure/table blocks, section headings, `\cite{}` keys, bibliography entries, DOI lookups) belong to the script. Anything that requires understanding what the paper is *about* lives outside this skill — paper-extraction sets the table; it doesn't read the meal.
- **`extraction_warnings` is the agent surface.** When the script can't resolve something (unmatched citation key, unresolvable DOI, network failure), it doesn't fail or guess — it warns. The agent reads the warnings and decides whether to fix or surface.

## Anti-patterns

- **Re-fetching what's already there.** Always survey `work/reference/` and read `index.json` first.
- **Adding numerical-finding extraction to the script.** Macro-based extraction (`\newcommand{\Omegam}{0.315}`) catches almost no real papers; inline-value extraction needs semantic judgment about what's a *result* vs incidental. Findings live in `astra.yaml`, written by the agent in Step 5.
- **Paraphrasing the `quote.exact` text.** Copy the paper's LaTeX text verbatim. Paraphrasing breaks `astra validate --verify-evidence` and weakens the back-pressure that justified ASTRA shape in the first place.
- **Producing a parallel cited-papers artifact.** Bibliography resolution lives inside `index.json`'s `citations:` block, not in a side file. Anyone who needs the citation→DOI mapping reads `index.json#citations[key].doi` directly.
- **Surfacing partial state silently.** If `paper.pdf` was fetched but the LaTeX-source download failed, write `work/reference/extraction-error.txt` with a clear cause and stop, rather than producing a half-populated `work/reference/` with no signal that more was intended.
- **Knowing about the caller.** The skill's contract is the directory + index. If you're tempted to write logic that depends on a particular invoker, push that logic into the invoker instead.
