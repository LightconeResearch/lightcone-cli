// reproduce-paper workflow — TEMPLATE, not a script to run verbatim.
//
// The autonomous middle of /lc-from-paper. The interactive ORIENT bookend (in the
// user's main session) has already produced, on disk in the workdir, and gated
// through plan mode:
//   - PLAN.md             the approved reproduction plan: Goal + Fidelity intent
//                         (the STOPPING CRITERION) + Scope + decomposition sketch
//   - CLAUDE.md           paper identity, rules, pointers, disagreements log
//   - work/reference/     paper substrate (+ code/ + code-index.md when a repo exists)
// This workflow builds the spec from that plan and carries it to a verified result.
//
// Each phase points its agents at references/<phase>.md — the contract lives in the
// reference (read it to change a phase's discipline), and the prompt here only
// carries the per-invocation specifics (which item, where to write notes, the schema).
//
// Spine: ARCHITECT (skeleton + targets) → SPECIFY ∥ LITERATURE (pipeline, no barrier)
// → merge → IMPLEMENT (parallel) → merge → RUN → VERIFY (a test per claim;
// run→fix→rerun, bounded by intent) → REVIEW (report.html + summary to the main agent).
//
// Sibling of citation_audit_workflow.js: same fan-out → verify → synthesize
// philosophy, but here the work-list is discovered (sub-analyses, outputs, targets)
// and the "vote" is the per-paper test suite VERIFY generates, not a pre-written gate.

export const meta = {
  name: 'reproduce-paper',
  description: 'Reproduce a paper in ASTRA: architect → specify ∥ literature → implement → run → verify-by-claim-tests → review',
  phases: [
    { title: 'Architect', detail: 'realize the approved plan into the astra.yaml skeleton + targets ledger' },
    { title: 'Specify',   detail: 'fill decisions/findings per sub-analysis; resolve cited-paper quotes (pipelined)' },
    { title: 'Implement', detail: 'one worker per output, parallel; scripts + recipes' },
    { title: 'Run',       detail: 'lc run over the Snakemake DAG' },
    { title: 'Verify',    detail: 'a test per replication target; run → fix → rerun until pass or intent reached' },
    { title: 'Review',    detail: 'synthesize state, fix obvious gaps → report.html + summary back to the main agent' },
  ],
}

// SKILL_ROOT: absolute path to this skill's directory, passed by the launcher
// (which resolves $CLAUDE_PLUGIN_ROOT — the skill ships as a plugin, so its files
// live under the plugin root, not the project). Falls back to the project-relative
// path for the legacy per-project copy layout.
const SKILL_ROOT = args?.skillRoot || '.claude/skills/lc-from-paper'
const REF = `${SKILL_ROOT}/references`
const ASTRA = 'astra.yaml'
const TARGETS = 'targets/targets.md'
const INTENT = args?.intent || 'read PLAN.md "Fidelity intent" — the stopping criterion'
const strip = s => s.trim().replace(/^```json?|```$/g, '')

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

// ───────────── Phase 1: ARCHITECT — realize the approved plan into the spec scaffolding ─────────────
// One agent (the decomposition is a holistic call, not a fan-out). Turns the approved
// PLAN.md + substrate into the astra.yaml SKELETON + the targets ledger VERIFY tests against.
phase('Architect')
await agent(
  `ARCHITECT. Read ${REF}/architect.md — that is your contract. From the approved PLAN.md and the substrate under ` +
  `work/reference/ (paper + code-index.md when present), write the ${ASTRA} SKELETON (sub-analyses, inputs, outputs, ` +
  `narrative — no decisions/findings/recipes yet) and ${TARGETS} (the replication-target ledger: each target with ` +
  `priority + expected value + stated uncertainty + comparison guidance). Then: astra validate ${ASTRA}. Commit.`,
  { label: 'architect', phase: 'Architect' }
)

// ───────────── Phase 2+3: SPECIFY ∥ LITERATURE (pipeline, no barrier) ─────────────
// Each sub-analysis is specified, then its citations resolved, as a pipeline: sub A's
// literature runs while sub B is still being specified. Workers WRITE structured output
// to work/notes/<phase>/<sub>.json (and return it); the merge reads the notes dirs — so
// it sees BOTH stages (a pipeline only returns its final stage) and is resume-safe.
phase('Specify')
const subs = JSON.parse(strip(await agent(
  `Read ${ASTRA}. Return ONLY a JSON array of the sub-analysis ids (keys under analyses:, or ["root"] if monolithic).`,
  { label: 'list-subanalyses', phase: 'Specify' }
)))
log(`specifying ${subs.length} sub-analysis(es): ${subs.join(', ')}`)

const specced = (await pipeline(
  subs,
  sub => agent(
    `You are the SPECIFY worker for sub-analysis "${sub}". Read ${REF}/specify.md — that is your full contract ` +
    `(what to read, the two-pass paper→code discipline, code-as-canonical, the citation-placeholder shape). ` +
    `Fidelity intent (how exhaustively to specify): ${INTENT}. ` +
    `Write your output to work/notes/specify/${sub}.json (mkdir -p first) AND return it (SPEC_SCHEMA) — the merge ` +
    `reads the notes file; the return threads to LITERATURE.`,
    { label: `specify:${sub}`, phase: 'Specify', schema: SPEC_SCHEMA }
  ),
  (spec, sub) => agent(
    `You are the LITERATURE worker for sub-analysis "${sub}". Read ${REF}/literature.md — that is your full contract ` +
    `(fetch each cited paper's substrate, find the verbatim quote that justifies each placeholder, never fabricate). ` +
    `Placeholders to resolve: ${JSON.stringify(spec?.insight_placeholders ?? [])}. ` +
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

// ───────────────────── Phase 4: IMPLEMENT (parallel per output) ─────────────────────
phase('Implement')
const outputs = JSON.parse(strip(await agent(
  `Read ${ASTRA}. Return ONLY a JSON array of output ids that still need a recipe (declared, no recipe yet).`,
  { label: 'list-outputs', phase: 'Implement' }
)))
log(`implementing ${outputs.length} output(s)`)

const implemented = (await parallel(outputs.map(out => () => agent(
  `You are the IMPLEMENT worker for output "${out}". Read ${REF}/implement.md — that is your full contract ` +
  `(write scripts/${out}.py as a DISJOINT file parameterized from its decisions, code-as-canonical, REAL DATA ONLY). ` +
  `Write your recipe+requirements to work/notes/implement/${out}.json (mkdir -p first) AND return it (IMPL_SCHEMA).`,
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

// ───────────────────────────── Phase 5: RUN ─────────────────────────────
phase('Run')
await agent(
  `RUN. Read ${REF}/run.md — that is your contract. Drive lc run --universe baseline to all-ok (Monitor long/cluster ` +
  `jobs rather than polling), iterate on failures, commit, and report the final lc status.`,
  { label: 'run:baseline', phase: 'Run' }
)

// ───────────── Phase 6: VERIFY — a test per claim, then a fix-loop bounded by intent ─────────────
// We cannot pre-write a gate for THIS paper's claims; we GENERATE one. Write a test per
// target, run the suite, and where a test fails diagnose+fix+rerun — until green or the
// fidelity intent says "reasonable-ish, stop." Full contract in references/verify.md.
phase('Verify')

// how hard to push is the interview's job: derive a fix-round budget from the intent.
const budgetInfo = JSON.parse(strip(await agent(
  `Read PLAN.md's "Fidelity intent" and ${TARGETS}. The intent is the stopping criterion: ${INTENT}. ` +
  `Return ONLY JSON {"max_fix_rounds": <int>, "posture": "<one line on how close is close enough per target priority>"}. ` +
  `Rough calibration: "afternoon/sanity" → 1-2; "overnight/headline" → 3-4; "a day or two" → 5-6; "no deadline" → 8+.`,
  { label: 'verify:budget', phase: 'Verify' }
)))
const MAX_ROUNDS = budgetInfo.max_fix_rounds ?? 3
log(`verify: ${MAX_ROUNDS} max fix rounds — ${budgetInfo.posture}`)

const targets = JSON.parse(strip(await agent(
  `Read ${TARGETS}. Return ONLY a JSON array of replication-target ids (each maps to an output).`,
  { label: 'list-targets', phase: 'Verify' }
)))

// write a test per target — disjoint files, so parallel is safe.
await parallel(targets.map(t => () => agent(
  `You are the VERIFY test-writer for target "${t}". Read ${REF}/verify.md Part 1 — that is your contract ` +
  `(encode the paper's claim as tests/test_${t}.py; the metric/table/figure bars). Read the target's row in ` +
  `${TARGETS} and the matching result in results/baseline/. Commit the test.`,
  { label: `test:${t}`, phase: 'Verify', schema: TEST_SCHEMA }
)))

// the fix-loop: run the suite; while failing && budget remains, fix the failing outputs
// and re-run the FULL suite (interdependence — a fix can regress a passing sibling).
let verdict = null
for (let round = 0; round <= MAX_ROUNDS; round++) {
  verdict = await agent(
    `Read ${REF}/verify.md Part 2. Run lc run --universe baseline (re-materialize stale outputs) then pytest tests/. ` +
    `Return the VERDICT_SCHEMA (per-target pass/fail + reproduced/expected + one-line diagnosis; all_pass; failing[]). ` +
    `Report only — fix nothing.`,
    { label: `verify:run#${round}`, phase: 'Verify', schema: VERDICT_SCHEMA }
  )
  if (verdict.all_pass || round === MAX_ROUNDS) break
  log(`round ${round}: ${verdict.failing.length} failing — ${verdict.failing.join(', ')}`)
  // fix the failing targets (parallel where the failures are in disjoint outputs).
  await parallel(verdict.failing.map(t => () => agent(
    `You are a VERIFY fix worker for failing target "${t}". Read ${REF}/verify.md Part 2 — that is your contract ` +
    `(smallest correct change to the IMPLEMENTATION so the claim holds; NEVER weaken the test; code-as-canonical; ` +
    `a genuine paper gap goes to open-questions.md). The diagnosis is in the verdict. Commit the fix.`,
    { label: `fix:${t}#${round}`, phase: 'Verify' }
  )))
}
log(`verify done: ${verdict.all_pass ? 'all targets pass' : verdict.failing.length + ' below intent — logged'}`)

// ───────────── Phase 7: REVIEW — synthesize, fix obvious gaps, emit report + summary ─────────────
phase('Review')
const summary = await agent(
  `REVIEW (in-workflow). Read ${REF}/review.md Part A — that is your contract. Survey the reproduction against ` +
  `PLAN.md's fidelity intent, fix only obvious remaining gaps (no new fix campaign), write report.html (a ` +
  `self-contained claims-vs-values side-by-side, parchment palette, phone-renderable), commit, and return the ` +
  `structured summary (targets passed/total, which landed below intent + why, open questions, report.html path). ` +
  `The verify verdict: ${JSON.stringify(verdict)}.`,
  { label: 'review:synthesize', phase: 'Review' }
)

return {
  intent: INTENT,
  verify: verdict,
  review: summary,
  report: 'report.html',
  note: 'Workflow done. Main agent: run the CLOSE-OUT bookend (figure-comparison, check-sentence-by-sentence, walk open-questions.md with the user).',
}
