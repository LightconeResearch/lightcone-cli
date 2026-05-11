# LITERATURE — resolve `prior_insights:` placeholders against the cited papers

After SPECIFY records each citation marker as a `prior_insights:` *placeholder* (`id`, `claim`, `doi`, `decision_links` — no `evidence:` selector), LITERATURE stands up each cited paper's reading materials, finds the verbatim quote in the cited paper that justifies the placeholder's claim, and authors the resolved `evidence:` selector back into `astra.yaml`. After LITERATURE, every `prior_insights:` entry is a verified citation; `astra validate astra.yaml --verify-evidence` returns clean.

The quote-finding direction is: **target paper's claim → quote inside the cited paper**. The target paper says "we follow Smith+20's magnitude cut of i<24"; LITERATURE goes to Smith+20 and finds the verbatim quote there that justifies that statement ("we adopt a magnitude cut of i<24 as our fiducial selection"). The point is to verify the target paper's claims about its predecessors are real, not paraphrased or misremembered.

LITERATURE runs **after SPECIFY**, not before — relevant `prior_insights:` are defined by the decisions and findings they justify. Fetching cited papers speculatively before SPECIFY would do work for citations that may never end up needed.

This phase runs as the orchestrator-spawned `literature` sub-agent. Its internal architecture is **two simple stages**: mechanical fetch (paper-extraction's deterministic script, batched-parallel via shell — no agent fan-out), then quote-finding (literature does it itself for small placeholder counts; spawns a small number of Haiku sub-agents for large counts). The agentic work is the quote-matching; the fetch is plumbing.

## Inputs

- `astra.yaml` — filled by SPECIFY's paper (and code) passes; each sub-analysis has `prior_insights:` entries with `claim:` + `doi:` + `decision_links:` but no `evidence:` selector. These are the placeholders LITERATURE resolves.
- `work/reference/index.json#citations` — paper-extraction's cite-key → `{locations, citation, doi}` mapping for every entry in the target paper's bibliography. Used as the canonical cite-key → DOI lookup when cross-checking placeholder DOIs and surfacing unresolved-DOI cases.
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — the target paper; useful for context on how the cited paper is invoked.
- **paper-expert** (agent ID passed in by the orchestrator) — reachable via `SendMessage`. Useful when a placeholder's claim is ambiguous and you need to know what the target paper actually says around the citation site.
- CLAUDE.md — **Rigor** for this spawn's chosen rigor level.

## Outputs

- `astra.yaml` — `prior_insights:` placeholders **resolved**: each placeholder now has at least one `evidence:` entry with `TextQuoteSelector` (`exact:`, `prefix:`, `suffix:`) plus `FragmentSelector` (`page:`) pointing at the cited paper. `astra validate astra.yaml --verify-evidence` returns clean.
- `work/cited/<doi-slug>/` — one directory per cited paper, holding that paper's substrate from paper-extraction (`paper.pdf`, `source/` or `document.md`, `index.json`, `astra.yaml` stub, figures, tables). Resume-by-existence: re-running LITERATURE skips fetching any DOI whose `work/cited/<doi-slug>/` is already populated.
- `work/notes/literature/resolutions.yaml` — consolidated per-placeholder evidence resolutions before merge (when Haiku fan-out is used, sub-Haiku outputs land in `work/notes/literature/haiku-<N>.yaml` and are merged into this single file). Intermediate; survives for audit.

## How it runs

### Stage 1 — Mechanical fetch (batched, no agent fan-out)

Collect every `prior_insights:` entry whose `evidence:` is missing or empty. Group by DOI. Each unique DOI becomes one fetch.

Run paper-extraction's substrate script for each unique DOI **in batches of 5** via shell parallelism. paper-extraction's `extract-paper-substrate.py` is deterministic — no agent involvement needed. Each invocation writes to `work/cited/<doi-slug>/work/reference/`:

```bash
# Pseudocode for the batched fetch loop the literature sub-agent runs.
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

- **≤10 placeholders:** the literature sub-agent does the quote-finding itself. It walks the placeholders one at a time, greps into the relevant cited paper's substrate for terms from the claim, identifies the verbatim quote, and writes `{exact, prefix, suffix, page}` to `work/notes/literature/resolutions.yaml`. Single agent, low context overhead per placeholder (grep + targeted read, not whole-paper-absorption).

- **>10 placeholders:** the literature sub-agent partitions placeholders across **a small number of Haiku sub-agents** (rough rule: aim for 5–8 placeholders per Haiku, so 11–15 placeholders → 2 Haikus, 30 placeholders → 4 Haikus). Each Haiku gets its subset of placeholders + the substrate paths for the cited papers those placeholders reference. Haikus are cheap and fast and the work is well-bounded (grep + format YAML), so this is the right model. Each Haiku writes to `work/notes/literature/haiku-<N>.yaml`; literature reads them all, merges into `resolutions.yaml`, then writes back to `astra.yaml`.

The exact Haiku threshold and partition size are heuristic — they trade off context-budget per Haiku vs. orchestration overhead. The literature sub-agent has discretion; the rule of thumb is "few enough to track easily, each one small enough to finish in a single fast turn."

### Stage 3 — Merge into astra.yaml

The literature sub-agent reads `work/notes/literature/resolutions.yaml` and writes the resolutions back into `astra.yaml`:

- For each resolved placeholder, locate `prior_insights[<id>]` in `astra.yaml` (the placeholder already lives in its sub-analysis; the merge just sets its `evidence:` field).
- For each unresolved placeholder, append a line to `open-questions.md` describing it — the user resolves at REVIEW close-out by either supplying a different citation, weakening the claim, or removing the placeholder entirely.
- Run `astra validate astra.yaml --verify-evidence` after the merge to catch structural breakage early.

Single writer (the literature sub-agent), no merge conflicts even when Haikus produced the inputs in parallel.

## Quote-finding contract (used by both the literature sub-agent and Haiku sub-agents)

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
      doi:            DOI of the cited paper
      decision_links: which decision option(s) this placeholder backs
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
       - Empirical comparisons between approaches the claim's
         decision_links reference.
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

When the literature sub-agent fans out to Haikus, each Haiku is spawned with `model="haiku"` and gets this contract plus its assigned subset of placeholders and substrate paths.

## Self-review (rigor chosen per spawn)

After the merge lands, a fresh-context Task-tool sub-agent cross-checks each resolved `prior_insights:` entry against its cited paper:

- Does the `evidence:` quote belong to the cited paper at the cited page? (`astra validate --verify-evidence` does the deterministic check; the sub-agent does the semantic check.)
- Does the quote actually justify the placeholder's `claim:`? Or is the quote technically present but tangential?
- Does the placeholder's `claim:` actually support the decision option it's linked to via `decision_links:`?

The depth of self-review follows the rigor level the orchestrator picked for this spawn (read CLAUDE.md's **Rigor** section):

- **Cheap:** skip review entirely, or run a single fresh-context reviewer pass and incorporate its fixes once.
- **Heavy:** N rounds — each round spawns a fresh reviewer; literature incorporates fixes between rounds; the next round spawns another fresh reviewer that does not see the prior round's fixes. Iterate until two consecutive rounds find no fixes, or a 5-round system cap.

Each round runs a brand-new sub-agent that does NOT see prior rounds' findings or fixes — pattern-matching on prior fixes defeats the cross-check. Reviewers output findings only; the literature sub-agent edits `astra.yaml` between rounds (or re-spawns Haiku quote-finding for entries that need a different quote).

### Per-round fresh reviewer — prompt shape

```
You are a LITERATURE reviewer. Read astra.yaml's prior_insights:
entries, the cited papers (substrate at work/cited/<doi-slug>/), and
the target paper. Report inconsistencies. You are one of several
independent reviewers; assume nothing has been fixed.

Check:
  1. Evidence integrity. (astra validate --verify-evidence handles the
     deterministic check; you do the semantic check.)
  2. Evidence justifies claim. Does the quote actually support the
     claim, or is it tangential?
  3. Claim supports the decision. Does the placeholder's claim justify
     the linked decision option?
  4. Cited paper is the right paper. Does the target paper actually
     invoke this DOI for this claim?
  5. Unresolved entries are honest. For entries in open-questions.md
     flagged unresolved, does a closer read of the cited paper find
     supporting evidence the resolver missed?

Output findings to work/notes/literature-review/round-<N>.md, one fix
per F-N entry. Verdict is `clean` or a count. Do NOT edit astra.yaml.
```

If N hits the 5-round system cap without two consecutive clean rounds, the literature sub-agent stops and reports back to the orchestrator. If the user is reachable, ask in prose: "LITERATURE review reached round cap with N fixes still landing; continue, accept the current resolutions, or revise scope?" If unreachable, accept current state, log the unfinished tail in `open-questions.md`, and let the orchestrator decide whether to proceed or re-spawn.

## Survey signals (entry into LITERATURE)

- `astra.yaml` has `prior_insights:` placeholders — entries with `claim:` + `doi:` but no `evidence:` ⇒ ready to resolve
- `work/cited/<doi-slug>/work/reference/index.json` exists for each unique cited DOI ⇒ fetches done
- `work/notes/literature/resolutions.yaml` exists with non-empty resolutions / unresolved sections ⇒ quote-finding done
- `astra.yaml`'s `prior_insights:` entries each have a resolved `evidence:` selector ⇒ merge done
- `astra validate astra.yaml --verify-evidence` returns clean ⇒ structural validation done
- For cheap: at least one `work/notes/literature-review/round-<N>.md` with verdict `clean` (or no fixes were incorporated) ⇒ LITERATURE review done
- For heavy: two consecutive `round-<N>.md` files with verdict `clean` ⇒ LITERATURE review done

When all of the above hold ⇒ LITERATURE complete; orchestrator proceeds to IMPLEMENT.

## Notes

- **Mechanical fetch is the substrate; quote-finding is the agentic work.** Don't conflate them. paper-extraction's deterministic script handles the fetch — batched-parallel via shell, no agent fan-out. Quote-finding is the semantic match between target-paper-claim and cited-paper-quote; that's the agent's job.
- **paper-extraction is the canonical fetch mechanism.** Using `astra paper add` would give only the cached PDF; paper-extraction gives substrate (LaTeX source where available, structural index, figures, citations) which is much better material for verbatim quote-finding. The cost is small and parallelizable.
- **Haiku is the right model for fan-out quote-finding.** Cheap, fast, well-suited to bounded grep-and-format work. Use Sonnet/Opus only when the placeholder count is small enough that the literature sub-agent does it itself anyway.
- **Resume is automatic.** If `work/cited/<doi-slug>/work/reference/index.json` exists, skip that DOI's fetch. If `work/notes/literature/resolutions.yaml` has an entry for a placeholder, skip that placeholder's quote-finding.
- **Unresolved is not failure.** A placeholder that no quote in the cited paper supports is a real signal — the target paper cited loosely or paraphrased beyond what the source actually says. Surface to `open-questions.md`; don't fabricate evidence.
- **`astra validate --verify-evidence` runs after the merge**, not after each Haiku's per-placeholder output. Haikus write to disjoint files; the deterministic check happens once `astra.yaml` is updated.
- **Commit per stage.** Fetches commit together once Stage 1 completes (one commit for all cited-paper substrates). Quote-finding commits together once Stage 2 completes (`resolutions.yaml` + Haiku files). The merge into `astra.yaml` is its own commit. Each review round file commits as it lands. The orchestrator reads `git log` to see progress.
