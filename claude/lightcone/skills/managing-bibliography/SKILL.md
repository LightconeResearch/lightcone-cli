---
name: managing-bibliography
description: >
  Read arXiv paper source and add BibTeX entries via ADS API. Use for
  research that requires reading full paper text and managing citations.
  Also the canonical paper-acquisition path inside the lightcone-cli
  paper-reproduction bundle: `/paper2astra` calls this during the ACQUIRE
  phase to fetch arXiv LaTeX source, with PDF + Docling as the non-arXiv
  fallback. Triggers on: "read paper", "cite", "add to bibliography",
  "bibtex", "ADS", "arXiv", "find paper", "add citation", or any request
  to read scientific papers or manage references.
---

Read scientific papers and manage citations. Two capabilities:

1. **Read papers** — Download arXiv LaTeX source to read full text, verify claims, understand methodology
2. **Cite papers** — Fetch BibTeX from NASA ADS and add to bibliography

**Activation**: Use this skill when you need to:
- Read a paper's full text (not just abstract)
- Verify a claim before citing it
- Add citations to your bibliography
- Research how other papers phrase similar findings
- Acquire a paper for the `/paper2astra` reproduction pipeline (ACQUIRE phase)

**Usage pattern**:
- "Read the KiDS-Legacy paper to see how they report B-mode PTEs"
- "Add [paper description] to the bibliography"
- "Find and cite [author name] [year] [topic]"

---

## Reading Papers

Download arXiv LaTeX source to read full paper text:

```bash
# Download source (replace ID as needed)
curl -L -o /tmp/2503.19441.tar.gz "https://arxiv.org/src/2503.19441"

# Extract
mkdir -p /tmp/2503.19441 && cd /tmp/2503.19441 && tar -xzf /tmp/2503.19441.tar.gz

# Find the main tex file
ls *.tex
```

This gives you:
- Full paper text (not just abstract)
- Equations and methodology details
- How authors phrased specific claims
- Their bibliography (.bib or .bbl files)

Use when you need to:
- Verify a claim before citing
- See exact phrasing in another paper
- Understand methodology not in abstract
- Cross-reference their citations

---

## ADS API Setup

The ADS API requires an API token. Before using citation features:

1. **Check for token**: The skill reads `$ADS_API_TOKEN` from the environment
2. **If missing**: Tell the user to create one at https://ui.adsabs.harvard.edu/user/settings/token and set it:
   ```bash
   # Add to ~/.zshrc or ~/.bashrc
   export ADS_API_TOKEN="your-token-here"
   ```
3. **Do not proceed** with ADS API calls until the token is available — check with `echo $ADS_API_TOKEN`

---

## Citing Papers

When adding a paper to the bibliography:

1. **Web search** for the paper using description + "arxiv"
   - Look for arXiv ID in format `YYMM.NNNNN`
   - If multiple results, show options and ask user to select

2. **Query ADS API** to get bibcode using arXiv ID
   ```bash
   curl -H "Authorization: Bearer $ADS_API_TOKEN" \
     'https://api.adsabs.harvard.edu/v1/search/query?q=arXiv:YYMM.NNNNN&fl=bibcode'
   ```

3. **Fetch BibTeX entry** with abstract from ADS
   ```bash
   curl -H "Authorization: Bearer $ADS_API_TOKEN" \
     'https://api.adsabs.harvard.edu/v1/export/bibtexabs/{bibcode}'
   ```

4. **Parse BibTeX** to extract author names and year:
   - Parse `author = {...}` field for last names
   - Parse `year = YYYY` field for publication year
   - Generate citation key based on author count:
     - 1 author: `firstauthor{YY}` (e.g., `asgari17`)
     - 2 authors: `firstauthor.secondauthor{YY}` (e.g., `schneider.kilbinger12`)
     - 3+ authors: `firstauthor.etal{YY}` (e.g., `wright.etal25`)
   - Use only last names, lowercase, final 2 digits of year

5. **Replace citation key** in BibTeX entry
   - Update the entry key on the first line (before the opening brace)
   - Keep all other fields unchanged

6. **Append to bibliography** file
   - Add the modified entry to the project's `.bib` file
   - Check for duplicate keys first and warn if found

7. **Report success**
   - Show the user the complete entry that was added
   - Confirm file location

## Citation Key Generation

**Examples from BibTeX parsing**:
- `author = {{Wright}, Angus H. and {Stölzner}, Benjamin and ...}` + `year = 2025` → `wright.etal25`
- `author = {{Schneider}, Peter and {Kilbinger}, Martin}` + `year = 2012` → `schneider.kilbinger12`
- `author = {{Asgari}, Marika}` + `year = 2017` → `asgari17`

## Error Handling

- **No arXiv ID found**: Ask user to provide it manually or search for the paper directly
- **Multiple search results**: Show options and ask user to select the correct paper
- **ADS API fails**: Show error and suggest manual bibcode lookup or entry
- **Duplicate citation key**: Warn user, show existing entry, offer to replace or rename
- **Missing bibliography file**: Report error and ask for correct file path

## Key Configuration Points

- **ADS API Token**: Read from `$ADS_API_TOKEN` environment variable
- **ADS Search endpoint**: `https://api.adsabs.harvard.edu/v1/search/query`
- **ADS Export endpoint**: `https://api.adsabs.harvard.edu/v1/export/bibtexabs/{bibcode}`
- **Export format**: Use `bibtexabs` endpoint to include abstracts

## Bibliography File Paths

Adapt to your project structure:
- `docs/unions_bmodes/unions_bmodes.bib` (example UNIONS project)
- `references/bibliography.bib` (common alternative)
- User should specify their bibliography file path

## Notes

- Always use the `bibtexabs` endpoint to include abstract in the entry
- Parse author list carefully: format is `author = {{LastName}, FirstName and {LastName}, FirstName ...}`
- Year is straightforward: `year = YYYY`
- Before appending, verify file exists and has proper BibTeX format
- Preserve existing entries when appending new ones

---

## Provenance

Originally maintained at `~/.claude/skills/managing-bibliography/SKILL.md`
(Cail's personal version). Copied here so the lightcone-cli
paper-reproduction bundle has the full toolkit available via `lc init` —
without depending on a separate plugin install. The personal copy may be
ahead; re-sync as needed.
