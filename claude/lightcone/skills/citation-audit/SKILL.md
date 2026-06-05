---
name: citation-audit
description: >
  Audit every citation in a manuscript by verifying that the cited paper
  actually supports the statement that cites it. A deep-research sibling:
  same fan-out → fetch → verify → synthesize spine, but the bibliography
  IS the work-list (no discovery) and the back end is a per-citation
  verdict table, not a synthesized narrative. Consumes paper-extraction's
  `work/reference/index.json`, fetches each cited paper's own source
  (arXiv LaTeX by default, a fetched PDF when no source exists), then fans
  out parallel verifier workers (Opus) that read the source and return
  multi-anchor verdicts — one verbatim quote per facet of the claim
  (supported / weak / unsupported / wrong_paper / unverifiable). Every
  quote is re-checked against the source by a DETERMINISTIC gate
  (`source_match.py`) — the "vote" that makes the skill trustworthy.
  Verdicts materialize as `prior_insights:` on `astra.yaml` and a
  per-citation HTML report. Ships a workflow `.js` template that
  orchestrates the fan-out. Triggers on: "audit citations", "check
  citations", "verify references", "due diligence", "arxiv compliance",
  or `/citation-audit`.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task, Workflow, AskUserQuestion
---

# citation-audit

For every citation in a manuscript, find the verbatim quote(s) in the cited
paper that support the citing statement — or report that none exists. The
unit is the verdict per citation use-site; the aggregate is a per-citation
report saying which cites are clean, which need rewording, and which need to
be replaced.

**One verification path, applied to every cite.** There is no classifier and
no per-kind branching:

```
resolve source (.bib doi/eprint, never title-fuzzy)
  → fetch   (backend: arXiv .tex  |  fetched PDF)
  → anchor  (Opus): 1..N verbatim quotes, one per facet of the claim
  → gate    (deterministic): source_match.py for .tex | fuzzy/normalized for PDF
  → verdict: supported | weak | unsupported | wrong_paper | unverifiable
```

The only thing that ever varies is which **backend** supplies the source
text (LaTeX or PDF). That is a mechanical detail inside fetch, not a semantic
router — the question and the verdict vocabulary are identical for every
cite. A cite that merely *names* a thing (software, method, survey) is not a
special case: it anchors the cited paper's own self-introducing sentence
("TreeCorr is a code for computing two-point correlation functions…") like
any claim. The only sink for "couldn't check" is `unverifiable`, and only
when no facet anchors at all.

**Verification runs against source, not a rendering, whenever source exists.**
This is the central design choice. PDF text extraction is lossy — collapsed
math, ligatures, captcha pages saved as `.pdf`, ISO-8859 encodings — and that
lossiness pushes verifiers to quote topical title fragments because the real
evidence (a measured value with its uncertainty) is unreadable in extracted
text. The arXiv e-print source is the author's actual words: `$S_8 =
0.776\pm0.017$` is right there and quotable. So the skill fetches each cited
paper's source tarball and the verifiers read `.tex`; a fetched PDF is the
backend only when no arXiv source exists.

## Why this exists

[arXiv's May 2026 clarification](https://x.com/tdietterich/status/...) of its
code-of-conduct penalty made citation hygiene a first-class publication risk:
a hallucinated reference or LLM-residue meta-comment can trigger a one-year
ban. The cheap part of LLM-assisted writing (prose fluency) outruns the
expensive part (verifying every cite against the actual cited paper). This
skill closes that gap with the same evidence-and-verification discipline that
ASTRA applies to analyses.

It is also a positive instrument, not only a defensive one: the same process
surfaces *non-hallucinated* mis-citations — wrong-paper-cited, over-claimed
support, key→entry mismatches in `.bib` files — that hand audits miss. The
multi-anchor pass on the UNIONS B-modes manuscript took a grouped Stage-III
cite from `weak` to `supported` once each facet was anchored separately, and
surfaced two real manuscript errors (an over-attributed Planck tension, a
mis-stated survey area) that a single quote would have buried.

## Contract

**Input:** an ASTRA project where `paper-extraction` has populated
`work/reference/` for the manuscript under audit (one subject paper per
project). The skill reads `index.json`'s `citations:` block (citation key →
`{locations, citation, doi}`) plus the `.tex` source under `source/`.

**Output:**

1. `prior_insights:` populated on `work/reference/astra.yaml`, one entry per
   `supported`/`weak` use-site. Each carries `evidence: [ev1 .. evN]` — one
   verbatim source quote per facet of the claim. `astra validate astra.yaml`
   confirms structural validity; the deterministic source gate
   (`verify_and_downgrade.py`) is the evidence gate.
2. `work/citation-audit/ledger.json` — the durable spine: every
   `(citation_key, file, line, claim, manuscript context, doi)` tuple plus
   the verifier verdict and its anchors. Machine-friendly.
3. `work/citation-audit/report.html` — a **per-citation** HTML report
   (one entry per cite, sortable by severity or manuscript appearance);
   phone-renderable via `SendUserFile`.

Side effects: every cited paper with a resolvable arXiv eprint is fetched as
a **source tarball** into `work/citation-audit/papers/<doi-slug>/source/`
(UTF-8-normalized `.tex`); papers with no eprint get a **PDF** (ADS-gateway by
bibcode). The fetch outcome per DOI is recorded in `fetch_state.json`.

## When to use

- "Audit citations in [manuscript]" before submitting to arXiv or a journal
- "Verify the references" on a paper you're reviewing
- After substantial AI-assisted prose changes, to catch hallucinated or
  drifted citations

**Not for:** general literature review, finding new citations to add, or
judging the *quality* of the citations chosen. Scope is fidelity of existing
citations to their referents.

## Pipeline

The skill ships a workflow **`.js` template**,
[`citation_audit_workflow.js`](citation_audit_workflow.js), that orchestrates
the verify → synthesize spine. **It is a template to adapt, not a script to
run verbatim** — tune the partition list, the per-stream hint blocks, and the
model per manuscript. The deterministic pre-steps (ledger, fetch, partition)
run first; the workflow absorbs the fan-out and the synthesize barrier.

### Step 0 — Survey

Run from a project that contains `work/reference/` with a populated
`index.json`. If the subject's substrate isn't extracted yet, invoke
`/paper-extraction <arxiv-id-or-doi>` first.

```bash
ls work/reference/                                   # confirm substrate
jq '.citations | length' work/reference/index.json   # how many cites
```

If `work/citation-audit/ledger.json` exists, its verdicts are preserved on
re-build — survey-first, idempotent.

### Step 1 — Build the citation ledger (deterministic)

`scripts/build_citation_ledger.py` reads `index.json` plus the manuscript
`.tex` and emits `work/citation-audit/ledger.json`, one row per `\cite{}`
use-site:

```bash
python3 .claude/skills/citation-audit/scripts/build_citation_ledger.py \
  --reference-dir work/reference \
  --out work/citation-audit/ledger.json
```

Each row carries the **citation context** — the `claim` (the sentence
containing the cite) plus `manuscript_prefix` / `manuscript_suffix` (the full
surrounding sentences). `use_id` is a stable slug from bibkey + line; it
becomes the `prior_insight` id. Multi-cite calls (`\citep{a,b,c}`) produce one
row per key. Cites whose `.bib` entry has no DOI are marked
`unverifiable_no_doi` and surfaced in the report (never fuzzy-resolved — a
wrong DOI is worse than a missing one).

### Step 2 — Fetch source for every cited paper (deterministic)

`scripts/fetch_sources.py` walks the unique DOIs and fetches each cited
paper's source:

```bash
python3 .claude/skills/citation-audit/scripts/fetch_sources.py \
  --ledger work/citation-audit/ledger.json \
  --state  work/citation-audit/fetch_state.json
```

It resolves the eprint id from the `.bib`'s own `doi`/`eprint` (never
title-guessing), downloads `https://arxiv.org/e-print/<id>`, extracts to
`papers/<doi-slug>/source/`, and normalizes every `.tex` to UTF-8. Each DOI
lands in `fetch_state.json` with a **backend**:

| status | backend | verifier reads |
|---|---|---|
| `source_fetched` | `tex` | the `.tex` under `source_dir` (default) |
| `pdf` | `pdf` | a fetched PDF — arXiv-PDF-only, or the ADS-gateway PDF by bibcode when no eprint exists; quote English narrative |
| `unresolvable` | `none` | nothing — surfaced in the report |

A user-pre-placed `papers/<slug>/paper.pdf` is honored as a `pdf` backend.
PDF is a fetch backend, never a verdict. Idempotent — re-runs skip recorded
DOIs unless `--refresh`.

**ADS token.** The DOI→eprint/bibcode resolver (`resolve_arxiv.py`) needs a NASA
ADS API token — but only as a *fallback*: `fetch_sources.py` first uses the
eprint/bibcode the `.bib` already carries (the common case needs no ADS at all).
The token is loaded, in order, from `$ADS_API_TOKEN` / `$ADS_DEV_KEY`, then
`~/.ads/dev_key`, then **the user's login shell** (it is commonly exported only in
an interactive `~/.zshrc`/`~/.bashrc`, which the non-interactive pipeline shell
never sources — the loader sources `$SHELL -ic` to recover it). **If no token is
found and cites remain unresolved, ask the user** (via `AskUserQuestion`) to
generate one at <https://ui.adsabs.harvard.edu/user/settings/token> and either
`export ADS_API_TOKEN=…` in their shell rc or save it to `~/.ads/dev_key` — don't
silently degrade a third of the bibliography to `unverifiable`.

### Step 3 — Verify (the workflow fan-out)

Build the partition file (`partitions.json`): group the cited papers ~5–8 per
stream — ideally clustered by topic — joining each ledger row to its
`fetch_state` backend. Partitioning is orchestrator discretion; tight
per-stream context is the goal. **Balance streams by use-site (row) count, not
paper count:** a paper cited at many use-sites makes a stream much heavier than
its paper-count suggests, and a single verifier handed too many rows can exhaust
its budget and silently drop the tail. (The workflow's coverage guard catches and
redispatches any rows a verifier drops — see below — but a balanced partition
avoids the round-trip.)

The workflow then fans one **citation-verifier** (Opus) over each stream. The
agent reads the paper's own source, splits each composite claim into its
**facets**, and anchors **each facet separately** — one quote per facet. It
self-validates every `.tex` anchor with `source_match.py` before returning.
Run the template with the `Workflow` tool:

```js
Workflow({ scriptPath: '.claude/skills/citation-audit/citation_audit_workflow.js' })
```

After the fan-out, a **coverage guard** compares the returned verdicts against the
partition rows, and **redispatches any dropped rows** (small bundles, ≤2 rounds)
before synthesize — so a verifier that ran out of room on a heavy stream can't
leave use-sites silently un-verdicted. Adapt the template's paths per manuscript
(`BASE`/`SKILL`/`WORKDIR`/`REFDIR`); the coverage guard and contract are fixed.

For a small manuscript (≤10 pending rows) you can skip the workflow and spawn
one `citation-verifier` inline via `Task` (`subagent_type="citation-verifier"`,
`model="opus"`) — the agent contract is identical. The agent definition ships
at [`citation-verifier`](../../agents/citation-verifier.md).

Model choice is **Opus**, not a weaker model: finding the substantive
supporting quote (not a topical fragment) and splitting a composite claim into
facets is judgment a weaker model reward-hacks.

### Step 4 — Synthesize (merge → gate → materialize → validate → render)

The workflow's synthesize barrier writes the collected verdicts to
`verdicts.json` and runs, in order:

```bash
S=.claude/skills/citation-audit/scripts
# merge verdicts into the ledger (anchors per row) and materialize astra.yaml
python3 $S/build_audit_yaml.py --verdicts work/citation-audit/verdicts.json \
  --ledger work/citation-audit/ledger.json --astra-yaml work/reference/astra.yaml
# the deterministic gate — re-check every anchor against the cited source
python3 $S/verify_and_downgrade.py --ledger work/citation-audit/ledger.json \
  --state work/citation-audit/fetch_state.json
# re-materialize so astra.yaml drops any downgraded rows (NOT a re-merge —
# --materialize-only keeps the gated ledger authoritative)
python3 $S/build_audit_yaml.py --ledger work/citation-audit/ledger.json \
  --astra-yaml work/reference/astra.yaml --materialize-only
astra validate work/reference/astra.yaml
python3 $S/render_report.py --ledger work/citation-audit/ledger.json \
  --reference-dir work/reference --out work/citation-audit/report.html
```

The ledger is the durable spine: `verdicts.json` merges into it (storing
`anchors` per row), the gate re-checks each anchor in place, and astra.yaml +
report.html are materialized *from* the ledger.

**The gate is the "vote."** deep-research votes on each claim with model
judges; here the "vote" is the **deterministic** `source_match.py` gate — a
quote either appears contiguously in the source (whitespace-normalized, and
clearing a substance bar) or it doesn't. For every `supported`/`weak` row,
`verify_and_downgrade.py` re-checks each anchor; a `.tex` anchor must match
contiguously, a `pdf` anchor by an order-sensitive fuzzy `partial_ratio`
(≥ 0.80) over the extracted text. An anchor that fails is dropped; a row whose
**every** anchor fails is downgraded to `unverifiable`. This is stronger than a
model judge and never a model judge. It is what makes the verdict trustworthy.

**PDF tools — ask the user to install if missing.** The gate reads `pdf`-backend
papers with **PyMuPDF** (`pip install pymupdf`), OCR-ing image pages via
**Tesseract** (`brew install tesseract`) — pre-arXiv papers (Bertin 1996,
Landy-Szalay 1993, …) are very often image scans, and without OCR the gate sees
~200 chars and can't confirm anything. If `verify_and_downgrade.py` prints the
`⚠ PDF tools missing` banner, **stop and ask the user (`AskUserQuestion`) to
install them** — and surface the same ask when presenting the report, since
missing tools are *why* those cites read `unverifiable`. Don't leave pdf cites
silently unverifiable. Even with OCR, an image scan's text is noisy:
a genuine quote may fall below the fuzzy bar and be reported honestly as
"source is an image scan … gate cannot deterministically confirm" — a tooling
limit, distinct from a quote that isn't there.

`astra validate astra.yaml` (without `--verify-evidence`) runs for
**structural** schema validation of the materialized insights; the source gate
above is the evidence gate. (The PDF-based `--verify-evidence` is not the gate
here — a quote copied from `.tex` carries author markup the PDF text layer
mangles. Upstream gaps filed as astra-tools #91 and #92.)

### The report

`render_report.py` produces a **per-citation** report on the editorial
parchment palette: one entry per cite, sortable by **severity** (worst first —
wrong_paper / unsupported / weak / unverifiable on top, supported below) or
**appearance** (manuscript order). Collapsed, an entry shows just the citing
sentence; expanding it fades the surrounding sentences in *around* the cite
and lists each facet's supporting quote, attributed to the cited paper. The
header carries the manuscript title and a one-line health summary. The HTML is
self-contained (Google-Fonts `<link>`s with serif fallback) — phone-renderable
via `SendUserFile`.

## The verdict taxonomy

| Verdict | When |
|---|---|
| `supported` | Every checkable facet of the claim is anchored by a substantive verbatim quote. |
| `weak` | Some facets anchored, others not, **or** the source supports a narrower/softer version. Names the unbacked facets; gives `suggested_rewording`. |
| `unsupported` | On-topic, but the source does not make the specific point(s) the manuscript claims. |
| `wrong_paper` | The paper is about a different topic; the bibkey likely points at the wrong reference. (If the *fetched source* looks wrong — a phantom/mis-resolved DOI — the verifier sets `doi_flag` and judges against the cite's intent.) |
| `unverifiable` | No anchorable quote despite a genuine attempt — apparent support that lives only in a figure, or (rare) a cite with no fetchable source at all. A tooling limit, not a content judgment; never used to dodge a quote that exists. |

Pre-verification ledger states (`unverifiable_no_doi`, `extraction_error`,
`pending`) are surfaced in the report but never materialize as ASTRA insights
(no quote to verify).

## Discipline

- **Verbatim quotes from source only.** Never paraphrase. Copy from the cited
  paper's `.tex` as-is, including LaTeX math and markup. The `source_match.py`
  gate is what makes the skill trustworthy; paraphrasing or macro-expanding
  breaks the contiguous-context check.
- **Quote the substance, not the topic.** For a quantitative facet the
  supporting quote is the measured value with its uncertainty as written —
  never a title fragment or survey middle-name. `source_match.py` enforces a
  substance bar: a quote with no measured-value signal and fewer than ~5 words
  is rejected as degenerate.
- **One quote per facet.** A composite claim ("detected B modes at 2–5σ, linked
  to additive shear bias, PSF leakage, and photometric selection" = four
  facets) gets one anchor per facet. A single quote for the whole composite
  buries the gaps; per-facet anchoring surfaces them. If the source backs only
  some facets, that is `weak`.
- **A naming cite still anchors a real sentence.** Software/method/survey cites
  anchor the cited paper's own self-introducing sentence — not an excuse to
  skip the quote.
- **One verdict per use-site, not per key.** The same paper cited three times
  for three claims gets three verdicts.
- **`unsupported` is a verdict, not a failure.** It's a finding the human acts
  on. The skill flags it loudly; it does not silently drop the citation.
- **Never fuzzy-resolve a DOI.** Trust the `.bib`'s own `doi`/`eprint`; record
  `unverifiable_no_doi` when it has neither. A wrong-but-plausible DOI sends the
  verifier to the wrong paper — worse than a flagged miss.
- **Idempotent.** Re-building the ledger preserves existing verdicts. To force
  re-verification of a cite, drop its row and re-run.

## Anti-patterns

- **A classifier or per-kind branch.** There is one path. Naming cites,
  pre-arXiv cites, quantitative cites — all run the same fetch → anchor → gate.
- **Reading cited papers in the orchestrator's context.** That defeats the
  bounded-worker property. Spawn a verifier; don't open cited `.tex`/PDFs in the
  main session.
- **A single quote for a composite claim.** Split it into facets; one anchor
  each.
- **Reaching for `lc-extractor`.** That agent is for `/lc-new`'s decision
  extraction — different problem space. The citation-audit verifier is
  `citation-verifier`.
- **Paraphrasing in an anchor's `exact`.** Breaks `source_match.py`. If a quote
  arrives unverified, treat it as fabricated and drop it.
- **Auto-rewriting the manuscript.** This skill produces *evidence* for the
  human author to act on. It does not edit the `.tex`.

## See also

- [`citation_audit_workflow.js`](citation_audit_workflow.js) — the workflow
  template the skill ships; adapt per manuscript.
- [`citation-verifier`](../../agents/citation-verifier.md) — the per-partition
  verifier agent (the canonical contract; the workflow inlines a mirror).
- [`paper-extraction`](../paper-extraction/SKILL.md) — the upstream skill
  producing the citation surface this skill consumes;
  [`references/arxiv-source.md`](../paper-extraction/references/arxiv-source.md)
  is the source-fetch machinery `fetch_sources.py` extends to cited papers.
- `scripts/source_match.py` — the quote-against-source matcher; the verifier's
  self-check CLI and the gate's import.
