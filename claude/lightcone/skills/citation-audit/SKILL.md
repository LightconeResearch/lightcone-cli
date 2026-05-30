---
name: citation-audit
description: >
  Audit every citation in a manuscript by verifying that the cited paper
  actually supports the manuscript statement that cites it. Consumes
  paper-extraction's `work/reference/index.json` for the citation
  surface, fetches each cited paper's **arXiv LaTeX source** (not PDF),
  then partitions the cited papers into 5–8-per-worker batches and fans
  out parallel verifier workers (claude-sonnet) that read the source and
  return verbatim-quote-anchored verdicts per claim (supported / weak /
  unsupported / wrong-paper / unverifiable_pre_arxiv / unverifiable).
  Every quote is checked against the source (`source_match.py`) — the
  gate that makes the skill trustworthy. Verdicts merge into the ledger,
  materialize as `prior_insights:` on `astra.yaml`, and produce a
  self-contained HTML report. Mirrors `lc-from-paper`'s LITERATURE-phase
  fan-out shape. Triggers on: "audit citations", "check citations",
  "verify references", "due diligence", "arxiv compliance", or
  `/citation-audit`.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task, AskUserQuestion
---

# citation-audit

For every citation in a manuscript, find the verbatim quote in the cited
paper that supports the manuscript statement — or report that none
exists. The verdict per citation use-site is the unit; the aggregate is
a report saying which citations are clean, which need rewording, and
which need to be replaced.

**Verification runs against arXiv LaTeX source, not PDF.** This is the
central design choice (the "arXiv-source pivot"). PDF text extraction is
lossy — collapsed math, ligatures, captcha pages saved as `.pdf`,
ISO-8859 encodings — and that lossiness pushes verifiers to quote
topical title fragments because the real evidence (a measured value with
its uncertainty) is unreadable in extracted text. The arXiv e-print
source is the author's actual words: `$S_8 = 0.776\pm0.017$` is right
there and quotable. So the skill fetches each cited paper's source
tarball and the verifiers read `.tex`; PDF is a last-resort fallback only
when no arXiv source exists.

## Why this exists

[arXiv's May 2026 clarification](https://x.com/tdietterich/status/...)
of its code-of-conduct penalty made citation hygiene a first-class
publication risk: a hallucinated reference or LLM-residue meta-comment
can trigger a one-year ban. The cheap part of LLM-assisted writing
(prose fluency) outruns the expensive part (verifying every cite
against the actual cited paper). This skill closes that gap with the
same evidence-and-verification discipline that ASTRA applies to
analyses.

It is also a positive instrument, not only a defensive one: the same
process surfaces *non-hallucinated* mis-citations — wrong-paper-cited,
over-claimed support, key→entry mismatches in `.bib` files — that hand
audits miss. The April 2 hand audit of the UNIONS B-modes manuscript
caught a wrong-paper TreeCorr cite (`jarvis15` resolved to the 2004
skewness paper, not the algorithm paper), an over-strong "direct
predecessor" claim, and several too-categorical foundation citations.
This skill formalizes that workflow.

## Contract

**Input:** an ASTRA project where `paper-extraction` has populated
`work/reference/` for the manuscript under audit (one subject paper
per project, by paper-extraction's contract). The skill reads
`index.json`'s `citations:` block (citation key → `{locations,
citation, doi}`) plus the `.tex` source under `source/` (Path A) to
extract local context.

**Output:**

1. `prior_insights:` populated on `work/reference/astra.yaml`, one
   entry per `supported`/`weak` use-site with verdict tags and a
   source-quote anchor. The evidence gate is the skill's strict source
   check (`verify_and_downgrade.py`); `astra validate astra.yaml`
   confirms the entries are structurally valid.
2. `work/citation-audit/ledger.json` — the structural ledger: every
   `(citation_key, file, line, sentence, doi)` tuple plus the verifier
   verdict. Machine-friendly.
3. `work/citation-audit/report.html` — self-contained HTML report:
   per-citation status, drill-down on each verdict, an "action list"
   surfacing wrong-paper and unsupported items first. Phone-renderable.

Side effects: every cited paper with a resolvable arXiv eprint is
fetched as a **source tarball** into
`work/citation-audit/papers/<doi-slug>/source/` (UTF-8-normalized
`.tex`) so verifiers read the author's LaTeX. Genuinely pre-arXiv cites
are confirmed via ADS metadata only. The fetch outcome per DOI is
recorded in `work/citation-audit/fetch_state.json`.

## When to use

- "Audit citations in [manuscript]" before submitting to arXiv or a
  journal
- "Verify the references" on a paper you're reviewing
- After substantial AI-assisted prose changes, to catch hallucinated
  or drifted citations
- As a routine check on any paper an LLM helped draft

**Not for:** general literature review, finding new citations to add,
or judging the *quality* of the citations chosen. Scope is fidelity of
existing citations to their referents.

## Pipeline

### Step 0 — Survey

Run from a project that contains `work/reference/` with a
populated `index.json`. If the subject's substrate isn't extracted
yet, invoke `/paper-extraction <arxiv-id-or-doi>` first.

```bash
ls work/reference/                                   # confirm substrate
jq '.citations | length' work/reference/index.json   # how many cites
```

If `work/citation-audit/ledger.json` exists, skip Steps 1–3 for
entries with verdicts already present. Survey-first, idempotent.

### Step 1 — Build the citation ledger

`scripts/build_citation_ledger.py` reads `index.json` plus the
manuscript `.tex` and emits `work/citation-audit/ledger.json`:

```bash
python3 .claude/skills/citation-audit/scripts/build_citation_ledger.py \
  --reference-dir work/reference \
  --out work/citation-audit/ledger.json
```

For each `(citation_key, location)` pair from `index.json`'s
`citations:` block, the script reconstructs the **citation context** —
the sentence(s) containing the `\cite{}` call plus ~50 chars of
prefix/suffix — and emits a row:

```json
{
  "use_id": "schneider02b__line_205",
  "citation_key": "schneider02b",
  "doi": "10.1051/0004-6361:20020626",
  "file": "main.tex",
  "line": 205,
  "claim": "Source clustering... can generate B modes \\citep{schneider02b}.",
  "prefix": "B modes from cosmic shear at second order arise primarily from",
  "suffix": "as well as intrinsic-alignment correlations and lens-lens coupling.",
  "verdict": null
}
```

`use_id` is a stable, slug-cased identifier combining the bibkey and
line number — it becomes the `prior_insight` id. Multi-cite calls
(`\citep{a,b,c}`) produce one row per key, all pointing at the same
claim sentence (each key gets its own verdict).

The ledger also marks citations with no resolved DOI as
`verdict: "unverifiable_no_doi"`; the fan-out below skips them.

### Step 2 — Fetch arXiv source for every cited paper (mechanical, no agent fan-out)

`scripts/fetch_sources.py` walks the unique DOIs in the ledger and
fetches each cited paper's **arXiv e-print source tarball**:

```bash
python3 .claude/skills/citation-audit/scripts/fetch_sources.py \
  --ledger work/citation-audit/ledger.json \
  --state  work/citation-audit/fetch_state.json
```

Per DOI it resolves the eprint id (from the DOI itself when it's an
arXiv DOI, else via `resolve_arxiv.py` — ADS `identifier[]` then Crossref
`relation.has-preprint`, **verifiable metadata only, no title-guessing**),
downloads `https://arxiv.org/e-print/<id>`, extracts to
`papers/<doi-slug>/source/`, and normalizes every `.tex` to UTF-8 (the
Heymans 1210.0032 trap: ISO-8859-1 with very long lines). Each DOI lands
in `fetch_state.json` with one of:

| status | meaning | verifier reads |
|---|---|---|
| `source_fetched` | eprint source extracted | the `.tex` under `source_dir` |
| `pre_arxiv` | no eprint exists (genuinely pre-arXiv); ADS metadata recorded | ADS metadata only — confirm identity, never quote-fake |
| `pdf_fallback` | eprint exists but arXiv has only a PDF (rare) | the local `paper.pdf` (lossy; flagged) |
| `unresolvable` | no eprint and no ADS record | nothing — surfaced in the report |

Idempotent — re-runs skip DOIs already recorded unless `--refresh`. The
fetch is plumbing; no agent involvement, and no PDF cache / Unpaywall
dependency (source comes straight from arXiv, sidestepping the A&A 403s
that plagued the PDF path).

### Step 3 — Quote-finding (orchestrator partitions; verifier fan-out)

Mirrors `literature.md`'s Stage 2. The orchestrator (this skill, in
its main session) does the partition and merge; verifier workers do the
per-paper **source read** + per-claim verdict.

**Sizing:**

- **≤10 pending rows total:** the orchestrator does it inline — read
  each paper's source, write the verdict to the ledger. Single agent.
- **>10 pending rows:** **partition by cited paper**, ~5–8 papers
  per worker (≤500 KiB source per partition), ideally **clustered by
  topic** (all "foundations" papers in one batch, all "survey results"
  in another). Clustering by manuscript section (using `index.json`'s
  `outline` to map cite-line → section) is a sensible default.

Each spawn is a `Task` with `subagent_type="citation-verifier"` and
`model="sonnet"`. (Sonnet, not Haiku: finding the substantive
supporting quote rather than a topical scrap is judgment a weaker model
reward-hacks.) The agent definition ships at
`.claude/agents/citation-verifier.md` and carries the full verifier
contract — read-source procedure, verdict taxonomy, self-validation
loop, output schema. The orchestrator's `Task` prompt carries the
partition data joined from the ledger and `fetch_state.json`:

- `output_path`: where this worker writes —
  `work/citation-audit/verifier-<N>.yaml`
- `partition`: a YAML list of bundles. Each bundle is `(citation_key,
  doi, status, source_dir, main_tex, tex_files, ads_metadata, pdf_path,
  citation_text, rows[])` — the per-paper fields come straight from
  `fetch_state.json`; `rows[]` is every ledger row for that paper.

The worker returns **one verdict per row** per this taxonomy:

| Verdict | Meaning | Action |
|---|---|---|
| `supported` | Verbatim quote in the cited paper's source supports the claim. | Pass; quote populates the evidence. |
| `weak` | Source partially supports; the manuscript wording is stronger. | Suggested rewording in `notes:`. |
| `unsupported` | No relevant support in the cited paper. | Flag for human; cite likely wrong or speculative. |
| `wrong_paper` | The bibkey resolves to a paper whose topic doesn't match the claim. | Replace the cite. |
| `unverifiable_pre_arxiv` | Genuinely pre-arXiv (no eprint); identity confirmed via ADS metadata, full text not quotable. | No action — correct cite, just not quotable. |
| `unverifiable` | Could not anchor a verbatim quote despite apparent support. | Manual review. |

`supported` and `weak` verdicts carry a verbatim-source-quote
`TextQuoteSelector` (prefix + exact + suffix, copied contiguously from
the `.tex`) plus a `section` locator. **Each worker self-validates its
quotes in-loop with `source_match.py`** before writing the YAML — the
quote's `prefix+exact+suffix` must appear contiguously in the source.
Quotes that fail after 3 self-correction iterations get downgraded to
`unverifiable`. A 2-word title scrap cannot satisfy the contiguous
context check, which is exactly the reward-hack the source gate closes.

Fan out all workers in a **single message** (parallel `Task` calls).
Each writes to a disjoint `verifier-<N>.yaml`; merge happens in Step 4.

> Partition strategy is an orchestrator-discretion choice. Topic-
> clustering is heuristic; the right partition may be section-by-section
> or similarity-by-bib-entry. Iterate on what produces tight per-worker
> context.

### Step 4 — Merge verifier outputs into the ledger, then materialize as `prior_insights:` on `astra.yaml`

```bash
python3 .claude/skills/citation-audit/scripts/build_audit_yaml.py \
  --ledger work/citation-audit/ledger.json \
  --astra-yaml work/reference/astra.yaml
```

Reads every `verifier-<N>.yaml`, merges verdicts into `ledger.json`
keyed on `use_id`, then materializes `supported`/`weak` rows as
`prior_insights:` on `work/reference/astra.yaml`. Single writer (the
orchestrator), no merge conflicts even when many workers ran in
parallel. For each materialized row:

```yaml
prior_insights:
  schneider02b__line_205:
    id: schneider02b__line_205
    claim: "Source clustering... can generate B modes."
    created_at: "<iso8601>"
    tags: ["citation_audit", "verdict:supported"]
    evidence:
      - id: ev1
        doi: "10.1051/0004-6361:20020626"
        quote:
          exact: "B-modes in fact are produced by lensing itself, through the clustering of source galaxies"
          prefix: "<~30 chars of source before>"
          suffix: "<~30 chars of source after>"
        location:
          value: "Sect. 2 (B modes from source clustering)"   # source section, not a PDF page
    notes: |
      <verifier rationale, if weak/unsupported/wrong_paper>
```

The quote is copied verbatim from the cited paper's `.tex`; the
`location.value` records the source section instead of a PDF page.
`build_audit_yaml.py` is non-destructive (preserves any prior
`prior_insights:` authored by hand or by paper-extraction) and purges
its own previous audit-tagged entries on re-run.

### Step 5 — The gate: strict source verification (blocking)

```bash
python3 .claude/skills/citation-audit/scripts/verify_and_downgrade.py \
  --ledger work/citation-audit/ledger.json \
  --state  work/citation-audit/fetch_state.json
# then re-materialize so astra.yaml drops any downgraded rows.
# --materialize-only is REQUIRED here: a plain re-run would re-read the
# worker YAMLs and clobber the downgrades the gate just wrote.
python3 .claude/skills/citation-audit/scripts/build_audit_yaml.py \
  --ledger work/citation-audit/ledger.json \
  --astra-yaml work/reference/astra.yaml \
  --materialize-only
```

This is the trust anchor. For every `supported`/`weak` row,
`verify_and_downgrade.py` re-checks the quote against the cited paper's
arXiv source via `source_match.py` — `prefix+exact+suffix` must appear
contiguously (whitespace-normalized) in the `.tex`. Any quote that fails
is downgraded to `unverifiable` and its evidence dropped. Run it
**after** the Step-4 merge; then re-materialize with `--materialize-only`
so `astra.yaml` reflects the downgrades. (Without that flag,
`build_audit_yaml` re-reads the pre-downgrade worker YAMLs and overwrites
the gate's work — the ordering bug the flag exists to prevent.)

**Why the gate moved off `astra paper verify-quotes` / `astra validate
--verify-evidence`.** Those are PDF-based: they extract text from a
cached PDF and fuzzy-match. A quote copied from `.tex` carries the
author's markup (`$S_8=0.776\pm0.017$`, `\citep{}`) that the PDF text
layer mangles — so the PDF gate would *reject correct source quotes*.
Source and PDF are incompatible substrates; the pivot chose source, so
verification lives on source too. The skill owns its gate
(`source_match.py` + `verify_and_downgrade.py`); the PDF path is retired
from the pipeline. `astra validate astra.yaml` (without
`--verify-evidence`) still runs for **structural** schema validation of
the materialized insights — but it is not the evidence gate.

> **Upstream gap (filed, not blocking):** ASTRA's `--verify-evidence`
> is PDF-only. A source-aware verify mode (verify a quote against a
> cited paper's arXiv `.tex`, not just its PDF) would let `astra
> validate --verify-evidence` re-become the gate. Tracked alongside the
> [[gate-hardening]] work, which tightens the source matcher (degenerate-
> quote rejection, claim-bearing vs identity distinction) on top of the
> substrate this pivot establishes.

### Step 6 — Generate the report

```bash
python3 .claude/skills/citation-audit/scripts/render_report.py \
  --reference-dir work/reference \
  --ledger work/citation-audit/ledger.json \
  --out work/citation-audit/report.html
```

The report has three sections:

1. **Action list.** All `wrong_paper`, `unsupported`,
   `unverifiable_pre_arxiv`, and `unverifiable` verdicts at the top.
   This is what to act on before submission.
2. **Weak claims.** All `weak` verdicts with the cited paper's actual
   support and the verifier's suggested rewording.
3. **Clean ledger.** All `supported` verdicts in citation order, with
   the source quote and its section locator. The bulk of the report;
   collapsed by default via `<details>`.

The HTML is self-contained (inline styling) — phone-renderable via
`SendUserFile`.

## What the orchestrator does vs what the verifier workers do

**Orchestrator (this skill, main session):** runs the deterministic
ledger script (Step 1), runs the source fetch (Step 2), partitions the
ledger and fans out verifiers (Step 3), reads every `verifier-<N>.yaml`
and merges into `ledger.json` + `astra.yaml` (Step 4), runs the strict
source gate and re-merges (Step 5), renders the report (Step 6).
**Never reads cited papers in the main context** — that's a worker's
job, and reading them in the main session would defeat the
bounded-worker property.

**Verifier workers (`Task` with `model="sonnet"`, one per partition):**
each is given 5–8 cited papers and the manuscript claim rows that cite
them. Reads each paper's **arXiv source** (targeted: abstract → grep
keywords → read the matching section), classifies every row per the
verdict taxonomy, extracts verbatim source quotes for `supported`/`weak`,
self-validates with `source_match.py`, writes the YAML, and exits.
Bounded to its partition — never reads outside-partition papers, never
edits `astra.yaml`. The agent is
[`citation-verifier`](../../agents/citation-verifier.md); the
orchestrator invokes it via `Task` with
`subagent_type="citation-verifier"` and `model="sonnet"`.

This is the same separation paper-extraction makes between
`extract-paper-substrate.py` (deterministic, structural) and the
agent invoking the skill (semantic, judgment-bearing) — and the same
shape `lc-from-paper`'s LITERATURE phase uses to scale quote-finding
across many cited papers.

## Discipline

- **Verbatim quotes from source only.** Never paraphrase. Copy from the
  cited paper's `.tex` as-is, including LaTeX math and markup. The
  `source_match.py` gate is what makes this skill trustworthy;
  paraphrasing or macro-expanding breaks the contiguous-context check.
- **Quote the substance, not the topic.** For a quantitative claim, the
  supporting quote is the measured value with its uncertainty as written
  in the source — never a title fragment or survey middle-name. The
  source makes the value directly quotable; there is no excuse to grab a
  scrap.
- **One verdict per use-site, not per key.** The same paper cited three
  times for three different claims gets three verdicts. Some may pass
  while others don't.
- **`unsupported` is a verdict, not a failure.** It's a finding the
  human acts on. The skill flags it loudly; it does not silently drop
  the citation.
- **Pre-arXiv cites are confirmed, never faked.** Genuinely pre-arXiv
  papers (no eprint exists — Kaiser 1992, Blandford+91) get
  `unverifiable_pre_arxiv`: identity confirmed via ADS metadata, full
  text not quotable. Never manufacture a quote for them.
- **Companion / in-prep papers.** UNIONS Papers I/III/IV/V resolve to
  arXiv DOIs once posted; treat them like any cited paper. A companion
  with no DOI yet (in-prep) records `verdict: "unverifiable_no_doi"` and
  the report calls it out.
- **Method/software citations.** A bibkey pointing to a method's
  foundational paper (TreeCorr → Jarvis, Bernstein & Jain 2004) counts
  as `supported` when the statement is about the method — even if the
  title sounds unrelated, so long as the source describes the method.
  Flag `wrong_paper` only when the topic genuinely doesn't match.
- **Idempotent.** Re-running on the same subject only re-verifies rows
  missing a verdict. To force re-verification of a specific citation,
  delete its row from `ledger.json` and re-run.

## Anti-patterns

- **One worker per cited paper, or one per use-site.** Wrong unit of
  partition. Aim for 5–8 cited papers per worker so the per-worker
  context covers a useful slice and the orchestration overhead stays
  small. Mirror `lc-from-paper`'s LITERATURE sizing.
- **Reading cited papers in the orchestrator's context.** That defeats
  the bounded-worker safety property. If you find yourself opening
  cited `.tex` or PDFs in the main session, spawn a verifier.
- **Reading PDFs at all when source exists.** The whole pivot is *away*
  from PDF. PDF is the `pdf_fallback` last resort only; if a paper has
  arXiv source, the verifier reads source.
- **Reaching for `lc-extractor`.** That agent is for `/lc-new`'s
  decision-extraction framing — different problem space. The
  citation-audit verifier agent is `citation-verifier`.
- **Paraphrasing in `quote.exact`.** Breaks `source_match.py`. The
  worker's self-check should prevent this; if a quote arrives unverified,
  treat it as fabricated and drop the verdict.
- **Auto-rewriting the manuscript.** This skill produces *evidence* for
  the human author to act on. It does not edit the `.tex`.
- **Silently skipping unverifiable cites.** Surface `unverifiable_no_doi`
  and `unverifiable_pre_arxiv` prominently in the report — they are the
  cases the system literally cannot quote-check, which is exactly the
  arxiv-policy concern.

## See also

- [`paper-extraction`](../paper-extraction/SKILL.md) — the upstream
  skill producing the citation surface this skill consumes; its
  [`references/arxiv-source.md`](../paper-extraction/references/arxiv-source.md)
  is the source-fetch machinery `fetch_sources.py` extends from the
  subject paper to every cited paper.
- `scripts/source_match.py` — the quote-against-source matcher; the
  verifier's self-check CLI and the orchestrator gate's import.
- `astra validate astra.yaml` — structural schema validation of the
  materialized `prior_insights`. (The PDF-based `--verify-evidence` is
  *not* the gate here; see Step 5.)
- The hand-audit precedent: `ai-futures/felt/citation-audit` (April 2
  audit of the UNIONS B-modes manuscript) and
  `ai-futures/process/citation-provenance-workflow` (related: bib
  provenance).
