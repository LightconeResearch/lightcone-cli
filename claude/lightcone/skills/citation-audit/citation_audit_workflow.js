// citation-audit workflow — TEMPLATE, not a script to run verbatim.
//
// Adapt per manuscript: the partition list, the per-stream hint blocks, and the
// model are the surfaces to tune. The shape is fixed: fan a verifier over every
// cited paper, each reading the paper's own source, returning multi-anchor
// verdicts that a DETERMINISTIC gate (source_match.py) has already self-checked.
//
// Pre-steps (run before this workflow, by the skill): paper-extraction produces
// work/reference/index.json; build_citation_ledger.py produces ledger.json;
// fetch_sources.py produces fetch_state.json + papers/. The orchestrator then
// partitions — joining ledger rows to their fetch_state backend — into
// work/citation-audit/partitions.json: paper bundles grouped ~5–8 per stream,
// each carrying backend (tex|pdf), source_dir/pdf_path, and the manuscript rows
// (claim + full prev/next sentence) that cite it.
//
// This is a deep-research SIBLING: same fan-out → verify → synthesize spine, but
// the bibliography IS the work-list (no discovery), and the "vote" is the
// deterministic gate, not a model judge.

export const meta = {
  name: 'citation-audit',
  description: 'Verify every manuscript citation against the cited paper\'s own source; multi-anchor, deterministic gate, per-citation HTML report',
  phases: [
    { title: 'Verify', detail: 'one verifier per partition, reading source, self-gated quotes' },
    { title: 'Synthesize', detail: 'merge verdicts → astra.yaml → validate → report.html' },
  ],
}

const WORKDIR = args?.workdir || 'work/citation-audit'
const REFDIR = args?.refdir || 'work/reference'
const SKILL = '.claude/skills/citation-audit/scripts'
const PARTITIONS = `${WORKDIR}/partitions.json`
const LEDGER = `${WORKDIR}/ledger.json`
const FETCH_STATE = `${WORKDIR}/fetch_state.json`
const ASTRA = `${REFDIR}/astra.yaml`

// Verdict schema — mirrors agents/citation-verifier.md. NO `identity`, NO
// `unverifiable_pre_arxiv`. Anchors are a LIST (one per facet); substrate is the
// backend axis (tex|pdf), not a verdict mode.
const SCHEMA = {
  type: 'object',
  required: ['verdicts'],
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['use_id', 'citation_key', 'verdict', 'anchors', 'notes'],
        properties: {
          use_id: { type: 'string' },
          citation_key: { type: 'string' },
          verdict: { type: 'string', enum: ['supported', 'weak', 'unsupported', 'wrong_paper', 'unverifiable'] },
          anchors: {
            type: 'array',
            description: '1..N verbatim quotes, one per facet of the claim; [] only when nothing anchors (note why).',
            items: {
              type: 'object',
              required: ['facet', 'exact', 'prefix', 'suffix', 'substrate'],
              properties: {
                facet: { type: 'string', description: 'which proposition this quote backs' },
                exact: { type: 'string' },
                prefix: { type: 'string' },
                suffix: { type: 'string' },
                section: { type: 'string' },
                substrate: { type: 'string', enum: ['tex', 'pdf'] },
                self_check: { type: 'string', description: 'verbatim source_match.py line (tex) / "fuzzy-ok" (pdf)' },
              },
            },
          },
          notes: { type: 'string' },
          suggested_rewording: { type: 'string' },
          doi_flag: { type: 'string', description: 'set if the resolved source is not the intended paper' },
        },
      },
    },
  },
}

// The verifier contract. Canonical spec lives in agents/citation-verifier.md;
// this is the inline form the workflow spawns. Keep the two in sync. The shared
// body (rules + gate + verdicts) is parameterized by the work ASSIGNMENT so the
// same contract drives both the partition fan-out and the coverage redispatch.
const verifierBody = (assignment) => `You are a citation-audit verifier (Opus). For each manuscript statement that
cites a paper, decide whether the cited paper's OWN SOURCE supports it, and anchor that judgment in
verbatim source quotes — ONE QUOTE PER FACET of the claim.

${assignment}

**One job, every cite.** Does this paper, in its own words, support this claim — and where? No
"identity" escape: a cite that merely NAMES a tool/method/survey still anchors the cited paper's own
self-introducing sentence ("TreeCorr is a code for…", "we present the aperture mass statistic…") —
naming is a claim whose evidence is the self-introduction. The only sink for "couldn't check" is
\`unverifiable\`, and only when no facet anchors at all.

**Read source, never a rendering** (backend=tex): Read main_tex (abstract+intro) to learn what the
paper is, then \`grep -rin "<keyword>" <source_dir>\` for claim terms, then Read the matching
section(s). Targeted reads only. For backend=pdf: Read pdf_path; quote ENGLISH NARRATIVE, not math.

**MULTI-ANCHOR — the core capability.** A composite claim has several facets; give ONE quote per facet,
each its own anchors[] entry with a \`facet\` label. "detected B modes at 2–5σ, linked to repeating
additive shear bias, PSF leakage, and photometric selection" = FOUR facets. If the source backs only
some, that is \`weak\` — name the unbacked facets in \`notes\` (the report shows your notes prominently;
no separate reworded sentence needed).

**CO-CITED references — judge THIS cite's SHARE, not the whole sentence.** A sentence often cites several
papers, and the facets may be SPLIT across them. Before assigning facets, decide which the manuscript
attributes to THIS cite:
  • *Distributive / parallel* — a construction that pairs facets with cites: "a detailed description of
    the catalogue and its systematics validation, we refer to \\citet{Guinot} and \\citet{HervasPeters}"
    pairs catalogue→Guinot, systematics→HervasPeters. Verify THIS cite ONLY against its paired facet.
  • *Redundant / list* — co-cites that each independently back the SAME claim: "a strong IA signal for
    red galaxies \\citep{A,B,C}". Each cite must back that one shared claim on its own.
A cite that fully anchors its share is \`supported\`. Do NOT downgrade to \`weak\` because it defers
(e.g. "see \\citet{X}") on a facet a CO-CITE carries — that facet was never this cite's to support.
In notes, state which facet(s) you assigned to this cite and why (esp. for a parallel construction).

**Anchor format (W3C TextQuoteSelector, verbatim from source):** exact (incl. LaTeX markup for tex —
never paraphrase/expand macros; for a quantitative facet the quote is THE VALUE WITH UNCERTAINTY,
$S_8 = 0.798^{+0.014}_{-0.015}$, never a title scrap); prefix/suffix (20–80 chars contiguous);
section; substrate (tex|pdf).

**Self-validate EVERY tex anchor before returning** (the gate):
  python3 ${SKILL}/source_match.py --source-dir "<source_dir>" --exact "<exact>" --prefix "<prefix>" --suffix "<suffix>"
"verified" → keep; "rejected: degenerate" → quote the clause that establishes the facet, not a scrap;
"not_found" → re-copy a contiguous span. Max 3 tries/anchor; drop an anchor that still fails. Put the
result line in self_check. For pdf anchors, set self_check "fuzzy-ok" (the synthesize step re-checks
pdf anchors with a fuzzy match). A row whose EVERY facet-anchor fails → verdict \`unverifiable\`.

**Verdicts:** supported (every facet ATTRIBUTED TO THIS CITE anchored) | weak (some of this cite's
facets / softer source — explain in notes) | unsupported (on-topic, no support) | wrong_paper
(different topic; if the
fetched source looks like the WRONG paper, set doi_flag and judge against the cite's intent) |
unverifiable (no anchorable quote despite genuine attempt — figure-only, or no fetchable source).

Return structured output: one verdict object per row, with its anchors[]. notes required on every row.`

const contract = (stream) => verifierBody(`**Your partition.** Read \`${PARTITIONS}\` and take the \`${stream}\` key — a list of paper bundles.
Each bundle: citation_key, doi, backend (tex|pdf), source_dir/main_tex/tex_files (tex) or pdf_path +
ads_metadata (pdf), citation_text, and rows[] (use_id, claim, manuscript_prefix, manuscript_suffix,
cite_command). The claim is the manuscript sentence; prefix/suffix are the full surrounding sentences.
Evaluate EVERY row.`)

const redispatchContract = (bundles) => verifierBody(`**Your work.** These citation rows were dropped by the first verify pass — re-verify ONLY these.
Bundles (JSON, same shape as a partition stream): ${JSON.stringify(bundles)}
Each bundle: citation_key, doi, backend (tex|pdf), source_dir/main_tex/tex_files (tex) or pdf_path +
ads_metadata (pdf), citation_text, rows[] (use_id, claim, manuscript_prefix, manuscript_suffix,
cite_command). Evaluate EVERY row.`)

// ---- Verify: fan a verifier over each partition stream --------------------
phase('Verify')
const streams = JSON.parse(await agent(
  `Read ${PARTITIONS} and return ONLY a JSON array of its top-level stream keys (the partition names).`,
  { label: 'list-streams', phase: 'Verify' }
).then(s => s.trim().replace(/^```json?|```$/g, '')))

const results = await parallel(streams.map(stream => () =>
  agent(contract(stream), { label: `verify:${stream}`, phase: 'Verify', model: 'opus', schema: SCHEMA })
))
const verdicts = results.filter(Boolean).flatMap(r => r.verdicts || [])
log(`collected ${verdicts.length} verdicts across ${streams.length} streams`)

// ---- Coverage guard: every partition row must get a verdict ----------------
// A stream carrying many use-sites can exhaust a single verifier, which then
// silently drops the tail (returns verdicts for the first papers only). Find the
// dropped rows and redispatch them in small bundles until covered or 2 rounds
// pass — the fan-out self-heals instead of quietly coming up short.
let redispatchRounds = 0
for (let round = 1; round <= 2; round++) {
  const have = new Set(verdicts.map(v => v.use_id))
  const missing = JSON.parse(await agent(
    `Read ${PARTITIONS} (stream → bundles; each bundle has citation_key, doi, backend, the source fields, citation_text, and rows[] with use_id). ` +
    `Return ONLY a JSON array of bundles — one per citation_key — keeping ONLY the rows[] whose use_id is NOT in this covered set, and dropping any bundle left with no rows: ${JSON.stringify([...have])}. ` +
    `Return [] if every partition row is already covered. No prose.`,
    { label: `coverage-check-r${round}`, phase: 'Verify' }
  ).then(s => s.trim().replace(/^```json?|```$/g, '')))
  const missRows = missing.reduce((n, b) => n + ((b.rows && b.rows.length) || 0), 0)
  if (!missRows) break
  redispatchRounds = round
  log(`coverage round ${round}: ${missRows} row(s) un-verdicted across ${missing.length} paper(s); redispatching`)
  const batches = []
  for (let i = 0; i < missing.length; i += 4) batches.push(missing.slice(i, i + 4))  // ≤4 papers/verifier so it can't re-overflow
  const recovered = (await parallel(batches.map(bundles => () =>
    agent(redispatchContract(bundles), { label: `verify:redispatch-r${round}`, phase: 'Verify', model: 'opus', schema: SCHEMA })
  ))).filter(Boolean).flatMap(r => r.verdicts || [])
  const seen = new Set(verdicts.map(v => v.use_id))
  verdicts.push(...recovered.filter(v => v.use_id && !seen.has(v.use_id)))
}
log(`final: ${verdicts.length} verdicts (${redispatchRounds} redispatch round(s))`)

// ---- Synthesize: barrier — merge, gate, materialize, validate, render -----
// The ledger (${LEDGER}) is the durable spine: verdicts.json is merged into it
// (anchors per row), the deterministic gate re-checks every anchor in place,
// then astra.yaml + report.html are materialized FROM the ledger. build_audit_yaml
// runs twice — merge+materialize, then materialize-only after the gate downgrades.
phase('Synthesize')
await agent(
  `Citation-audit synthesize step. ${verdicts.length} verdicts are in memory; I will write them for you.
1. Write this JSON to ${WORKDIR}/verdicts.json (verbatim):
${JSON.stringify({ verdicts })}
2. Run these in order (the ledger is the spine; do NOT hand-edit any output):
   # merge verdicts.json into the ledger (anchors per row) and materialize astra.yaml
   python3 ${SKILL}/build_audit_yaml.py --verdicts ${WORKDIR}/verdicts.json --ledger ${LEDGER} --astra-yaml ${ASTRA} --fetch-state ${FETCH_STATE}
   # deterministic re-gate: re-check every anchor against the cited source; drop failures, downgrade rows whose every anchor fails
   python3 ${SKILL}/verify_and_downgrade.py --ledger ${LEDGER} --state ${FETCH_STATE}
   # re-materialize so astra.yaml drops any downgraded rows (NOT a re-merge — the gated ledger is authoritative)
   python3 ${SKILL}/build_audit_yaml.py --ledger ${LEDGER} --astra-yaml ${ASTRA} --fetch-state ${FETCH_STATE} --materialize-only
   astra validate ${ASTRA}
   python3 ${SKILL}/render_report.py --ledger ${LEDGER} --reference-dir ${REFDIR} --out ${WORKDIR}/report.html
3. Report: counts by verdict, any rows the gate downgraded, and the report.html path.`,
  { label: 'synthesize', phase: 'Synthesize' }
)

return { verdicts: verdicts.length, streams: streams.length, redispatchRounds }
