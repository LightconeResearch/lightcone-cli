# ARCHITECT — the decomposition step (ORIENT stage 5)

ARCHITECT is not a workflow phase. It is the **decomposition step of building the plan**, done in the user's main session as stage 5 of ORIENT — the one place in the whole reproduction where the rich paper-and-code context is already loaded in a single mind. `/paper-extraction` and `/lc-from-code`'s scan ran inline in the stages just before this one; their on-disk substrate is your input, and the architecture you draw here is what the autonomous Workflow then fills, implements, runs, and verifies. Spend the context now: every structural call you get right here is a call the bounded workers downstream never have to reconstruct.

Its job is to **reconcile the paper's structure against the reference code, choose the sub-analysis decomposition, declare inputs and outputs, and author the structural narrative** — and to produce two concrete artifacts that persist as part of the plan:

1. **`astra.yaml` skeleton** — sub-analyses, inputs, outputs, per-analysis `narrative:` prose. **No `decisions:`, `findings:`, `prior_insights:` content, or `recipe:` blocks yet** — those are SPECIFY's and IMPLEMENT's work inside the Workflow. You declare *what the analyses are*; the Workflow decides *what's inside each one*.
2. **`targets/targets.md`** — the replication-target ledger: every target with priority, expected value, stated uncertainty, and comparison guidance. This is the artifact VERIFY writes tests against; the quality of your ledger is the quality of the eventual gate.

Splitting structure from content is what keeps the Workflow's per-phase workers bounded and stateless: a SPECIFY worker reads one sub-analysis's skeleton node plus the paper/code it points to, fills its decisions and findings, and returns structured output. It never has to invent the decomposition under load. ARCHITECT pays that cost once, here, with the whole picture in view.

## Inputs

All already on disk from the earlier ORIENT stages — read the indices, then reach into source/code only for specifics. Do **not** absorb the paper or codebase whole; the fan-out's whole value is that no single context has to.

- **The interview answers (in-session)** — scope, fidelity intent, decomposition hints, paper-specific conventions. Scope fences what `outputs:` belong in the skeleton; fidelity intent is the stopping criterion and rides into `PLAN.md` and the Workflow's `args.intent`, but it also shapes the ledger (which targets are primary vs secondary, how tight a tolerance each carries).
- **`work/reference/index.json`** — paper-side structural index: figures, tables, section outline with line numbers, citations with resolved DOIs. The structural surface.
- **`work/reference/astra.yaml`** — paper-extraction's ASTRA-shape stub of the paper itself: id, title, `narrative.summary` from the abstract, and often `findings:` carrying the paper's *claimed* numerical results. This is the primary feedstock for `targets/targets.md` — the paper's own claimed values, with their uncertainties.
- **`work/reference/code-index.md`** — code-side inventory from `/lc-from-code`'s scan: script inventory, natural decomposition, candidate decisions with `file:line` refs, module map, entry-points, external-data dependencies, container hints. Present only when a reference repo exists.
- **`work/reference/source/` (Path A) or `work/reference/document.md` (Path B)** — paper text. Grep for specific facts (a table cell, a tolerance, a stated σ); never re-read whole.
- **`work/reference/code/`** — the cloned reference code, when present. Read targeted modules when `code-index.md` doesn't settle a structural question (e.g. where a real stage boundary falls).

## Output 1 — the `astra.yaml` skeleton

### What to do

1. **Reconcile sub-analysis decompositions.** Read `code-index.md`'s natural-decomposition section against `index.json`'s section outline. Where paper and code agree on a stage, use that name. Where they disagree, **code's structure is canonical for stage boundaries** — the paper compresses for narrative; the code reveals the actual seams. Where code is absent or thin, follow the paper alone. Where module boundaries are genuinely ambiguous, read the relevant modules under `work/reference/code/` to settle it. This is the code-as-canonical seam discipline applied at the structural level: the same rule that, deeper in, makes SPECIFY take the code's numeric defaults makes ARCHITECT take the code's stage cuts.

2. **Choose: one analysis or sub-analyses?** If the paper runs end-to-end with no clean intermediate handoff, write a single (monolithic) analysis — the Workflow's SPECIFY then treats it as `["root"]`. If it has genuinely independent stages — each stage's output flowing as the next's input — write sub-analyses under `analyses:`. Sub-analysis ids are **noun phrases** (`reconstruction`, `clustering`, `bao_fit`), unique across the spec, and must avoid the reserved set: `inputs`, `outputs`, `decisions`, `findings`, `prior_insights`, `analyses`, `options`, `content`, `narrative`.

3. **Wire inputs and outputs at the sub-analysis level.** For each sub-analysis:
   - Declare `inputs:` from `code-index.md`'s external-data dependencies plus any paper-named external datasets. Give each a stable id and a one-line name; the depth (acquisition path, selection criteria) is SPECIFY's, not yours.
   - Declare `outputs:` matching the result loci from `index.json` (the in-scope figures and tables) plus any intermediate artifact a downstream sub-analysis consumes. Tag each output's `priority:` from the paper's emphasis. **Interview Scope takes precedence** — if the user wants only Figure 3 and Table 2, only those land as `outputs:`; the rest are out-of-scope, noted as such in `PLAN.md`.

4. **Author the structural narrative.** Invoke **[`/narrative`](../../narrative/SKILL.md) in paper-reproduction mode** for the prose — it carries the discipline on the five keys, reserved-name collisions, real-subjects/real-verbs voice, and the data-flow requirement. Author `narrative:` prose at root and per-sub-analysis level. **No anchors yet** (`#decisions.<id>`, `#findings.<id>` and friends): the entries those would point at don't exist until SPECIFY runs, and a dangling anchor fails validation. SPECIFY weaves anchors in as it authors the content they reference. When sub-analyses exist, the root narrative **must** include a top-down end-to-end data-flow paragraph (per the narrative skill's data-flow rules) — it is the cold-entry map for every later reader, human or worker.

5. **Validate.** `astra validate astra.yaml` must return clean — the skeleton's structural fields and narrative prose pass schema checks even with the content blocks empty. (`--verify-evidence` is *not* run here; there is no evidence to verify yet.)

### Skeleton shape

```yaml
# Skeleton: structure + narrative only. The Workflow's SPECIFY fills
# decisions/findings/prior_insights and weaves anchors into the narrative;
# IMPLEMENT adds each output's recipe.
id: <paper-slug>
title: "<paper title>"
doi: <doi>

narrative:
  summary: |
    <high-level paragraph for the root analysis — no anchors yet>
  methods: |
    <end-to-end data-flow paragraph; required when sub-analyses exist>

analyses:
  <sub-analysis-id-1>:               # noun phrase, unique, not reserved
    narrative:
      summary: |
        <prose for this sub-analysis>
    inputs:
      <input-id>:
        name: <stable name; acquisition depth is SPECIFY's>
    outputs:
      <output-id>:
        type: figure | table | metric | data-product
        priority: primary | secondary
        description: <one line on what this output is>
    # no decisions:/findings:/prior_insights:/recipe: — the Workflow fills these

  <sub-analysis-id-2>:
    ...
```

### Rules

- **Skeleton, not snapshot.** Do not half-author `decisions:`/`findings:`/`prior_insights:` content or `recipe:` blocks. Leaving them off (rather than writing empty placeholders) keeps it honest that ARCHITECT's job is structural and the Workflow fills the rest.
- **Code-as-canonical for structure.** Where paper and code disagree on the decomposition, the code's stage boundaries win; the paper supplies the words to describe them.
- **Targeted scope wins.** Interview Scope fences which outputs land. No out-of-scope figures sneaking in; no in-scope target missed.
- **Narrative prose, no anchors.** Author the `narrative:` keys via `/narrative`; do not add `#`-anchors until the targets exist.
- **Validate before the plan is presented.** `astra validate astra.yaml` clean is a precondition for the plan-mode gate.

## Output 2 — `targets/targets.md` (the replication-target ledger)

This is the artifact that makes VERIFY possible. We cannot pre-write a gate for a specific paper's claims — the claims *are* the paper — so VERIFY **generates** the gate by writing one test per row in this ledger. A target whose expected value, uncertainty, and comparison guidance you specify precisely becomes a sharp test; a vague row becomes a vague test. This is where the reproduction's eventual fidelity is decided.

For every in-scope replication target — one per primary/secondary output the paper makes a checkable claim about — write a row carrying:

| Field | What it holds | Source |
|---|---|---|
| **id** | stable slug, maps to an `astra.yaml` output | your decomposition |
| **output** | the `<sub>.<output-id>` it verifies | the skeleton |
| **priority** | primary / secondary — drives how hard VERIFY pushes it | paper emphasis + interview scope |
| **kind** | `metric` \| `table` \| `figure` — the test shape VERIFY will write | the output type |
| **expected value** | the paper's claimed number(s), table cell(s), or figure features — verbatim where possible | `index.json`, the paper-extraction `astra.yaml`'s `findings:`, grep into source |
| **stated uncertainty** | the σ / error bar / tolerance *as the paper states it* — this is what a `metric` test asserts within | the paper's own quoted error |
| **comparison guidance** | how to judge a match: for a metric, "within stated σ"; for a table, which cells are load-bearing; for a figure, which structural features (shape, ranges, peak locations, ordering) matter — never pixels | your judgment from the paper |

The stated-uncertainty column is load-bearing: VERIFY's metric tests assert the reproduced value falls within *the paper's own quoted error*, not an invented tolerance. Where the paper gives no explicit uncertainty for a claimed value, say so in the row and propose a defensible bar (and let it surface at the plan gate). For figure targets, the comparison guidance is the whole test — name the features a reader would check by eye, because that is what VERIFY encodes.

Priority is how the fidelity intent reaches each target: a "headline within stated uncertainty, overnight" intent means VERIFY pushes primary targets to green and accepts secondary targets close; "every target lined up, no deadline" means it pushes them all. You set priority here so VERIFY's bounded fix-loop knows where to spend.

## When you have no code

When the interview established no public repo (and `code-status.yaml` records `found: false`), the code-as-canonical seam self-disables: the decomposition follows the paper's structure alone, stage boundaries come from the section outline, and the skeleton's narrative leans on the paper's prose. Expect the eventual SPECIFY/IMPLEMENT to converge more slowly without a canonical reference; the ledger's comparison guidance carries more weight as the only fidelity anchor. Note the paper-only posture in `PLAN.md`.

## Hand-off

ARCHITECT's two artifacts join `PLAN.md` and `CLAUDE.md` in the commit that closes ORIENT (per SKILL.md's plan-mode gate). The Workflow then reads them as fixed inputs:

- its SPECIFY phase lists sub-analyses from the skeleton's `analyses:` keys and fills each node's content;
- its IMPLEMENT phase lists the declared outputs and writes a recipe per output;
- its VERIFY phase lists targets from `targets/targets.md` and writes a test per row.

So the discipline here is: **make the structure right and the ledger sharp, because the autonomous middle will not re-litigate either.** Anything genuinely unresolved at this point — an ambiguous stage boundary you couldn't settle, a target with no stated uncertainty — is surfaced at the plan-mode gate (it is the user's one editorial pass) rather than left for a worker to guess at.

## See also

- [`orient.md`](orient.md) — the bookend ARCHITECT is stage 5 of; the interview stages that feed it.
- [`../templates/plan.md`](../templates/plan.md) — `PLAN.md` carries the human-readable contract (Goal, Fidelity intent + stopping criterion, Scope, Targets, Decomposition, Evidence) that this skeleton + ledger make concrete.
- [`specify.md`](specify.md), [`implement.md`](implement.md), [`verify.md`](verify.md) — the Workflow phases that consume the skeleton and ledger.
- [`/narrative`](../../narrative/SKILL.md) — the prose author for the skeleton's `narrative:` blocks (paper-reproduction mode).
