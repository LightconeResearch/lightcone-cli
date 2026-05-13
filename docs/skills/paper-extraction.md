# /paper-extraction

Turn an arXiv ID or DOI into a standardized, indexed `work/reference/`
directory: substrate (arXiv LaTeX source preferred, PDF + Docling
fallback), copied figures, per-table `.tex` files, a section outline
with line numbers, deduplicated citation keys with resolved DOIs, the
abstract, and a stub `astra.yaml` that treats the paper as an ASTRA
artifact.

Source: [`claude/lightcone/skills/paper-extraction/SKILL.md`](https://github.com/LightconeResearch/lightcone-cli/blob/main/claude/lightcone/skills/paper-extraction/SKILL.md).

Argument hint: `<arxiv-id-or-doi>` — invoked as `/paper-extraction
2503.19441` or `/paper-extraction 10.48550/arXiv.2503.19441`.

## Allowed tools

```
Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch
```

The agent runs `scripts/extract-paper-substrate.py` for the
deterministic structural pass, then walks any warnings and (optionally)
fills `findings:`.

## Outputs

Under `work/reference/` (idempotent — re-runs skip what's already done):

```
work/reference/
├── index.json                # structural index — figures, tables, outline, citations (with DOIs), paths
├── astra.yaml                # semantic — the paper as an ASTRA artifact (findings populated in Step 5)
├── paper.pdf                 # always
├── paper.tex                 # Path A — symlink to the main .tex file
│   (or)
├── document.md               # Path B — Docling-extracted markdown
├── source/                   # Path A — extracted arXiv tarball
├── figures/                  # copied figure files
├── tables/                   # one .tex file per `\begin{table}` block (Path A)
├── bibliography-source.bib   # Path A — copied from source
├── bibliography-source.bbl   # Path A — copied from source
└── .doi-cache.json           # Crossref/ADS lookup cache for idempotency
```

The skill produces only the paper's own reading materials. Code
repositories and supplementary datasets are out of scope; the caller
handles those.

## Two surfaces

**`index.json` is structural and machine-friendly.** Everything the
script mechanically extracts: figures, tables, section outline with
line numbers, citation keys (with every location *plus* the cited
paper's full citation text and resolved DOI), abstract, paths. Read
this when you want "what's in this paper, where do I find it." DOI
resolution covers ~96% of typical-paper bibliographies.

**`astra.yaml` is semantic and ASTRA-validating.** Treats the paper as
an ASTRA artifact: `id`, `name`, `narrative.summary`, and `findings:`
carrying the paper's claimed numerical results in the Insight +
Evidence shape. The verbosity of the shape *is* the back-pressure
against hallucinated claims — the agent has to find and quote actual
text.

## Workflow

1. **Survey.** `ls work/reference/`; read `index.json` if present. Skip
   any work already done.
2. **Acquire substrate.** Path A (arXiv → LaTeX source) or Path B
   (journal-only DOI → PDF + Docling).
3. **Run the extraction script.** `extract-paper-substrate.py` does
   the deterministic structural pass: figure copying, per-table `.tex`
   extraction, outline, citation resolution, `astra.yaml` stub.
4. **Review warnings and fix structural gaps.** Unresolved figures,
   missing captions, unresolved citation DOIs, Path B caveats.
5. **(Optional) Walk the paper for findings.** Append the paper's
   central numerical claims to `astra.yaml`'s `findings:` map with
   verbatim `quote.exact` evidence. Skip unless a downstream consumer
   needs it.

Path A is preferred whenever the paper is on arXiv — equations,
ligatures, captions, and tables come through clean. Path B is for
non-arXiv only.

## Citation DOI resolution

The resolver tries, in order: the entry's `doi:` field → an
`eprint:`-derived arXiv DOI → Crossref bibliographic query (free, no
API key) → ADS title search (only if `ADS_API_TOKEN` env var or
`~/.ads/dev_key` is present — graceful skip otherwise). Title hits
from Crossref are gated by a similarity check against the queried
title.

## Findings as Insight + Evidence

When Step 5 runs, each finding carries `claim:` plus verbatim `quote.
exact` anchored to the paper's DOI:

```yaml
findings:
  s8_constraint:
    claim: "S_8 = sigma_8 (Omega_m / 0.3)^0.5 = 0.795 ± 0.014 ..."
    created_at: "2026-04-04T00:00:00Z"
    evidence:
      - doi: "10.48550/arXiv.2604.03227"
        version: 1
        quote:
          exact: "we find $S_8 = 0.795 \\pm 0.014$"
```

`astra validate --verify-evidence` searches for `quote.exact` in the
cached PDF — paraphrasing breaks the gate.

## Discipline

- **Quote verbatim.** Copy LaTeX as it appears in `paper.tex`. Don't
  paraphrase, expand macros, or normalize math.
- **Every evidence carries `doi:` and `version:`** (the arXiv version,
  e.g. `1`, `2`).
- **Read abstract and conclusions first.** Most central findings sit
  in one of those two surfaces.
- **Re-runs are safe.** The script preserves agent edits to
  `astra.yaml` once the stub exists.

## Related

- [`/lc-from-paper`](lc-from-paper.md) — invokes `/paper-extraction`
  during ORIENT Stage 2 for the target paper, and again from inside a
  ralph iteration for each cited paper during LITERATURE; each iteration
  reads `index.json` and the substrate directly.
- [`/astra`](index.md#reference-skills-auto-primed-via-session-start) — Insight + Evidence shape, `quote.exact` rules.
