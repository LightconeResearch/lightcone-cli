# ARCHITECT — write the stub `astra.yaml`

ARCHITECT is the structural seam: decide the sub-analysis decomposition, wire the inputs and outputs at the sub-analysis level, and author high-level narrative prose for each analysis — all in one stub `astra.yaml`. SPECIFY then fills the stub with `decisions:`, `prior_insights:`, `findings:`, and `astra-anchor:` references. Splitting **structure** from **content** keeps each iteration's cognitive load manageable: ARCHITECT decides *what the analyses are*; SPECIFY decides *what's inside each one*.

ARCHITECT is what a ralph iteration does when the workdir signals "ORIENT substrate present + project-root `astra.yaml` absent (or empty stub)." The heavy work of *understanding* the paper and code happened in `/paper-extraction` and `/lc-from-code`'s scan-only branch — both invoked inline during ORIENT in the user's main session. Their on-disk substrate (the structural `index.json`, the paper-extraction `astra.yaml`, the `code-index.md`) is what you read on entry. No persistent expert sub-agents; targeted reads against the substrate carry the orientation.

## Inputs

- `constitution.md` — Goal, Fidelity intent, Scope, Quality bar. Read first; the Goal's intended replication targets fence what `outputs:` belong in the stub.
- `CLAUDE.md` — auto-loaded; Rules + accumulators (still empty at this point).
- `work/reference/index.json` — paper-side structural index from `/paper-extraction` (figures, tables, section outline with line numbers, citations with resolved DOIs).
- `work/reference/astra.yaml` — paper-extraction's ASTRA-shape stub of the paper itself: id, name, `narrative.summary` (from abstract), optionally `findings:` (paper's claimed numerical results).
- `work/reference/code-index.md` — code-side inventory from `/lc-from-code`'s scan: script inventory, candidate decisions with `file:line` refs, module map, entry-points, external data dependencies, container hints.
- `work/reference/source/` (Path A) or `work/reference/document.md` (Path B) — paper text. Grep into for specific facts; do not re-read whole.
- `work/reference/code/` (when present) — the cloned reference code. Read targeted modules when `code-index.md` doesn't answer a structural question.
- `work/notes/notes.md` — user-supplied prior notes, if any.

## Outputs

- `astra.yaml` at the project root — **stub form**: sub-analyses named, architecture wired (inputs / outputs declared at the sub-analysis level), high-level `narrative:` prose blocks per analysis. **No `decisions:`, `prior_insights:`, `findings:`, or `astra-anchor:` references yet** — those entries don't exist for the narrative to reference.
- `constitution.md` updates: Open dimensions, when something material surfaces that warrants user ratification at REVIEW.

## Step 1 — Read the substrate, then write the stub

Read `constitution.md`, `CLAUDE.md`, `work/reference/index.json`, `work/reference/code-index.md`, and the paper-extraction `astra.yaml` first. Then for anything the indices don't answer, Grep into `work/reference/source/` (Path A) or `document.md` (Path B), or read targeted modules in `work/reference/code/`. Don't try to absorb the paper or code whole; the indices give you the orientation, and targeted reads fill in specifics.

### What to do

1. **Reconcile sub-analysis decompositions.** Read `code-index.md`'s natural-decomposition section and `index.json`'s section outline. Where paper and code agree on a stage, use that name (noun-phrase, e.g. `reconstruction`). Where they disagree, **code's structure is canonical for stage boundaries** — the paper compresses; the code reveals the actual decomposition. Where code is absent or thin, follow the paper alone. Where module boundaries are genuinely ambiguous, read the relevant modules under `work/reference/code/` to settle it.
2. **Choose: one analysis or sub-analyses?** If the paper has only one stage end-to-end (no clean intermediate handoffs), write a single analysis. If it has genuinely independent stages (each stage's output flows as the next's input), write sub-analyses. Sub-analysis IDs must be noun phrases: `reconstruction`, `clustering`, `bao_fit`. Avoid reserved names: `inputs`, `outputs`, `decisions`, `findings`, `prior_insights`, `analyses`, `options`, `content`, `narrative`.
3. **Wire inputs and outputs at the sub-analysis level.** For each sub-analysis:
   - Declare `inputs:` from `code-index.md`'s External-data-dependencies plus any paper-named external datasets. The depth (acquisition path, selection criteria) is SPECIFY's; ARCHITECT names the input and gives it a stable id.
   - Declare `outputs:` matching the result loci from `index.json` (figures + tables) plus any intermediate artifacts a downstream sub-analysis consumes. Tag each output's `priority:` from the paper's emphasis (primary / secondary). **The reproduction's targeted scope from `constitution.md`'s Scope takes precedence** — if the user only wants Figure 3 and Table 2, only those land as `outputs:`; the rest are out-of-scope and noted as such.
4. **Author the root and per-analysis narrative.** Invoke `/narrative` for prose authoring (it carries the discipline on reserved names, voice, the data-flow paragraph requirement). High-level prose only — **no `astra-anchor:` references yet**, because the entries those would point at don't exist. SPECIFY will weave in anchors as it authors `decisions:` / `prior_insights:` / `findings:` per sub-analysis. The root `narrative:` MUST include a top-down end-to-end data-flow paragraph (per the narrative skill's data-flow rules) when sub-analyses exist.
5. **Validate.** `astra validate astra.yaml` must return clean — even with empty `decisions:` / `prior_insights:` / `findings:` blocks, the structural fields and narrative prose must pass schema checks.

### Stub shape — what `astra.yaml` looks like after ARCHITECT

```yaml
# Stub: structure + narrative. SPECIFY fills decisions/findings/prior_insights and weaves astra-anchor references into the narrative.
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
    prior_insights: {} # SPECIFY records placeholders (Evidence with doi:, no quote: yet), LITERATURE fills the quote: selectors
    findings: {}       # SPECIFY fills

  <sub-analysis-id-2>:
    ...
```

### Rules for Step 1

- **Stub, not snapshot.** Don't try to author content for `decisions:`, `prior_insights:`, `findings:`. Those go in SPECIFY. Your job is the structural skeleton.
- **Reserved names.** Sub-analysis IDs are noun phrases; avoid the reserved set. Each ID must be unique across the spec.
- **Code-as-canonical for structure.** Where paper and code disagree on the decomposition, the code's structure is canonical (the paper compresses for narrative; the code reveals real seams).
- **Targeted scope wins.** `constitution.md`'s Scope fences the reproduction. If the user only wants Figures 3–4 plus Table 2, only those land as `outputs:`.
- **Narrative prose, no anchors.** Author `narrative:` prose at root and per-sub-analysis levels. Do NOT add `astra-anchor:` references — the entries those would point at don't exist yet.
- **Validate before exit.** `astra validate astra.yaml` must return clean.
- **Targeted reads, not whole-paper absorption.** The indices give you most of what you need; reach into the source / document / code for specific items, not as a default.

After the stub is written and validates, commit it (`architect: stub astra.yaml`) and exit.

## Reviewing prior ARCHITECT work as part of survey

There is no separate review phase. Every iteration that enters and finds an ARCHITECT stub on disk reads it critically before doing anything else. If you see real issues — wrong sub-analysis decomposition, reserved-name collision, missing in-scope output, narrative gap — fix them inline, commit (`architect: fix <what>`), and exit. Only when a fresh-context read finds nothing to fix does the iteration move on to SPECIFY work. The fresh-context property at iteration boundaries makes the next iteration the review; nothing else is needed.

What to look at:

1. **Sub-analysis decomposition.** Right cuts? Consistent with `code-index.md`? Defensible against the paper where the paper compresses?
2. **Sub-analysis IDs.** Noun phrases. No reserved-name collisions (`inputs`, `outputs`, `decisions`, `findings`, `prior_insights`, `analyses`, `options`, `content`, `narrative`).
3. **Inputs at sub-analysis level.** Each input has a stable id; the data dependency is real (cross-check against `code-index.md`'s External-data-dependencies and the paper's data section).
4. **Outputs at sub-analysis level.** Each output corresponds to a result locus from `index.json` OR an intermediate artifact a downstream sub-analysis consumes. Targeted scope from `constitution.md`'s Scope is honored — no out-of-scope outputs sneaking in, no in-scope targets missed.
5. **Narrative coverage.** Root narrative includes a data-flow paragraph (when sub-analyses exist). Each sub-analysis's narrative accurately describes its role. No `astra-anchor:` references at this stage.
6. **Validates.** `astra validate astra.yaml` returns clean.

Don't flag empty `decisions:` / `prior_insights:` / `findings:` — that's SPECIFY's territory. Don't re-read the entire paper or code; use the indices and targeted reads. If you see the same artifact getting churned across many recent commits without convergence, log the situation to `open-questions.md` and advance the phase anyway.

## Survey signals (entry into ARCHITECT)

- `work/reference/index.json` + `work/reference/astra.yaml` + `work/reference/code-index.md` (when code present) exist ⇒ ORIENT substrate is ready
- `astra.yaml` at project root absent (or present-but-empty) ⇒ this iteration writes the stub
- `astra.yaml` exists with stub form (sub-analyses + inputs + outputs + narrative populated; `decisions:` / `prior_insights:` / `findings:` blocks present-and-empty) ⇒ ARCHITECT's output is on disk; read it critically. Fix anything wrong; otherwise the iteration moves on to SPECIFY.

## Notes

- **No persistent expert sub-agents.** The on-disk substrate (`index.json`, `code-index.md`, the paper-extraction `astra.yaml`) carries the orientation iterations need; re-read what you need on entry.
- **The stub's empty blocks are intentional.** `decisions: {}`, `prior_insights: {}`, `findings: {}` make it clear at a glance that ARCHITECT's job is structural and SPECIFY fills them. Don't try to half-author content — empty is honest.
- **Code-as-canonical for structure, paper-as-canonical for narrative voice.** The code reveals where the real stage boundaries are; the paper provides the words to describe them. The stub uses both.
- **The narrative skill is the prose author, not the structure author.** Invoke `/narrative` for the prose blocks; ARCHITECT's job is the structural skeleton plus invoking `/narrative` to fill the `narrative:` keys cleanly.
- **Commit each artifact as it lands.** The stub commits when it lands; each subsequent fix pass commits separately. Small, descriptive commits keep `git log` legible to the next iteration.
