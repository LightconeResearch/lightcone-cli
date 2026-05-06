# LITERATURE — resolve `prior_insights:` placeholders against the cited papers

After SPECIFY's paper pass records each citation marker as a `prior_insights:` *placeholder* (id, claim, doi, decision_links — no `evidence:` selector), LITERATURE fetches each cited paper, finds the verbatim quote that justifies the placeholder's claim, and authors the resolved `evidence:` selector back into `astra.yaml`'s `prior_insights[<id>].evidence[]`. After LITERATURE, every `prior_insights:` entry is a verified citation; `astra validate astra.yaml --verify-evidence` should pass.

LITERATURE runs **after SPECIFY**, not before — relevant `prior_insights:` are defined by the decisions and findings they justify. Fetching cited papers speculatively before SPECIFY would do work for citations that may never end up needed.

The constitution's per-phase mode is **always sub-agent** for this phase. Spawn one Task-tool sub-agent per cited paper for parallel resolution — they edit disjoint subsets of `astra.yaml`'s `prior_insights:` entries (only the placeholders whose `doi:` matches the sub-agent's paper). A merge step (orchestrator-inline) writes the per-paper resolutions back into `astra.yaml` after all sub-agents complete; a final fresh-context sub-agent runs the rigor-dialed self-review.

## Inputs

- `astra.yaml` — filled by SPECIFY's paper (and code) passes; each sub-analysis has `prior_insights:` entries with `claim:` + `doi:` + `decision_links:` but no `evidence:` selector. These are the placeholders LITERATURE resolves.
- `work/notes/cited_papers.yaml` — citation marker → DOI mapping from ARCHITECT (used to discover which DOIs need fetching, complementing the per-placeholder `doi:` lookup).
- `work/notes/architect/paper-index.md` — has the decision clusters per sub-analysis; per-paper sub-agents get it as context.
- `work/reference/source/` (Path A — arXiv LaTeX) or `work/reference/document.md` (Path B — Docling) — the target paper (for context on how the cited paper is invoked).

## Outputs

- `astra.yaml` — `prior_insights:` placeholders **resolved**: each placeholder now has at least one `evidence:` entry with `TextQuoteSelector` (`exact:`, `prefix:`, `suffix:`) plus `FragmentSelector` (`page:`) pointing at the cited paper. `astra validate astra.yaml --verify-evidence` returns clean.
- `work/notes/literature/<doi-slug>.yaml` — one file per cited paper carrying that paper's per-placeholder evidence resolutions (intermediate artifact; resume-by-existence — re-running LITERATURE skips a paper whose YAML already exists).
- Cached PDFs registered with `astra paper add` so `astra validate --verify-evidence` and downstream auditors can find them.

## How it runs

1. **Discovery.** Read `astra.yaml` and collect every `prior_insights:` entry whose `evidence:` is missing or empty. Group by `doi:`. Each group becomes a per-paper sub-agent invocation.
2. **Per-paper resolution (parallel).** Spawn one Task-tool sub-agent per DOI group. Each sub-agent: caches the PDF via `astra paper add`, reads the cited paper, finds verbatim quote(s) supporting each placeholder claim in its group, and writes the per-placeholder `evidence:` resolutions to `work/notes/literature/<doi-slug>.yaml`. Sub-agents do not edit `astra.yaml` directly — they write their per-paper YAML and exit.
3. **Merge.** A short orchestrator pass (or a single merge sub-agent) reads each `work/notes/literature/<doi-slug>.yaml` and writes the resolved `evidence:` entries back into `astra.yaml`'s `prior_insights[<insight_id>].evidence[]`. Single writer, no merge conflicts.
4. **Rigor-dialed self-review.** A fresh-context sub-agent reads each `prior_insights:` entry against its cited paper and asks "does this evidence actually justify the claim it's attached to?" Iterate per the rigor dial — frugal: one pass; rigor: N rounds until two consecutive rounds find no fixes (or a 5-round system cap).

## Per-paper resolution sub-agent — system prompt

> You are an ASTRA evidence-resolution agent. Your task is to find the verbatim quotes in a single cited paper that justify a set of `prior_insights:` placeholders authored by SPECIFY.
>
> ### Inputs
>
> You are given:
>
> - The path to the cited paper's PDF (cached via `astra paper add`).
> - A list of placeholder claims to resolve, each carrying:
>   - `id:` — the placeholder's unique id within `astra.yaml`.
>   - `claim:` — what the cited paper supports about a decision in the target paper (the target paper's framing, written by SPECIFY).
>   - `decision_links:` — which decision option(s) in `astra.yaml` this placeholder backs (for context — helps you find the right passage).
> - The path to the target paper (`work/reference/source/` or `work/reference/document.md`) for context on how the cited paper is invoked.
> - `work/notes/architect/paper-index.md` — the decision clusters from ARCHITECT.
>
> ### Instructions
>
> 1. Read the cited PDF using the Read tool.
> 2. For each placeholder claim, locate verbatim passage(s) in the cited paper that support it. Focus on:
>    - Empirical comparisons between approaches the placeholder's `decision_links` reference.
>    - Performance benchmarks or validation results relevant to the choices.
>    - Recommendations or caveats about specific methods / parameters.
> 3. For each supporting passage, build a `TextQuoteSelector` (`exact:` + `prefix:` + `suffix:`) and `FragmentSelector` (`page:`).
> 4. If a placeholder's claim has no supporting evidence in the paper (the citation was loose or the claim was paraphrased beyond what the paper actually says), record it under `unresolved:` with a brief note rather than fabricating evidence. The self-review pass surfaces these to the user via `<paper-slug>/open-questions.md`.
> 5. Write the per-placeholder resolutions to the specified output file.
>
> ### Caching the source PDF
>
> Before resolution, register the paper with the validator's PDF cache:
>
> ```bash
> astra paper add "<DOI>"
> ```
>
> For arXiv DOIs (`10.48550/arXiv.<id>`) this fetches directly. Journal DOIs that 403 on Unpaywall can be aliased to a locally-downloaded arXiv preprint:
>
> ```bash
> astra paper add "<JOURNAL_DOI>" --pdf <path-to-arxiv-pdf>
> ```
>
> ### Quote fidelity rules
>
> Quotes are verified at the spec level (`astra validate astra.yaml --verify-evidence`). Your job here is to extract quotes that pass that verification cleanly. The checks are:
>
> - Each `exact` quote must be present on the cited page, fuzzy-matched at RapidFuzz `partial_ratio` ≥ 70. Copy verbatim from the PDF; do not paraphrase, normalize whitespace, or strip mathematical typesetting.
> - The validator concatenates `prefix + quote + suffix` and matches that against the page text at a context score ≥ 80. Choose `prefix` / `suffix` as REAL surrounding page text (W3C TextQuoteSelector convention), not editorial commentary. Wording like "(Section 3.1 of Foo+19)" or "(see Figure 4)" silently lowers the context score below threshold even when the quote itself is in the PDF.
> - Avoid YAML `|` block-literal style for `exact`, `prefix`, and `suffix` values: embedded newlines from block-literal folding can mishandle the context-score concatenation. Single-line strings or `>` folded-block style are safer.
> - Math-formula quotes (with superscripts, subscripts, inline footnote markers) are likely to fail because the PDF text extractor collapses these. Quote the surrounding English narrative instead, or skip that piece of evidence if a sibling quote already establishes the claim.
>
> The verification cache is keyed by `(doi, version, sha256(quote_text))` plus `pdf_sha256`, so any edit to a quote in the eventual YAML automatically invalidates that entry — there is no need to delete the cache between runs.
>
> ### Quote granularity rules
>
> - **Quotes carry the claim on their own.** A four-word fragment satisfies fuzzy-match but fails the reader: lift the quote out of context and the claim it supports must still stand. Default to full sentences with TeX-anchored prefix/suffix; split a long passage into two evidence rows rather than truncate a quote into a fragment that depends on context. Fragments creep in at exactly the spots where inline math forces shrinking, which is also where claims hide.
> - **Cross-section methodology gets separate evidence rows.** When a paper's relevant methodology is split across multiple sections — a methods chapter defining a tool, a results chapter setting a threshold, an application chapter running it — file one evidence row per piece, each citing the section where that piece is *defined*. Do not collapse all the borrowed pieces into the application section's number.
>
> ### Output format
>
> Write ONLY this YAML structure to the output file. No other text.
>
> ```yaml
> resolutions:
>   <insight_id>:
>     id: <insight_id>
>     evidence:
>       - id: ev1
>         doi: "<DOI>"
>         quote:
>           type: TextQuoteSelector
>           exact: "<exact quote from paper, verbatim>"
>           prefix: "<~20-100 chars of REAL surrounding text BEFORE the quote>"
>           suffix: "<~20-100 chars of REAL surrounding text AFTER the quote>"
>         location:
>           type: FragmentSelector
>           page: <page number>
>
> unresolved:
>   <insight_id>:
>     reason: "<one-line: why no supporting evidence was found>"
> ```
>
> ### Rules
>
> - The keys under `resolutions:` and `unresolved:` are the placeholder `id:` values from `astra.yaml`'s `prior_insights:` — preserve them exactly. The merge step uses these as the join key.
> - One placeholder lands in either `resolutions:` or `unresolved:`, never both. If two passages support the same claim, list both as siblings under one placeholder's `evidence:`.
> - Quotes must be EXACT — copy verbatim from the PDF, no paraphrasing or whitespace normalization.
> - Prefix and suffix must be real surrounding page text, not editorial parentheticals.
> - `prefix:` and `suffix:` are REQUIRED for every `TextQuoteSelector`.
> - Do NOT edit `astra.yaml`. The merge step does that.

## Merge step

After all per-paper sub-agents complete, the orchestrator (or a single merge sub-agent) reads each `work/notes/literature/<doi-slug>.yaml` and writes the resolutions back into `astra.yaml`:

- For each entry in `resolutions:`, locate `prior_insights[<insight_id>]` in `astra.yaml` (sub-analysis ownership is implicit in the id; the placeholder already lives there) and set its `evidence:` field to the resolved selectors.
- For each entry in `unresolved:`, append a line to `<paper-slug>/open-questions.md` describing the unresolved placeholder and the reason — the user resolves at REVIEW (close-out) by either supplying a different citation, weakening the placeholder's `claim:`, or removing the placeholder entirely.
- Re-run `astra validate astra.yaml` after each per-paper merge to catch any structural breakage early.

A single writer (the merge step) avoids YAML round-trip conflicts that parallel writes would produce.

## Rigor-dialed self-review

After the merge lands, a fresh-context sub-agent cross-checks each resolved `prior_insights:` entry against its cited paper:

- Does the `evidence:` quote belong to the cited paper at the cited page? (`astra validate --verify-evidence` does the deterministic check; the sub-agent does the semantic check.)
- Does the quote actually justify the placeholder's `claim:`? Or is the quote technically present but tangential?
- Does the placeholder's `claim:` actually support the decision option it's linked to via `decision_links:`?

The depth of self-review is set by the constitution's frugality / rigor dial:

- **Frugal:** skip review entirely, or run a single fresh-context sub-agent pass and incorporate its fixes once.
- **Rigor:** N rounds — each round runs a fresh reviewer against the resolved `prior_insights:` + the cited papers + the target paper; LITERATURE incorporates fixes (re-spawn the per-paper sub-agent for entries that need a different quote, or adjust unresolved entries); the next round runs another fresh reviewer that has not seen the fixes. Iterate until two consecutive rounds find no fixes (the strong-termination criterion the loop already uses), or a 5-round system cap.

The discipline matches ARCHITECT's and SPECIFY's self-review shape: each round runs a brand-new sub-agent that does NOT see prior rounds' findings or fixes — pattern-matching on prior fixes defeats the cross-check. Reviewers output findings only; a separate fix pass (the orchestrator inline for trivial fixes, or another LITERATURE iteration for substantive changes) edits `astra.yaml`.

### Per-round fresh sub-agent — system prompt

> You are a LITERATURE reviewer. Read `astra.yaml`'s `prior_insights:` entries, the cited papers (cached via `astra paper add`), and the target paper, and report any inconsistencies you find. You will be one of several independent reviewers; do not assume anything has already been fixed.
>
> ### Inputs
>
> - `astra.yaml` — focus on every `analyses.<sub-analysis-id>.prior_insights:` entry. Each should have a resolved `evidence:` block.
> - The cited papers (cached PDFs).
> - `work/notes/cited_papers.yaml` — DOI lookups.
> - `<paper-slug>/open-questions.md` — to see which placeholders the resolution sub-agents flagged unresolved.
> - `work/reference/source/` (or `document.md`) — the target paper, for context on how the cited paper is invoked.
>
> ### What to check
>
> 1. **Evidence integrity.** `astra validate astra.yaml --verify-evidence` returns clean. (Do not run it yourself — your job is the semantic check beyond what `--verify-evidence` does.)
> 2. **Evidence justifies claim.** For each `prior_insights:` entry, does the quote actually support the `claim:`? Or is it tangential / weaker than the claim asserts?
> 3. **Claim supports the decision.** For each placeholder's `decision_links:`, does the placeholder's claim actually justify the linked decision option(s)? Or is the link a leap?
> 4. **Cited paper is the right paper.** Does the target paper actually invoke this DOI for this claim? (Sometimes a citation marker is misread; the wrong paper gets cached.)
> 5. **Unresolved entries are honest.** For entries in `<paper-slug>/open-questions.md` flagged unresolved, does a closer read of the cited paper actually find supporting evidence? (If yes, the resolution sub-agent missed it; flag for re-resolution.)
>
> ### Output
>
> Write your findings to `work/notes/literature-review/round-<N>.md`:
>
> ```markdown
> # LITERATURE review — round <N>
>
> ## verdict: clean | <count> fixes
>
> ## findings (one per fix needed)
>
> ### F-1 — <one-line summary>
>
> - placeholder: `prior_insights.<id>` (sub-analysis: `<sub-analysis-id>`)
> - issue: <evidence integrity | evidence-claim mismatch | claim-decision mismatch | wrong paper | unresolved-but-resolvable>
> - paper: `<DOI>` (page <N>)
> - what's wrong: <2–3 sentences>
> - suggested fix: <re-resolve with a different quote | adjust the claim | re-link decision | flag for human review>
> ```
>
> ### Rules
>
> - **Output findings only — do not edit `astra.yaml`.** A separate fix pass responds to your findings. Editing here defeats the multi-round-fresh-context discipline.
> - **Verdict is `clean` or a count.** "clean" means no fixes; otherwise enumerate.
> - **One fix per `F-N`.** Do not bundle.
> - **Cite specifically.** Always reference the placeholder by id, the cited paper by DOI + page, and the target paper's invocation site by section / page.

### LITERATURE-fix pass between rounds

After each round's findings file lands, a LITERATURE-fix pass (or the orchestrator inline for trivial mechanical fixes) responds to the findings — re-resolving placeholders with different quotes, adjusting claims, re-linking decisions, or surfacing unresolvable entries to `<paper-slug>/open-questions.md`. After any change to `astra.yaml`, re-run `astra validate astra.yaml --verify-evidence` to confirm the structural and quote-fidelity checks still pass.

If N hits the system cap of 5 rounds without two consecutive clean rounds, surface to the user via `AskUserQuestion`: "LITERATURE review reached round cap with N fixes still landing; continue, accept the current resolutions, or revise the constitution?" Default on user silence: accept current state, log the unfinished tail in `<paper-slug>/open-questions.md`, and proceed to IMPLEMENT.

## Survey signals (entry into LITERATURE)

- `astra.yaml` has `prior_insights:` placeholders — entries with `claim:` + `doi:` but no `evidence:` ⇒ ready to resolve
- `work/notes/literature/<doi-slug>.yaml` files exist (one per cited DOI) ⇒ per-paper resolution done
- `astra.yaml`'s `prior_insights:` entries each have a resolved `evidence:` selector ⇒ merge done
- `astra validate astra.yaml --verify-evidence` returns clean ⇒ structural validation done
- For frugal: at least a `work/notes/literature-review/round-1.md` with verdict `clean` (or no fixes were incorporated) ⇒ LITERATURE review done
- For rigor: two consecutive `round-<N>.md` files with verdict `clean` ⇒ LITERATURE review done

When all of the above hold ⇒ LITERATURE complete; proceed to IMPLEMENT.

## Notes

- **Run per-paper resolutions in parallel.** One sub-agent per cited DOI; they edit disjoint subsets of `prior_insights:` so write conflicts don't arise — but the merge step still serializes the writes back to `astra.yaml` to keep YAML round-trip safe.
- **Resume is automatic.** If `work/notes/literature/<doi-slug>.yaml` already exists, skip the per-paper resolution for that DOI. The merge re-runs whenever new per-paper files appear.
- **Unresolved is not failure.** A placeholder that no quote in the cited paper supports is a real signal — the target paper cited loosely, or paraphrased beyond what the source actually says. Surface to `<paper-slug>/open-questions.md`; don't fabricate evidence to make it green.
- **`astra validate --verify-evidence` runs after the merge, not after each per-paper sub-agent.** Sub-agents write to per-paper YAMLs; the deterministic check happens once `astra.yaml` is updated.
