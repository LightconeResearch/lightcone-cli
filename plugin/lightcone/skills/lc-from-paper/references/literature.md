# LITERATURE — resolve `prior_insights:` placeholders against the cited papers

After SPECIFY records each citation marker as a `prior_insights:` *placeholder* — a syntactically-complete `Insight` (`id`, `claim`, `created_at`, `evidence: [{id, doi}]`) whose Evidence entry carries the cited paper's DOI but **no `quote:` selector yet** — LITERATURE stands up each cited paper's reading materials, finds the verbatim quote in the cited paper that justifies the placeholder's claim, and writes the resolved `quote: {exact, prefix, suffix}` (+ `location: {page: N}`) onto that Evidence entry. The decision↔insight linkage already lives on the option side (`Option.insights: [<insight_id>, ...]`); LITERATURE doesn't touch it — only the Evidence's `quote:` / `location:`. After LITERATURE, every `prior_insights:` Evidence entry has a verified quote; `astra validate astra.yaml --verify-evidence` returns clean.

The quote-finding direction is: **target paper's claim → quote inside the cited paper**. The target paper says "we follow Smith+20's magnitude cut of i<24"; LITERATURE goes to Smith+20 and finds the verbatim quote there that justifies that statement ("we adopt a magnitude cut of i<24 as our fiducial selection"). The point is to verify the target paper's claims about its predecessors are real, not paraphrased or misremembered.

LITERATURE runs **after SPECIFY**, not before — relevant `prior_insights:` are defined by the decisions and findings they justify. Fetching cited papers speculatively before SPECIFY would do work for citations that may never end up needed.

LITERATURE is what a ralph iteration does when the workdir signals "SPECIFY done + `prior_insights:` placeholders present whose Evidence entries carry `doi:` but no `quote:` selector yet." Its internal architecture is **two simple stages**: mechanical fetch (paper-extraction's deterministic script, batched-parallel via shell — no agent fan-out), then quote-finding (the iteration does it itself for small placeholder counts; spawns a small number of Haiku sub-agents inside its own main session for large counts). The agentic work is the quote-matching; the fetch is plumbing.

## Inputs

- `astra.yaml` — filled by SPECIFY's paper (and code) passes; each sub-analysis has `prior_insights:` entries shaped as syntactically-complete `Insight` blocks (`id`, `claim`, `created_at`, `evidence: [{id, doi}]`) where each Evidence carries a `doi:` but no `quote:` selector. These are the placeholders LITERATURE resolves by writing `quote: {exact, prefix, suffix}` and `location: {page}` onto each Evidence entry. The option↔insight linkage already lives on the option side (`Option.insights`); LITERATURE does not touch it.
- `work/reference/index.json#citations` — paper-extraction's cite-key → `{locations, citation, doi}` mapping for every entry in the target paper's bibliography. Used as the canonical cite-key → DOI lookup when cross-checking placeholder DOIs and surfacing unresolved-DOI cases.
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — target paper text. Grep into for context on how the cited paper is invoked, when a placeholder's claim is ambiguous.
- `constitution.md` — Fidelity intent.

## Outputs

- `astra.yaml` — `prior_insights:` placeholders **resolved**: each placeholder's Evidence entries now carry `quote: {exact, prefix, suffix}` (TextQuoteSelector) plus `location: {page: N}` (FragmentSelector, 1-indexed page) pointing at the cited paper. `astra validate astra.yaml --verify-evidence` returns clean.
- `work/cited/<doi-slug>/` — one directory per cited paper, holding that paper's substrate from paper-extraction (`paper.pdf`, `source/` or `document.md`, `index.json`, `astra.yaml` stub, figures, tables). Resume-by-existence: re-running LITERATURE skips fetching any DOI whose `work/cited/<doi-slug>/` is already populated.
- `work/notes/literature/resolutions.yaml` — consolidated per-placeholder evidence resolutions before merge (when Haiku fan-out is used, sub-Haiku outputs land in `work/notes/literature/haiku-<N>.yaml` and are merged into this single file). Intermediate; survives for audit.

## How it runs

### Stage 1 — Mechanical fetch (batched, no agent fan-out)

Collect every unresolved `prior_insights:` placeholder — its Evidence carries `doi:` but no `quote:` selector yet. Group those DOIs uniquely; each unique DOI becomes one fetch.

Run paper-extraction's substrate script for each unique DOI **in batches of 5** via shell parallelism. paper-extraction's `extract-paper-substrate.py` is deterministic — no agent involvement needed. Each invocation writes to `work/cited/<doi-slug>/work/reference/`:

```bash
# Pseudocode for the batched fetch loop an iteration runs.
# For each unique DOI in the placeholder set:
mkdir -p work/cited/<doi-slug>
cd work/cited/<doi-slug>
python3 /path/to/paper-extraction/scripts/extract-paper-substrate.py \
    --arxiv-id <id-or-doi>
# Run up to 5 in parallel with `&` and `wait`; throttle to bound disk + network.
```

Skip Step 5 (findings) — LITERATURE only needs substrate, not the cited paper's claimed findings. Skip the agent's Step 4 (fix structural gaps) too — cited papers don't need warning-resolution to be quote-grep-able. Cited-paper bibliographies don't need DOI resolution either (we don't care about their citations' DOIs); if paper-extraction supports suppressing that, use it; if not, the cache amortizes across cited papers and it's tolerable.

Wall time: tens of seconds for 20 cited papers; bottlenecked by the slowest single fetch in each batch.

After each fetch lands, **register the PDF with the validator's cache** so `astra validate --verify-evidence` can find it later:

```bash
astra paper add "<DOI>" --pdf work/cited/<doi-slug>/work/reference/paper.pdf
```

For arXiv DOIs (`10.48550/arXiv.<id>`) the `--pdf` argument is optional (astra paper add can fetch directly), but pointing at the already-fetched PDF avoids a redundant network hit. For journal DOIs that 403 on Unpaywall, `--pdf` is required.

Resume: if `work/cited/<doi-slug>/work/reference/index.json` already exists, skip that DOI's fetch. If `astra paper get <DOI>` returns a cached entry, skip the registration too.

### Stage 2 — Quote-finding (literature does it, or Haiku fan-out)

Once all substrate is in place, count placeholders:

- **≤10 placeholders:** the iteration does the quote-finding itself. It walks the placeholders one at a time, greps into the relevant cited paper's substrate for terms from the claim, identifies the verbatim quote, and writes `{exact, prefix, suffix, page}` to `work/notes/literature/resolutions.yaml`. Single agent, low context overhead per placeholder (grep + targeted read, not whole-paper-absorption).

- **>10 placeholders:** the iteration partitions placeholders across **a small number of Haiku sub-agents** (rough rule: aim for 5–8 placeholders per Haiku, so 11–15 placeholders → 2 Haikus, 30 placeholders → 4 Haikus). Each Haiku gets its subset of placeholders + the substrate paths for the cited papers those placeholders reference. Haikus are cheap and fast and the work is well-bounded (grep + format YAML), so this is the right model. Each Haiku writes to `work/notes/literature/haiku-<N>.yaml`; the iteration reads them all, merges into `resolutions.yaml`, then writes back to `astra.yaml`.

The exact Haiku threshold and partition size are heuristic — they trade off context-budget per Haiku vs. orchestration overhead. The iteration has discretion; the rule of thumb is "few enough to track easily, each one small enough to finish in a single fast turn."

### Stage 3 — Merge into astra.yaml

The iteration reads `work/notes/literature/resolutions.yaml` and writes the resolutions back into `astra.yaml`:

- For each resolved placeholder, locate `prior_insights[<id>]` in `astra.yaml` (the placeholder already lives in its sub-analysis with `evidence: [{id, doi}]`; the merge augments each Evidence entry with the newly-authored `quote:` + `location:` selectors — `id` and `doi` were already there).
- For each unresolved placeholder, append a line to `open-questions.md` describing it — the user resolves at REVIEW close-out by either supplying a different citation, weakening the claim, or removing the placeholder entirely.
- Run `astra validate astra.yaml --verify-evidence` after the merge to catch structural breakage early.

Single writer (the iteration), no merge conflicts even when Haikus produced the inputs in parallel.

## Quote-finding contract (used by both the iteration itself and any Haiku sub-agents the iteration spawns)

The agent doing the quote-finding (literature itself, or each Haiku) follows the same contract. The Haiku prompt is just this contract with concrete placeholders + paths spliced in.

```
You are an ASTRA evidence-resolution agent. Your task is to find the
verbatim quotes in cited papers that justify a set of prior_insights:
placeholders authored by SPECIFY.

Inputs:
  - A list of placeholders. Each carries:
      id:             the placeholder's unique id within astra.yaml
      claim:          what the cited paper supports about a decision
                      in the target paper (target paper's framing)
      doi:            DOI of the cited paper (lives on the placeholder's
                      Evidence entry; quote: needs to be filled in)
      backed_options: a derived list of "<decision_id>.<option_id>" pairs
                      that reference this placeholder via Option.insights
                      — surface from astra.yaml when assembling the
                      placeholder set so the resolver knows which
                      decision-options this evidence has to support
  - Substrate path per cited paper at work/cited/<doi-slug>/work/reference/:
      paper.pdf, source/*.tex (Path A) or document.md (Path B),
      index.json (structural index for that cited paper).
  - Target paper at work/reference/source/ or work/reference/document.md
    (for context on how the cited paper is invoked, if you need it).

For each placeholder:

  1. Grep into the cited paper's substrate for terms from the claim.
     Path A: grep across work/cited/<doi-slug>/work/reference/source/*.tex.
     Path B: grep work/cited/<doi-slug>/work/reference/document.md.

  2. Read targeted spans (offset/limit) around the matches. Find a
     verbatim passage that supports the claim. Focus on:
       - Empirical comparisons between the approaches the placeholder's
         backed_options reference.
       - Performance benchmarks or validation results relevant to the
         choices.
       - Recommendations or caveats about specific methods/parameters.

  3. Build a TextQuoteSelector (exact + prefix + suffix) and
     FragmentSelector (page).
       - exact: copied VERBATIM from the source. Don't paraphrase or
         normalize whitespace. Don't quote math-heavy passages (the PDF
         text extractor collapses them); quote the surrounding English
         narrative instead.
       - prefix / suffix: 20–100 chars of REAL surrounding text, NOT
         editorial parentheticals. The validator concatenates them with
         the quote and matches against the PDF page at score ≥ 80.
       - page: page number from the rendered PDF where the quote
         appears.

  4. If no quote in the cited paper supports the claim, record the
     placeholder under unresolved: with a brief reason. The citation
     was loose, or the paper was paraphrased beyond what the source
     says, or the wrong paper was cited. Don't fabricate evidence.

Output (YAML, written to the path you were assigned):

resolutions:
  <insight_id>:
    id: <insight_id>
    evidence:
      - id: ev1
        doi: "<DOI>"
        quote:
          type: TextQuoteSelector
          exact: "<verbatim quote>"
          prefix: "<~20-100 chars REAL surrounding text BEFORE>"
          suffix: "<~20-100 chars REAL surrounding text AFTER>"
        location:
          type: FragmentSelector
          page: <int>

unresolved:
  <insight_id>:
    reason: "<one-line>"

Rules:
  - Keys under resolutions: / unresolved: are placeholder ids from
    astra.yaml; preserve them exactly. Merge uses these as the join key.
  - One placeholder lands in either resolutions: or unresolved:, never both.
  - Quotes are EXACT — verbatim, no paraphrasing, no whitespace normalization.
  - prefix: and suffix: are REQUIRED.
  - Avoid YAML | block-literal style for these strings; single-line or > folded.
  - Do NOT edit astra.yaml. The merge step does that.
```

When the iteration fans out to Haikus, each Haiku is spawned with `model="haiku"` and gets this contract plus its assigned subset of placeholders and substrate paths.

## Reviewing prior LITERATURE work as part of survey

There is no separate review phase. Every iteration that enters and finds `prior_insights:` placeholders resolved on disk reads them critically — running `astra validate --verify-evidence` for the deterministic check, plus a semantic re-read of each insight. If you see real issues — tangential quote, wrong cited paper, broken `Option.insights` linkage — fix them inline, commit (`literature: fix <what>`), exit. When a fresh-context read finds nothing to fix, the iteration advances to IMPLEMENT.

The cross-check questions on entry:

1. **Evidence integrity.** `astra validate --verify-evidence` handles the deterministic check; do the semantic check yourself.
2. **Evidence justifies claim.** Does the quote actually support the claim, or is it tangential?
3. **Claim supports the decision.** Does the placeholder's claim justify the decision option that references it via `Option.insights`?
4. **Cited paper is the right paper.** Does the target paper actually invoke this DOI for this claim?
5. **Unresolved entries are honest.** For entries in `open-questions.md` flagged unresolved, does a closer read of the cited paper find supporting evidence the resolver missed?

Apply fixes inline as you find them — `astra.yaml`'s `prior_insights:` entries (including re-running Haiku quote-finding for entries that need a different quote, when the gap is mechanical rather than semantic). Commit the diff and exit.

If the entry genuinely has no supporting quote in the cited paper, log it to `open-questions.md` with a "no support found" note and leave the entry as-is for the user to resolve at REVIEW. Don't fabricate evidence.

## Survey signals (entry into LITERATURE)

- `astra.yaml` has `prior_insights:` placeholders — entries with `claim:` plus Evidence carrying `doi:` but no `quote:` selector ⇒ ready to resolve
- `work/cited/<doi-slug>/work/reference/index.json` exists for each unique cited DOI ⇒ fetches done
- `work/notes/literature/resolutions.yaml` exists with non-empty resolutions / unresolved sections ⇒ quote-finding done
- `astra.yaml`'s `prior_insights:` entries each have a resolved `quote:` (+ `location:`) selector on their Evidence ⇒ merge done
- `astra validate astra.yaml --verify-evidence` returns clean ⇒ structural validation done; read the resolutions critically. Fix anything wrong; otherwise the iteration advances to IMPLEMENT.

## Notes

- **Mechanical fetch is the substrate; quote-finding is the agentic work.** Don't conflate them. paper-extraction's deterministic script handles the fetch — batched-parallel via shell, no agent fan-out. Quote-finding is the semantic match between target-paper-claim and cited-paper-quote; that's the agent's job.
- **paper-extraction is the canonical fetch mechanism.** Using `astra paper add` would give only the cached PDF; paper-extraction gives substrate (LaTeX source where available, structural index, figures, citations) which is much better material for verbatim quote-finding. The cost is small and parallelizable.
- **Haiku is the right model for fan-out quote-finding.** Cheap, fast, well-suited to bounded grep-and-format work. Use Sonnet/Opus only when the placeholder count is small enough that the iteration does the quote-finding itself anyway.
- **Resume is automatic.** If `work/cited/<doi-slug>/work/reference/index.json` exists, skip that DOI's fetch. If `work/notes/literature/resolutions.yaml` has an entry for a placeholder, skip that placeholder's quote-finding.
- **Unresolved is not failure.** A placeholder that no quote in the cited paper supports is a real signal — the target paper cited loosely or paraphrased beyond what the source actually says. Surface to `open-questions.md`; don't fabricate evidence.
- **`astra validate --verify-evidence` runs after the merge**, not after each Haiku's per-placeholder output. Haikus write to disjoint files; the deterministic check happens once `astra.yaml` is updated.
- **Commit per stage.** Fetches commit together once Stage 1 completes (one commit for all cited-paper substrates). Quote-finding commits together once Stage 2 completes (`resolutions.yaml` + Haiku files). The merge into `astra.yaml` is its own commit. Subsequent fix passes commit separately. The next iteration reads `git log` to see progress.
