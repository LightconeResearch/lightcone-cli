# ACQUIRE — fetch the paper, structure it, clone the code

Acquire the paper's reading materials and (when available) clone the reference code repository. The substrate work — LaTeX-source download, Docling fallback, figures, tables, outline, citations, embedded bibliography, paper-as-ASTRA-artifact — is delegated to **`/paper-extraction`**, which paper2astra trusts blindly. ACQUIRE adds **Step 2: code-clone**, which is reproduction-specific and stays here.

The constitution's per-phase mode controls whether this runs interactively or as a sub-agent. Default is sub-agent — surfacing happens only on download failures.

## Inputs

- The paper's DOI or arXiv ID (from the constitution)
- An optional code repo URL (from the interview, if the user knew it)

## Outputs

After Step 1 (`/paper-extraction`):

- `work/reference/index.json` — structural index (figures, tables, outline, citations with line numbers, paths)
- `work/reference/astra.yaml` — ASTRA-shape representation of the paper, including the paper's claimed numerical findings as ASTRA `findings:` (when paper-extraction's optional Step 5 is run)
- `work/reference/paper.pdf` — always
- `work/reference/paper.tex` + `work/reference/source/` — Path A (arXiv LaTeX)
- `work/reference/document.md` — Path B (PDF + Docling)
- `work/reference/figures/` — figure files
- `work/reference/tables/` — one .tex file per `\begin{table}` block
- `work/reference/bibliography-source.{bib,bbl}` — Path A only, copied from source tarball when present

After Step 2 (this phase):

- `work/reference/code/` — cloned reference repo (or absent if not found)
- `work/reference/code-status.yaml` — record of where the code came from

## Step 1 — Stand up the paper's reading materials

Invoke `/paper-extraction <arxiv-id-or-doi>`. The skill is idempotent — it surveys `work/reference/` first and skips work that's already done.

```
/paper-extraction <arxiv-id-or-doi>
```

This produces everything under `work/reference/` *except* the code clone. paper2astra ACQUIRE does not re-implement the substrate logic; if something is wrong with the substrate, fix it in `/paper-extraction`, not here.

Two starting surfaces: `work/reference/index.json` (structural — figures, tables, outline, citations with line numbers) and `work/reference/astra.yaml` (semantic — the paper as an ASTRA artifact, with `findings:` carrying the paper's central numerical claims as quote-anchored evidence). ARCHITECT reads index.json when its Explore sub-agents fan out across the paper; SPECIFY reads astra.yaml when authoring `prior_insights:` against the paper's claims (the paper's `findings:` map directly to a reproduction's `prior_insights:`).

## Step 2 — Clone the reference code repository

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

- If `paper.pdf` is present, **and** the path indicator (`source/` for Path A or `document.md` for Path B) is present, **and** `index.json` is present → Step 1 is done.
- If `work/reference/code/` is present (or `code-status.yaml` records `found: false`) → Step 2 is done.
- When both are done, ACQUIRE is complete; proceed to ARCHITECT.
- Otherwise, run whichever step is missing. `/paper-extraction` handles its own idempotency for Step 1.

## Notes

- **paper-extraction is the substrate authority.** Don't re-fetch the LaTeX source, don't re-run Docling, don't re-parse the paper from inside ACQUIRE. If a substrate need surfaces that paper-extraction doesn't cover, file it as paper-extraction work — not as ACQUIRE work.
- **arXiv DOI form is `10.48550/arXiv.<id>`.** Useful when downstream tools want a DOI rather than an arXiv ID.
- **Equation numbers and section numbers must match the rendered paper.** When citing "eq. N" or "§N" in any downstream phase, find the equation or heading by content, not by a naïve count of TeX blocks or markdown headings. Path A: source preserves printed numbers in `\label{}`s. Path B: Docling preserves printed numbers in its markdown.
- **This phase is acquisition + code-clone, not understanding.** Do not start indexing or comparing the paper here — that's ARCHITECT.
- **Code-as-canonical** is loaded by every subsequent phase. The per-paper `CLAUDE.md` restates the rule; ACQUIRE just makes sure `work/reference/code/` exists when possible.
