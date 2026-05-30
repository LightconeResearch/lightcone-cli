---
name: citation-audit
description: >
  Audit every citation in a manuscript by verifying that the cited paper
  actually supports the manuscript statement that cites it. Consumes
  paper-extraction's `work/reference/index.json` for the
  citation surface, then partitions the cited papers into 5–8-per-Haiku
  batches and fans out parallel Haiku workers that return verbatim-quote-
  anchored verdicts per claim (supported / weak / unsupported /
  wrong-paper / unverifiable). Verdicts merge back into the ledger,
  materialize as `prior_insights:` on `astra.yaml`, and validate
  end-to-end with `astra validate --verify-evidence`. Produces a
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
   entry per citation use-site with verdict tags and quote-anchored
   evidence. Validates with `astra validate --verify-evidence`.
2. `work/citation-audit/ledger.json` — the structural ledger: every
   `(citation_key, file, line, sentence, doi)` tuple plus the verifier
   verdict. Machine-friendly.
3. `work/citation-audit/report.html` — self-contained HTML report:
   per-citation status, drill-down on each verdict, an "action list"
   surfacing wrong-paper and unsupported items first. Phone-renderable.

Side effects: every cited paper with a resolved DOI is fetched into
ASTRA's paper cache via `astra paper add <doi>` so verifiers can read
the PDF and `astra validate --verify-evidence` can confirm quotes.

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

### Step 2 — Mechanical fetch (batched-parallel, no agent fan-out)

For each unique DOI in the ledger, register the cited paper with
ASTRA's evidence cache. Batched-parallel via shell, exactly like
[`lc-from-paper`'s LITERATURE phase](../lc-from-paper/references/literature.md):

```bash
astra paper add "<DOI>"          # caches PDF; run up to 5 in parallel
```

Resume-by-existence: `astra paper get <DOI>` returning a cached entry
means skip that fetch. The fetch is plumbing — no agent involvement.

### Step 3 — Quote-finding (orchestrator partitions; Haiku fan-out)

Mirrors `literature.md`'s Stage 2. The orchestrator (this skill, in
its main session) does the partition and merge; Haiku workers do the
per-paper read + per-claim verdict.

**Sizing:**

- **≤10 pending rows total:** the orchestrator does it inline. Walk
  each row, read the cached PDF, write the verdict directly to the
  ledger. Single agent, low overhead.
- **>10 pending rows:** **partition by cited paper**, ~5–8 papers
  per Haiku, ideally **clustered by topic** (e.g. all "foundations"
  papers in one batch, all "survey results" in another). Clustering
  by section of the manuscript (using `index.json`'s `outline` to
  map cite-line → section) is a sensible default; an orchestrator
  with more context may cluster smarter.

Each Haiku spawn is a `Task` with `subagent_type="citation-verifier"`
and `model="haiku"`. The agent definition ships at
`.claude/agents/citation-verifier.md` and carries the full verifier
contract — verdict taxonomy, self-validation loop, output schema. The
orchestrator's `Task` prompt just carries the partition data:

- `output_path`: where this Haiku writes —
  `work/citation-audit/haiku-<N>.yaml`
- `partition`: a YAML list of bundles. Each bundle is `(citation_key,
  doi, pdf_path, citation_text, rows[])` where `rows[]` is every
  ledger row for that paper (a paper cited 5 times gets 5 rows in its
  bundle)

The Haiku returns **one verdict per row** per this taxonomy:

| Verdict | Meaning | Action |
|---|---|---|
| `supported` | Verbatim quote in the cited paper supports the claim. | Pass; quote populates the evidence. |
| `weak` | Cited paper partially supports; the manuscript wording is stronger than the source. | Suggested rewording in `notes:`. |
| `unsupported` | No relevant support in the cited paper. | Flag for human; cite likely wrong or speculative. |
| `wrong_paper` | The bibkey resolves to a paper whose topic doesn't match the claim. | Replace the cite. |
| `unverifiable` | Quote-extraction or PDF access failed after retries; not necessarily wrong. | Manual review. |

`supported` and `weak` verdicts carry an exact-quote
`TextQuoteSelector` (prefix + exact + suffix per W3C convention).
Each Haiku self-validates its quotes in-loop with `astra paper
verify-quotes <doi>` before writing the YAML; quotes that fail after
3 self-correction iterations get downgraded to `unverifiable` with a
note.

Fan out all Haikus in a **single message** (parallel `Task` calls).
Each writes to a disjoint `haiku-<N>.yaml`; merge happens in Step 4.

> Partition strategy is an orchestrator-discretion choice and an
> active design surface. Topic-clustering is heuristic; the right
> partition may end up being section-by-section, similarity-by-bib-
> entry-text, or something else. Iterate on what produces tight
> Haiku context per worker.

### Step 4 — Merge Haiku outputs into the ledger, then materialize as `prior_insights:` on `astra.yaml`

Read every `haiku-<N>.yaml`, merge verdicts into `ledger.json` keyed
on `use_id`, then materialize as `prior_insights:` on
`work/reference/astra.yaml`. Per `lc-from-paper`'s LITERATURE: single
writer (the orchestrator), no merge conflicts even when many Haikus
ran in parallel.

For each ledger row with a verdict:

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
          type: TextQuoteSelector
          exact: "B-modes in fact are produced by lensing itself. The effect comes about through the clustering of source galaxies."
          prefix: "<~30 chars before>"
          suffix: "<~30 chars after>"
        location:
          type: FragmentSelector
          page: 3
    notes: |
      <verifier rationale, if weak/unsupported/wrong_paper>
```

`scripts/build_audit_yaml.py` merges new entries into the subject's
`astra.yaml` non-destructively (preserves any prior `prior_insights:`
authored by hand or by paper-extraction).

### Step 5 — Validate

```bash
cd work/reference && astra validate astra.yaml --verify-evidence
```

Every `supported` and `weak` quote must verify against the cached PDF
of the cited paper. A failure here means either a Haiku fabricated
the quote (severe — should never happen if the Haiku ran its
self-check) or the cited paper's cached version differs (e.g.
v1 vs v2). Investigate before accepting.

### Step 6 — Generate the report

```bash
python3 .claude/skills/citation-audit/scripts/render_report.py \
  --reference-dir work/reference \
  --ledger work/citation-audit/ledger.json \
  --out work/citation-audit/report.html
```

The report has three sections:

1. **Action list.** All `wrong_paper`, `unsupported`, and
   `unverifiable_no_doi` verdicts at the top. This is what to act on
   before submission.
2. **Weak claims.** All `weak` verdicts with the cited paper's actual
   support and the verifier's suggested rewording.
3. **Clean ledger.** All `supported` verdicts in citation order, with
   the quote and link to the cited paper's PDF page. The bulk of the
   report; collapsed by default via `<details>`.

The HTML is self-contained (base64-embedded styling) — phone-renderable
via `SendUserFile`.

## What the orchestrator does vs what the Haiku workers do

**Orchestrator (this skill, main session, Sonnet):** runs the
deterministic ledger script (Step 1), runs the batched-parallel fetch
(Step 2), partitions the ledger and fans out Haikus (Step 3), reads
every `haiku-<N>.yaml` and merges into `ledger.json` + `astra.yaml`
(Step 4), calls `astra validate` (Step 5), renders the report (Step
6). **Never reads cited PDFs in the main context** — that's a Haiku's
job, and reading PDFs in the main session would defeat the
bounded-worker property.

**Haiku workers (`Task` with `model="haiku"`, one per partition):**
each Haiku is given 5–8 cited papers and the manuscript claim rows
that cite them. Reads each cited PDF, classifies every row per the
verdict taxonomy, extracts verbatim quotes for `supported`/`weak`,
self-validates with `astra paper verify-quotes`, writes the YAML, and
exits. Bounded to its partition — never reads outside-partition
papers, never edits `astra.yaml`. The agent is
[`citation-verifier`](../../agents/citation-verifier.md), shipped as
part of this bundle; the orchestrator invokes it via `Task` with
`subagent_type="citation-verifier"` and `model="haiku"`.

This is the same separation paper-extraction makes between
`extract-paper-substrate.py` (deterministic, structural) and the
agent invoking the skill (semantic, judgment-bearing) — and the same
shape `lc-from-paper`'s LITERATURE phase uses to scale quote-finding
across many cited papers.

## Discipline

- **Verbatim quotes only.** Never paraphrase. Copy from the cached
  PDF as-is, including LaTeX math, ligatures, line breaks. The
  `astra validate --verify-evidence` gate is what makes this skill
  trustworthy; paraphrasing breaks the gate.
- **One verdict per use-site, not per key.** The same paper cited
  three times for three different claims gets three verdicts. Some
  may pass while others don't.
- **`unsupported` is a verdict, not a failure.** It's a finding the
  human acts on. The skill flags it loudly; it does not silently drop
  the citation.
- **Companion papers and in-prep work.** Papers I/III/IV/V of the
  UNIONS series and similar companion citations resolve to arXiv DOIs
  once posted; treat them like any cited paper. If a companion has no
  DOI yet (in-prep), the ledger records `verdict: "unverifiable_no_doi"`
  and the report calls it out — the skill does not attempt local-tex
  evidence in v1.
- **Software citations.** Some bibkeys point to method papers
  (TreeCorr → Jarvis et al. 2004 algorithm paper) rather than software
  records. Haikus accept a method-paper anchor as `supported` when the
  manuscript statement is about the method; flag as `wrong_paper` only
  when the topic genuinely doesn't match.
- **Idempotent.** Re-running on the same subject only re-verifies
  rows missing a verdict. To force re-verification of a specific
  citation, delete its row from `ledger.json` and re-run.

## Anti-patterns

- **One Haiku per cited paper, or one per use-site.** Wrong unit of
  partition. Aim for 5–8 cited papers per Haiku so the per-worker
  context covers a useful slice and the orchestration overhead stays
  small. Mirror `lc-from-paper`'s LITERATURE sizing.
- **Reading cited PDFs in the orchestrator's context.** That defeats
  the bounded-worker safety property. If you find yourself opening
  PDFs in the main session, spawn a Haiku.
- **Reaching for `lc-extractor`.** That agent is for `/lc-new`'s
  decision-extraction framing — analysis context + target decisions
  → prior insights about decisions. Different problem space. The
  citation-audit verifier agent is `citation-verifier` (this skill's
  bundled agent).
- **Paraphrasing in `quote.exact`.** Breaks `astra validate
  --verify-evidence`. Each Haiku's self-check should prevent this; if
  a quote arrives back unverified, treat it as fabricated and drop
  the verdict.
- **Auto-rewriting the manuscript.** This skill produces *evidence*
  for the human author to act on. It does not edit the `.tex`. The
  human decides whether to reword, replace the cite, or accept the
  finding.
- **Silently skipping `unverifiable_no_doi`.** Surface those
  prominently in the report — they are the cases where the manuscript
  cited something the system literally cannot check, which is exactly
  the arxiv-policy concern.

## See also

- [`paper-extraction`](../paper-extraction/SKILL.md) — the upstream
  skill producing the citation surface this skill consumes.
- [`astra paper add`](https://github.com/LightconeResearch/ASTRA) and
  `astra validate --verify-evidence` — the verification primitives.
- The hand-audit precedent: `ai-futures/felt/citation-audit` (April 2
  audit of the UNIONS B-modes manuscript) and
  `ai-futures/process/citation-provenance-workflow` (related: bib
  provenance).
