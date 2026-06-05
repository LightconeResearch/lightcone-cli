// reproduce-paper workflow — TEMPLATE, not a script to run verbatim.
//
// The autonomous middle of /lc-from-paper. The interactive ORIENT bookend (in
// the user's main session) has already produced, on disk in the workdir:
//   - astra.yaml          SKELETON: sub-analyses, inputs, outputs, narrative;
//                         no decisions:/findings:/recipes: yet
//   - targets/targets.md  the replication-target ledger VERIFY writes tests against
//   - PLAN.md             Goal + Fidelity intent (the STOPPING CRITERION) + Scope
//   - CLAUDE.md           paper identity, rules, pointers, disagreements log
//   - work/reference/     paper substrate (+ code/ + code-index.md when a repo exists)
// ...and the plan has been approved in plan mode. This workflow fills the spec,
// implements it, runs it, verifies it against the paper's claims, and reviews it.
//
// The shape is fixed; the SURFACES to tune per paper are the schemas, the model
// tier, and the per-phase contract files under references/. Each phase points its
// agents at references/<phase>.md rather than inlining a giant prompt — edit the
// reference, not this file, to change a phase's discipline.
//
// Spine: SPECIFY ∥ LITERATURE (pipeline, no barrier) → merge → IMPLEMENT (parallel)
// → merge → RUN → VERIFY (a test per claim; run→fix→rerun, bounded by intent)
// → REVIEW (report.html + structured summary returned to the main agent).
//
// Sibling of citation_audit_workflow.js: same fan-out → verify → synthesize
// philosophy, but here the work-list is discovered (sub-analyses, outputs,
// targets) and the "vote" is the per-paper test suite VERIFY generates, not a
// pre-written gate.

export const meta = {
  name: 'reproduce-paper',
  description: 'Reproduce a paper in ASTRA: specify ∥ literature → implement → run → verify-by-claim-tests → review',
  phases: [
    { title: 'Specify',   detail: 'fill decisions/findings per sub-analysis; resolve cited-paper quotes (pipelined)' },
    { title: 'Implement', detail: 'one worker per output, parallel; scripts + recipes' },
    { title: 'Run',       detail: 'lc run over the Snakemake DAG' },
    { title: 'Verify',    detail: 'a test per replication target; run → fix → rerun until pass or intent reached' },
    { title: 'Review',    detail: 'synthesize state, fix obvious gaps → report.html + summary back to the main agent' },
  ],
}

const REF = '.claude/skills/lc-from-paper/references'
const ASTRA = 'astra.yaml'
const TARGETS = 'targets/targets.md'
const INTENT = args?.intent || 'read PLAN.md "Fidelity intent" — the stopping criterion'

// ───────────────────────── schemas ─────────────────────────
// One structured return per worker; a single merge step writes astra.yaml so two
// agents never edit the spec at once. Adapt fields per paper.

const SPEC_SCHEMA = {
  type: 'object',
  required: ['sub_id', 'decisions', 'findings', 'insight_placeholders'],
  properties: {
    sub_id: { type: 'string' },
    decisions: { type: 'array', items: { type: 'object' }, description: 'astra Decision blocks (id, rationale, options[])' },
    findings: { type: 'array', items: { type: 'object' }, description: 'astra Finding blocks (full Insight shape)' },
    insight_placeholders: {
      type: 'array',
      description: 'prior_insights: each a syntactically-complete Insight with evidence:[{id,doi}] but NO quote yet — LITERATURE resolves these',
      items: { type: 'object' },
    },
    disagreements: { type: 'array', items: { type: 'string' }, description: 'material paper-vs-code conflicts, code taken canonical' },
    notes: { type: 'string' },
  },
}

const LIT_SCHEMA = {
  type: 'object',
  required: ['sub_id', 'resolutions', 'unresolved'],
  properties: {
    sub_id: { type: 'string' },
    resolutions: { type: 'array', items: { type: 'object' }, description: 'per placeholder: id + evidence[] each carrying quote:{exact,prefix,suffix} + location:{page}' },
    unresolved: { type: 'array', items: { type: 'object' }, description: 'placeholder id + one-line reason — no supporting quote found; goes to open-questions.md' },
  },
}

const IMPL_SCHEMA = {
  type: 'object',
  required: ['output_id', 'script_path', 'recipe'],
  properties: {
    output_id: { type: 'string' },
    script_path: { type: 'string' },
    recipe: { type: 'object', description: 'astra recipe: {command, inputs}' },
    requirements: { type: 'array', items: { type: 'string' }, description: 'pip deps this output needs' },
    disagreements: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}

const TEST_SCHEMA = {
  type: 'object',
  required: ['target_id', 'test_path', 'kind'],
  properties: {
    target_id: { type: 'string' },
    test_path: { type: 'string', description: 'tests/test_<target>.py' },
    kind: { type: 'string', enum: ['metric', 'table', 'figure'] },
    claim: { type: 'string', description: 'the paper claim the test encodes, verbatim where possible' },
    tolerance: { type: 'string', description: 'the stated uncertainty / structural bar the test asserts against' },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['results', 'all_pass', 'failing'],
  properties: {
    results: { type: 'array', items: { type: 'object', properties: {
      target_id: { type: 'string' }, pass: { type: 'boolean' }, reproduced: { type: 'string' },
      expected: { type: 'string' }, diagnosis: { type: 'string' } } } },
    all_pass: { type: 'boolean' },
    failing: { type: 'array', items: { type: 'string' }, description: 'target_ids still failing' },
  },
}

// ───────────── Phase 1+2: SPECIFY ∥ LITERATURE (pipeline) ─────────────
// Each sub-analysis is specified, then its citations resolved, as a pipeline:
// sub A's literature runs while sub B is still being specified. Workers RETURN
// structured output; a single barrier merge folds everything into astra.yaml.
phase('Specify')
const subs = JSON.parse((await agent(
  `Read ${ASTRA}. Return ONLY a JSON array of the sub-analysis ids (keys under analyses:, or ["root"] if the spec is monolithic).`,
  { label: 'list-subanalyses', phase: 'Specify' }
)).trim().replace(/^```json?|```$/g, ''))
log(`specifying ${subs.length} sub-analysis(es): ${subs.join(', ')}`)

// Each worker WRITES its structured output to work/notes/<phase>/<sub>.json and also
// returns it. The merge reads the notes dirs — so it sees BOTH stages (a pipeline only
// returns its final stage), and it's resume-safe (notes survive a re-run).
const specced = (await pipeline(
  subs,
  sub => agent(
    `SPECIFY sub-analysis "${sub}". Read ${REF}/specify.md for your full contract, then read the relevant ` +
    `paper text under work/reference/ and (if present) the code at the path code-index.md maps for "${sub}". ` +
    `Fidelity intent (governs how exhaustively to specify): ${INTENT}. Code is canonical where it disagrees ` +
    `materially with the paper; record such conflicts in disagreements[]. Do NOT edit ${ASTRA}. ` +
    `Write your structured output to work/notes/specify/${sub}.json (mkdir -p first) AND return it (SPEC_SCHEMA) — ` +
    `the merge reads the notes file; the return threads to LITERATURE.`,
    { label: `specify:${sub}`, phase: 'Specify', schema: SPEC_SCHEMA }
  ),
  (spec, sub) => agent(
    `LITERATURE for sub-analysis "${sub}". Read ${REF}/literature.md for your full contract. ` +
    `Resolve every prior_insights placeholder this sub-analysis just produced: fetch each cited paper's substrate ` +
    `(paper-extraction's deterministic script, batched), then find the verbatim quote in the cited paper that ` +
    `justifies the placeholder's claim — quote:{exact,prefix,suffix}+location:{page} per evidence entry. ` +
    `Placeholders: ${JSON.stringify(spec?.insight_placeholders ?? [])}. A placeholder with no supporting quote goes ` +
    `in unresolved[] — do NOT fabricate evidence. Do NOT edit ${ASTRA}. ` +
    `Write your output to work/notes/literature/${sub}.json (mkdir -p first) AND return it (LIT_SCHEMA).`,
    { label: `literature:${sub}`, phase: 'Literature', schema: LIT_SCHEMA }
  )
)).filter(Boolean)
log(`${specced.length} sub-analysis(es) specified + citations resolved`)

// barrier — single writer reads the notes and folds spec + literature into astra.yaml.
await agent(
  `Merge step (single writer for ${ASTRA}). Read every file under work/notes/specify/ and work/notes/literature/. ` +
  `For each sub-analysis: place its decisions/findings/prior_insights under its node in ${ASTRA}, and write each ` +
  `resolved quote/location onto the matching prior_insights evidence entry. Append every unresolved placeholder to ` +
  `open-questions.md with its reason, and every disagreement to CLAUDE.md's disagreements log. Then run: ` +
  `astra validate ${ASTRA} --verify-evidence — fix any structural breakage. Commit ` +
  `("specify+literature: decisions, findings, resolved citations").`,
  { label: 'merge:spec', phase: 'Specify' }
)

// ───────────────────── Phase 3: IMPLEMENT (parallel per output) ─────────────────────
phase('Implement')
const outputs = JSON.parse((await agent(
  `Read ${ASTRA}. Return ONLY a JSON array of output ids that need a recipe (every declared output across all ` +
  `sub-analyses that has no recipe yet).`,
  { label: 'list-outputs', phase: 'Implement' }
)).trim().replace(/^```json?|```$/g, ''))
log(`implementing ${outputs.length} output(s)`)

const implemented = (await parallel(outputs.map(out => () => agent(
  `IMPLEMENT output "${out}". Read ${REF}/implement.md for your full contract, the output's spec entry in ${ASTRA}, ` +
  `implementation-notes.md, and (canonical) the reference code at the path code-index.md maps for it. Write ` +
  `scripts/${out}.py (a DISJOINT file — do not touch other outputs' scripts or ${ASTRA}), parameterized from the ` +
  `decisions it consumes. Real data only. Write your recipe+requirements to work/notes/implement/${out}.json ` +
  `(mkdir -p first) AND return it (IMPL_SCHEMA) — the merge reads the notes files.`,
  { label: `implement:${out}`, phase: 'Implement', schema: IMPL_SCHEMA }
)))).filter(Boolean)
log(`${implemented.length} output(s) implemented`)

// barrier — single writer reads the notes, folds recipes into astra.yaml, reconciles requirements.txt, validates.
await agent(
  `Merge step (single writer for ${ASTRA}). Read every file under work/notes/implement/. Fold each output's recipe ` +
  `into its node in ${ASTRA}; union all requirements into requirements.txt; set container: per CLAUDE.md/scan if ` +
  `unset; append any disagreements to CLAUDE.md. Then: astra validate ${ASTRA}. Fix breakage, commit ` +
  `("implement: scripts + recipes").`,
  { label: 'merge:implement', phase: 'Implement' }
)

// ───────────────────────────── Phase 4: RUN ─────────────────────────────
phase('Run')
await agent(
  `RUN. Read ${REF}/run.md. Execute: lc run --universe baseline — this drives the Snakemake DAG; cluster jobs may ` +
  `be long, so Monitor the logs rather than polling. Iterate on run-failures (read the .log, fix the script/recipe/` +
  `requirements, re-run) until lc status --universe baseline shows every output ok. Then commit. Report the final ` +
  `lc status.`,
  { label: 'run:baseline', phase: 'Run' }
)

// ───────────── Phase 5: VERIFY — a test per claim, then a fix-loop bounded by intent ─────────────
// We cannot pre-write a gate for THIS paper's claims; we GENERATE one. Write a test
// per replication target, run the suite, and where a test fails diagnose+fix+rerun —
// looping until green or until the fidelity intent says "reasonable-ish, stop."
phase('Verify')

// how hard to push is the interview's job: derive a fix-round budget from the intent.
const budgetInfo = JSON.parse((await agent(
  `Read PLAN.md's "Fidelity intent" and ${TARGETS}. The intent is the stopping criterion: ${INTENT}. ` +
  `Return ONLY JSON {"max_fix_rounds": <int>, "posture": "<one line on how close is close enough per target priority>"}. ` +
  `Rough calibration: "afternoon/sanity" → 1-2; "overnight/headline" → 3-4; "a day or two" → 5-6; "no deadline" → 8+.`,
  { label: 'verify:budget', phase: 'Verify' }
)).trim().replace(/^```json?|```$/g, ''))
const MAX_ROUNDS = budgetInfo.max_fix_rounds ?? 3
log(`verify: ${MAX_ROUNDS} max fix rounds — ${budgetInfo.posture}`)

const targets = JSON.parse((await agent(
  `Read ${TARGETS}. Return ONLY a JSON array of replication-target ids (each maps to an output).`,
  { label: 'list-targets', phase: 'Verify' }
)).trim().replace(/^```json?|```$/g, ''))

// write a test per target — disjoint files, so parallel is safe.
await parallel(targets.map(t => () => agent(
  `WRITE A TEST for replication target "${t}". Read ${REF}/verify.md for the contract, the target's row in ${TARGETS} ` +
  `(priority, expected value + stated uncertainty, comparison guidance), the matching result in ` +
  `results/baseline/<output>/, and the paper text for what the claim actually is. Write tests/test_${t}.py: a metric ` +
  `test asserts the reproduced value is within the paper's stated uncertainty; a table test checks the key cells; a ` +
  `figure test asserts structural features (shape, ranges, peaks/ordering), NOT pixels. The test reads the result ` +
  `from results/baseline/ and the expected value from ${TARGETS}. Commit the test.`,
  { label: `test:${t}`, phase: 'Verify', schema: TEST_SCHEMA }
)))

// the fix-loop: run the suite; while failing && budget remains, fix the failing
// outputs and re-run the FULL suite (interdependence — a fix can regress a sibling).
let verdict = null
for (let round = 0; round <= MAX_ROUNDS; round++) {
  verdict = await agent(
    `Run the claim-test suite: lc run --universe baseline (re-materialize any stale outputs), then run ` +
    `tests/ (pytest). Return per-target pass/fail with reproduced vs expected and a one-line diagnosis for each ` +
    `failure. Do NOT fix anything in this step — just report.`,
    { label: `verify:run#${round}`, phase: 'Verify', schema: VERDICT_SCHEMA }
  )
  if (verdict.all_pass || round === MAX_ROUNDS) break
  log(`round ${round}: ${verdict.failing.length} failing — ${verdict.failing.join(', ')}`)
  // fix the failing targets (parallel where the failures are in disjoint outputs).
  await parallel(verdict.failing.map(t => () => agent(
    `FIX failing target "${t}". Read ${REF}/verify.md, the test tests/test_${t}.py, its diagnosis, the script that ` +
    `produces it, and the canonical reference code. Code is canonical where it disagrees with the paper. Make the ` +
    `smallest correct change to the implementation (script, recipe, or a decision's baseline value) so the claim ` +
    `holds — do NOT weaken the test to pass. If the gap is genuinely in the paper (under-specified, or our reading ` +
    `of intent says stop here), log it to open-questions.md instead. Commit the fix.`,
    { label: `fix:${t}#${round}`, phase: 'Verify' }
  )))
}
log(`verify done: ${verdict.all_pass ? 'all targets pass' : verdict.failing.length + ' below intent — logged'}`)

// ───────────── Phase 6: REVIEW — synthesize, fix obvious gaps, emit report + summary ─────────────
phase('Review')
const summary = await agent(
  `REVIEW (in-workflow). Read ${REF}/review.md for the in-workflow contract. Survey the whole reproduction: ${ASTRA}, ` +
  `the verify verdict (${JSON.stringify(verdict)}), results/baseline/, open-questions.md, PLAN.md's fidelity intent. ` +
  `Fix any obvious remaining problems (a mislabeled output, a recipe typo) but do NOT start a new fix campaign — the ` +
  `intent has been honored. Write report.html (a self-contained side-by-side of paper claims vs reproduced values, ` +
  `parchment palette, phone-renderable) summarizing where the reproduction landed against intent. Commit. Return a ` +
  `structured summary: targets passed/total, which landed below intent and why, open questions for the human, and the ` +
  `report.html path.`,
  { label: 'review:synthesize', phase: 'Review' }
)

return {
  intent: INTENT,
  verify: verdict,
  review: summary,
  report: 'report.html',
  note: 'Workflow done. Main agent: run the CLOSE-OUT bookend (figure-comparison, check-sentence-by-sentence, walk open-questions.md with the user).',
}
