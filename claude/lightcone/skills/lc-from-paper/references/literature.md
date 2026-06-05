# LITERATURE — resolve one sub-analysis's `prior_insights:` placeholders against the cited papers

You are the **LITERATURE worker** for a single sub-analysis, spawned by [`reproduce_workflow.js`](../reproduce_workflow.js) immediately after that sub-analysis's SPECIFY (the two run as a `pipeline`, so sub A's literature overlaps sub B's specify). SPECIFY handed you a list of `prior_insights:` **placeholders** — each a syntactically-complete `Insight` (`id`, `claim`, `created_at`, `evidence: [{id, doi}]`) whose Evidence carries the cited paper's DOI but **no `quote:` selector yet**. Your job: for each placeholder, find the verbatim quote *in the cited paper* that justifies the placeholder's claim, and return it as a `quote: {exact, prefix, suffix}` + `location: {page}` selector on the Evidence entry.

You are **bounded and stateless**: read your inputs, do the quote-finding, return structured output (`LIT_SCHEMA`), exit. **You do NOT edit `astra.yaml`** — a single merge step (one writer, no concurrent-edit conflict) folds your `resolutions[]` onto the matching Evidence entries, appends your `unresolved[]` to `open-questions.md`, and runs `astra validate astra.yaml --verify-evidence`. Return the shape; the merge writes it.

**The quote-finding direction is: target paper's claim → quote inside the cited paper.** The target paper says "we follow Smith+20's magnitude cut of i<24"; you go to Smith+20 and find the verbatim quote there that justifies it ("we adopt a magnitude cut of i<24 as our fiducial selection"). The point is to verify the target paper's claims about its predecessors are real, not paraphrased or misremembered. This is the [`citation-audit`](../../citation-audit/SKILL.md) anchor → gate spine, applied to one sub-analysis's citations: the worker anchors, the merge's `--verify-evidence` gate re-checks every quote against the cited source.

**Fetch is plumbing; quote-finding is the work.** Standing up a cited paper's substrate is a deterministic script call — batched shell, no agents. The agentic value is the semantic match between target-paper-claim and cited-paper-quote. Don't conflate them.

## Inputs (the workflow hands you)

- **The placeholder list** — passed inline in your prompt as `spec.insight_placeholders` (the SPECIFY worker's structured output for this sub-analysis). Each entry carries `id`, `claim`, and `evidence: [{id, doi}]`. These are exactly the placeholders you resolve.
- `astra.yaml` — for cross-reference only. The `Option.insights: [<insight_id>, ...]` back-references tell you which decision-options each placeholder has to support (its `backed_options`), which sharpens what "justifies the claim" means. Read; never write.
- `work/reference/index.json#citations` — paper-extraction's cite-key → `{locations, citation, doi}` map for the target paper's bibliography. The canonical cite-key → DOI lookup when a placeholder's DOI needs cross-checking.
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — target paper text. Grep into it for context on *how* the cited paper is invoked when a placeholder's claim is ambiguous.
- **Fidelity intent** — passed in your prompt (`args.intent`). It sizes how hard you hunt for a borderline quote before declaring a placeholder unresolved; it does not license fabrication.

## Output — `LIT_SCHEMA` (structured return, not a file write)

```
{
  sub_id: "<this sub-analysis's id>",
  resolutions: [
    { id: "<placeholder_id>",
      evidence: [
        { id: "ev1", doi: "<DOI>",
          quote:    { type: "TextQuoteSelector", exact, prefix, suffix },
          location: { type: "FragmentSelector", page: <int> } } ] },
    ...
  ],
  unresolved: [ { id: "<placeholder_id>", reason: "<one line>" }, ... ]
}
```

One placeholder lands in **exactly one** of `resolutions[]` / `unresolved[]`, never both. Preserve placeholder `id`s verbatim — the merge joins on them. `id` and `doi` on each Evidence entry were already set by SPECIFY; you are *adding* `quote:` and `location:`.

## How it runs — two stages

### Stage 1 — Mechanical fetch (batched shell, no nested agents)

Collect the unique DOIs across your placeholders. Each unique DOI becomes one fetch via paper-extraction's deterministic substrate script — run them in batches (e.g. 5 at a time with `&` / `wait`) to bound disk + network:

```bash
mkdir -p work/cited/<doi-slug> && cd work/cited/<doi-slug>
python3 .claude/skills/paper-extraction/scripts/extract-paper-substrate.py --arxiv-id <id-or-doi>
```

Each invocation writes substrate (`paper.pdf`, `source/*.tex` Path A or `document.md` Path B, `index.json`) under `work/cited/<doi-slug>/work/reference/`. The script is deterministic — **no agent involvement**. You only need substrate, so skip the substrate skill's findings step and its structural-warning-resolution step (cited papers don't need to be warning-clean to be grep-able). You don't care about a cited paper's *own* citations' DOIs, so suppress that resolution if the script supports it; otherwise tolerate it — the cache amortizes.

After each fetch lands, register the PDF with the validator's cache so the merge's `--verify-evidence` gate can find the cited source later:

```bash
astra paper add "<DOI>" --pdf work/cited/<doi-slug>/work/reference/paper.pdf
```

For arXiv DOIs (`10.48550/arXiv.<id>`) `--pdf` is optional but avoids a redundant fetch; for journal DOIs that 403 on Unpaywall it's required.

Wall time: tens of seconds for ~20 cited papers, bottlenecked by the slowest fetch per batch.

### Stage 2 — Quote-finding (the agentic work — you do it)

Walk your placeholders one at a time. For each:

1. **Grep** the cited paper's substrate for terms from the claim. Path A: `grep` across `work/cited/<doi-slug>/work/reference/source/*.tex`. Path B: `grep` `work/cited/<doi-slug>/work/reference/document.md`.
2. **Read targeted spans** (offset/limit) around the matches — not the whole paper. Find a verbatim passage that supports the claim. Favor: empirical comparisons between the approaches the placeholder's `backed_options` reference; performance/validation results relevant to those choices; explicit recommendations or caveats about the method/parameter being cited.
3. **Build the selector** — a `TextQuoteSelector` (`exact` + `prefix` + `suffix`) and a `FragmentSelector` (`page`). The quote-finding contract below is binding.
4. **No supporting quote → `unresolved[]`** with a one-line reason. The citation was loose, the paper was paraphrased beyond what the source says, or the wrong paper was cited. **Never fabricate evidence.**

## Quote-finding contract

- **`exact` is verbatim.** Copied character-for-character from the cited source. No paraphrase, no whitespace normalization. **Don't quote math-heavy passages** — the PDF text extractor collapses LaTeX math and ligatures; quote the surrounding English narrative instead, where the real evidence (a measured value with its stated uncertainty, a method recommendation) reads cleanly.
- **`prefix` / `suffix` are 20–100 chars of REAL surrounding text** — not editorial parentheticals. The merge's `--verify-evidence` gate concatenates prefix + exact + suffix and matches against the cited PDF page; it needs genuine adjacent text to clear the contiguous-context check (score ≥ 80).
- **`page` is the 1-indexed page** of the rendered PDF where the quote appears.
- **Quote the substance, not the topic.** For a quantitative claim, anchor the measured value with its uncertainty as written — never a title fragment or survey middle-name. A naming cite (software / method / survey) still anchors a real sentence: the cited paper's own self-introducing line ("X is a code for computing …"), not a skip.

## Resume by existence

You are restart-safe without any state file:

- If `work/cited/<doi-slug>/work/reference/index.json` already exists, **skip that DOI's fetch**.
- If `astra paper show <DOI>` returns a cached entry, **skip the registration**.
- If a placeholder's matching Evidence entry in `astra.yaml` already carries a `quote:` selector, a prior LITERATURE run already resolved it — skip it.

A re-spawned worker re-does only the unfinished quote-finding, then returns the full `LIT_SCHEMA` for its sub-analysis (resolved + unresolved together) so the merge has a complete picture.

## Notes

- **paper-extraction is the canonical fetch mechanism, not `astra paper add` alone.** `astra paper add` gives only the cached PDF; the substrate script gives LaTeX source where available, a structural index, figures, and citations — far better material for verbatim quote-finding. The cost is small and parallelizable.
- **Unresolved is a signal, not a failure.** A placeholder no quote supports means the target paper cited loosely or paraphrased past what the source says. It goes to `open-questions.md` (via the merge) for the user to resolve at close-out — a different citation, a softened claim, or a removed placeholder. Surfacing it honestly is the job; fabricating a quote to hide it is the one unforgivable move.
- **You don't run `--verify-evidence` yourself.** You write quotes; the merge step runs `astra validate astra.yaml --verify-evidence` once, after folding every sub-analysis's resolutions in. Your contract is the anchor; the gate is downstream and deterministic.
- **The fan-out is the workflow's, not yours.** One worker, one sub-analysis. Don't spawn sub-agents to parallelize quote-finding across your placeholders — for the placeholder counts a single sub-analysis produces, walking them sequentially with grep + targeted read is cheap and keeps your context tight. The workflow already parallelizes across sub-analyses.
