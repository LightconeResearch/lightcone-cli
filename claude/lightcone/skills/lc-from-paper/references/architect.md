# ARCHITECT — write the stub `astra.yaml`

ARCHITECT is the structural seam: decide the sub-analysis decomposition, wire the inputs and outputs at the sub-analysis level, and author high-level narrative prose for each analysis — all in one stub `astra.yaml`. SPECIFY then fills the stub with `decisions:`, `prior_insights:`, `findings:`, and `astra-anchor:` references. Splitting **structure** from **content** keeps each phase's cognitive load manageable: ARCHITECT decides *what the analyses are*; SPECIFY decides *what's inside each one*.

This phase runs as the orchestrator-spawned `architect` sub-agent. The heavy work of *understanding* the paper and code already happened in ACQUIRE: paper-expert and code-expert are alive with deep context. ARCHITECT reads their indices, queries them via `SendMessage` for anything the indices don't cover, writes the stub, and self-reviews. No re-ingestion.

## Inputs

- `work/reference/index.json` — paper-side structural index from `/paper-extraction` (figures, tables, section outline with line numbers, citations with resolved DOIs)
- `work/reference/astra.yaml` — paper-extraction's ASTRA-shape stub of the paper itself: id, name, `narrative.summary` (from abstract), optionally `findings:` (paper's claimed numerical results)
- `work/reference/code-index.md` — code-side inventory from code-expert's scan: script inventory, candidate decisions with file:line refs, module map, entry-points, external data dependencies, container hints
- **paper-expert** (agent ID handed in by the orchestrator) — reachable via `SendMessage`. Ask anything the indices don't cover: "what does the paper say about the apodization choice", "which figures are primary vs secondary", "where does the paper define the fiducial cosmology", etc.
- **code-expert** (agent ID handed in by the orchestrator) — reachable via `SendMessage`. Ask: "which module produces the BAO fit posteriors", "where is the magnitude cut applied", "is there a config file we should treat as the canonical baseline", etc.
- CLAUDE.md — the per-paper artifact at the workdir root; its **Goal** section names the user's intended replication targets and fidelity intent.
- `work/notes/notes.md` — user-supplied prior notes, if any.

## Outputs

- `astra.yaml` — **stub form**: sub-analyses named, architecture wired (inputs / outputs declared at the sub-analysis level), high-level `narrative:` prose blocks per analysis. **No `decisions:`, `prior_insights:`, `findings:`, or `astra-anchor:` references yet** — those entries don't exist for the narrative to reference.
- `work/notes/architect/review-round-<N>.md` — each self-review round's findings (one file per round; how many rounds depends on the rigor setting the orchestrator chose for this spawn).

The architect sub-agent's transcript persists alongside paper-expert and code-expert — later phases can `SendMessage` it with "you wrote this stub; why this decomposition?" if a downstream question needs the writing-time reasoning.

## Step 1 — Write the stub `astra.yaml`

Read the three indices first. Then query the experts as you write — paper-expert for paper-specific facts, code-expert for code-specific facts. Don't try to absorb the paper or code yourself; the experts already have that context built up.

### What to do

1. **Reconcile sub-analysis decompositions.** Read `code-index.md`'s natural-decomposition section and `index.json`'s section outline. Where paper and code agree on a stage, use that name (noun-phrase, e.g. `reconstruction`). Where they disagree, **code's structure is canonical for stage boundaries** — the paper compresses; the code reveals the actual decomposition. Where code is absent or thin, follow the paper alone. Ask code-expert to clarify any module-boundary ambiguity; ask paper-expert how the paper itself frames stage boundaries.
2. **Choose: one analysis or sub-analyses?** If the paper has only one stage end-to-end (no clean intermediate handoffs), write a single analysis. If it has genuinely independent stages (each stage's output flows as the next's input), write sub-analyses. Sub-analysis IDs must be noun phrases: `reconstruction`, `clustering`, `bao_fit`. Avoid reserved names: `inputs`, `outputs`, `decisions`, `findings`, `prior_insights`, `analyses`, `options`, `content`, `narrative`.
3. **Wire inputs and outputs at the sub-analysis level.** For each sub-analysis:
   - Declare `inputs:` from `code-index.md`'s External-data-dependencies plus any paper-named external datasets. The depth (acquisition path, selection criteria) is SPECIFY's; ARCHITECT names the input and gives it a stable id.
   - Declare `outputs:` matching the result loci from `index.json` (figures + tables) plus any intermediate artifacts a downstream sub-analysis consumes. Tag each output's `priority:` from the paper's emphasis (primary / secondary). **The reproduction's targeted scope from CLAUDE.md's Goal takes precedence** — if the user only wants Figure 3 and Table 2, only those land as `outputs:`; the rest are out-of-scope and noted as such.
   - Ask paper-expert which results the paper itself emphasizes if priority is unclear.
4. **Author the root and per-analysis narrative.** Invoke `/narrative` for prose authoring (it carries the discipline on reserved names, voice, the data-flow paragraph requirement). High-level prose only — **no `astra-anchor:` references yet**, because the entries those would point at don't exist. SPECIFY will weave in anchors as it authors `decisions:` / `prior_insights:` / `findings:` per sub-analysis. The root `narrative:` MUST include a top-down end-to-end data-flow paragraph (per the narrative skill's data-flow rules) when sub-analyses exist.
5. **Validate.** `astra validate astra.yaml` must return clean — even with empty `decisions:` / `prior_insights:` / `findings:` blocks, the structural fields and narrative prose must pass schema checks.

### Stub shape — what `astra.yaml` looks like after ARCHITECT

```yaml
# Stub: structure + narrative; SPECIFY fills decisions, findings, prior_insights, evidence, anchors.
id: <paper-slug>
title: "<paper title>"
doi: <doi>

narrative:
  summary: |
    <high-level paragraph for the root analysis>
  methods: |
    <data-flow paragraph; required when sub-analyses exist>

analyses:
  <sub-analysis-id-1>:
    narrative:
      summary: |
        <prose for this sub-analysis>
    inputs:
      <input-id>:
        <stable name; depth lives in SPECIFY>
    outputs:
      <output-id>:
        type: figure | table | metric | data-product
        priority: primary | secondary
        description: |
          <one-line on what this output is>
    decisions: {}      # SPECIFY fills
    prior_insights: {} # SPECIFY records placeholders (citation only), LITERATURE resolves evidence
    findings: {}       # SPECIFY fills

  <sub-analysis-id-2>:
    ...
```

### Rules for Step 1

- **Stub, not snapshot.** Don't try to author content for `decisions:`, `prior_insights:`, `findings:`. Those go in SPECIFY. Your job is the structural skeleton.
- **Reserved names.** Sub-analysis IDs are noun phrases; avoid the reserved set. Each ID must be unique across the spec.
- **Code-as-canonical for structure.** Where paper and code disagree on the decomposition, the code's structure is canonical (the paper compresses for narrative; the code reveals real seams).
- **Targeted scope wins.** CLAUDE.md's **Goal** scopes the reproduction. If the user only wants Figures 3–4 plus Table 2, only those land as `outputs:`.
- **Narrative prose, no anchors.** Author `narrative:` prose at root and per-sub-analysis levels. Do NOT add `astra-anchor:` references — the entries those would point at don't exist yet.
- **Validate before exit.** `astra validate astra.yaml` must return clean.
- **Don't re-ingest.** The experts have already read the paper and code in depth. Query them; don't try to absorb the materials yourself. Your context window is for synthesis, not absorption.

## Step 2 — Self-review (rigor chosen per spawn)

After the stub lands, a fresh-context sub-agent cross-checks it against paper + code: are the sub-analyses the right decomposition? Are the inputs and outputs declared at the sub-analysis level wired correctly? Does the narrative prose accurately describe what each sub-analysis does?

The depth of self-review is set by the rigor level the orchestrator picked when it spawned this `architect` sub-agent — read CLAUDE.md's **Rigor** section for the current state and what the orchestrator flagged as the chosen rigor for this spawn:

- **Cheap:** skip review entirely, or run a single fresh-context reviewer pass and incorporate its fixes once.
- **Heavy:** N rounds — each round spawns a fresh reviewer against `astra.yaml` + the ACQUIRE indices + the experts; the architect sub-agent incorporates fixes; the next round spawns another fresh reviewer that has not seen the fixes. Iterate until two consecutive rounds find no fixes, or a 5-round system cap.

Each round spawns a brand-new sub-agent that does NOT see prior rounds' findings or fixes — pattern-matching on prior fixes defeats the cross-check. Reviewers output findings only; the architect sub-agent edits the stub between rounds (or for trivial mechanical fixes, the orchestrator can do the edit directly).

After self-review terminates, the architect sub-agent updates CLAUDE.md's **Rigor** section with the post-spawn state of `astra.yaml` (e.g. *stub: baseline* after a cheap pass, *stub: tightened* after heavy review).

### Per-round fresh sub-agent — prompt shape

```
You are an ARCHITECT-stub reviewer. Read astra.yaml (the stub) and report
structural inconsistencies. You are one of several independent reviewers;
do not assume anything has already been fixed.

Inputs:
  - astra.yaml — the stub under review. decisions: / prior_insights: /
    findings: are intentionally empty; do NOT flag those as missing.
  - work/reference/index.json — paper structural index
  - work/reference/astra.yaml — paper-extraction's paper-as-ASTRA stub
  - work/reference/code-index.md — code inventory
  - paper-expert agent ID: <id> — SendMessage for paper-side questions
  - code-expert agent ID:  <id> — SendMessage for code-side questions
  - CLAUDE.md — for the Goal section's scope fence

What to check:
  1. Sub-analysis decomposition. Right cuts? Consistent with code-index?
     Defensible against the paper where the paper compresses?
  2. Sub-analysis IDs. Noun phrases. No reserved-name collisions
     (inputs, outputs, decisions, findings, prior_insights, analyses,
      options, content, narrative).
  3. Inputs at sub-analysis level. Each input has a stable id; the data
     dependency is real (cross-check against code-index.md's
     External-data-dependencies and the paper's data section).
  4. Outputs at sub-analysis level. Each output corresponds to a result
     locus from index.json OR an intermediate artifact a downstream
     sub-analysis consumes. Targeted scope from CLAUDE.md's Goal is
     honored — no out-of-scope outputs sneaking in, no in-scope targets
     missed.
  5. Narrative coverage. Root narrative includes a data-flow paragraph
     (when sub-analyses exist). Each sub-analysis's narrative accurately
     describes its role. No astra-anchor: references at this stage; flag
     any that snuck in.
  6. Validates. astra validate astra.yaml returns clean.

What NOT to do:
  - Do not flag empty decisions: / prior_insights: / findings:. That's
    SPECIFY's territory.
  - Do not edit any file. Output findings only.
  - Do not re-read the entire paper or code. Use the indices and ask the
    experts.
  - Do not assume a prior reviewer has been here. You are fresh.

Output: work/notes/architect/review-round-<N>.md (findings + verdict).
```

### Termination

- **Cheap:** one pass. Done after fixes (or immediately, if `fixes_needed` was 0).
- **Heavy:**
  - Round N's `fixes_needed` was 0 AND round (N-1)'s was also 0 → done.
  - First round (N=1): spawn round 2 unconditionally so we can compare.
  - Round N produced fixes: spawn round (N+1) as a fresh sub-agent that does not see round N's findings or fixes.
  - 5-round cap without two consecutive clean rounds: stop, report back to orchestrator. If user is reachable, ask in prose: "ARCHITECT review reached round cap with N fixes still landing; continue, accept the current stub, or revise scope?" If unreachable, accept the current stub, log the unfinished tail in `open-questions.md`, and let the orchestrator decide whether to proceed to SPECIFY or re-spawn ARCHITECT later.

## Survey signals (entry into ARCHITECT)

- `work/reference/index.json` + `work/reference/astra.yaml` + `work/reference/code-index.md` (when code present) exist ⇒ ACQUIRE indices are ready
- paper-expert and code-expert agent IDs received from the orchestrator ⇒ experts are reachable
- `astra.yaml` exists at project root; `astra validate astra.yaml` returns clean; sub-analyses + inputs + outputs + narrative populated; `decisions:` / `prior_insights:` / `findings:` blocks present-and-empty ⇒ stub written
- For cheap: `work/notes/architect/review-round-1.md` with verdict `clean` (or no fixes were incorporated) ⇒ ARCHITECT done
- For heavy: two consecutive `work/notes/architect/review-round-<N>.md` files both with verdict `clean` ⇒ ARCHITECT done; orchestrator proceeds to SPECIFY

## Notes

- **Experts replace re-ingestion.** ACQUIRE's paper-expert and code-expert are alive with deep context. ARCHITECT does not spawn its own Explore sub-agents; it queries the experts. This keeps the architect sub-agent's context lean.
- **The stub's empty blocks are intentional.** `decisions: {}`, `prior_insights: {}`, `findings: {}` make it clear at a glance that ARCHITECT's job is structural and SPECIFY fills them. Don't try to half-author content — empty is honest.
- **Code-as-canonical for structure, paper-as-canonical for narrative voice.** The code reveals where the real stage boundaries are; the paper provides the words to describe them. The stub uses both.
- **Resume is automatic.** If `astra.yaml` already validates and has the structural fields populated, on re-spawn the architect sub-agent skips Step 1 and runs Step 2 (review) only.
- **The narrative skill is the prose author, not the structure author.** Invoke `/narrative` for the prose blocks; ARCHITECT's job is the structural skeleton plus invoking `/narrative` to fill the `narrative:` keys cleanly.
- **Commit each artifact as it lands.** The orchestrator reads `git log` to see how far the architect sub-agent got. Stub commits before any review-round files; review-round files commit one per round. Small, descriptive commits keep the trail readable.
