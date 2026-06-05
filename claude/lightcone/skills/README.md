# lightcone-cli skills

Each subdirectory is one Claude Code skill: `SKILL.md` plus optional `references/`, `assets/`, and `scripts/`. `lc init` copies these into a project's `.claude/skills/` so they are discoverable to Claude Code sessions.

## Project lifecycle skills

| Skill | Role |
|---|---|
| `lc-new` | Scaffold a new ASTRA-shaped project from a research question. |
| `lc-from-code` | Bring an existing codebase into ASTRA — scan, spec, parameterize. |
| `lc-from-paper` | Reproduce a published paper in ASTRA (paper-reproduction bundle entry point — see below). |
| `lc-feedback` | Report bugs and feature requests upstream. |
| `ralph` | Author a constitution and run a ralph loop against it (authoring + launching + iterating in one skill). The substrate for open-ended long-running work. (No longer used by `lc-from-paper`, which now drives its middle with the reproduce-paper Workflow.) |

## Reference skills

Not direct entry points — Claude invokes these (or other skills invoke them) to load reference content into the session. The session-start hook primes their names so they're discoverable from turn one.

| Skill | Role |
|---|---|
| `astra` | Reference for the `astra.yaml` spec: structure, decisions, options, prior insights, findings, evidence, sub-analyses, narrative anchors, composition mechanics. |
| `lc-cli` | Reference for `lc` workflow: commands, the Spec-Code Invariant, status interpretation, failure diagnosis, multiverse runs, WRROC export. |

## Paper-reproduction bundle

A self-contained toolkit for reproducing published papers in ASTRA. The bundle is co-located so a single `lc init` brings the full toolkit into a project — no plugin marketplace, no separate installs.

| Skill | Role |
|---|---|
| [`lc-from-paper`](lc-from-paper/SKILL.md) | **Reproduction driver.** Two interactive bookends in the user's main session wrapping one autonomous Workflow. **ORIENT → PLAN**: ask for the paper, run `/paper-extraction` inline, interview the user (grounded — the fidelity-intent answer is the workflow's *stopping criterion*), run `/lc-from-code` scan-only (when a repo exists), architect the decomposition, and draft the reproduction `PLAN.md` + `astra.yaml` skeleton + `targets/targets.md` + `CLAUDE.md` — gated through **plan mode**. On approval, launch [`reproduce_workflow.js`](lc-from-paper/reproduce_workflow.js): SPECIFY ∥ LITERATURE (pipeline) → IMPLEMENT (parallel per output) → RUN → **VERIFY** (a generated test per claim; run → fix → rerun, bounded by the fidelity intent) → REVIEW (`report.html` + summary back). Then **CLOSE-OUT** runs in the main session (figure-comparison, sentence-by-sentence, open-questions). |
| [`narrative`](narrative/SKILL.md) | Author the `narrative:` prose and decision `rationale:` in `astra.yaml`. Invoked by `lc-from-paper`'s ARCHITECT step (for the structural narrative, while building the plan) and the workflow's SPECIFY phase (for anchored content narrative). |
| [`paper-extraction`](paper-extraction/SKILL.md) | Turn an arXiv ID or DOI into a standardized `work/reference/` directory: structural index (figures, tables, outline, citations with resolved DOIs) plus a stub `astra.yaml` for the paper. Primary acquisition path for `lc-from-paper`'s ORIENT (Stage 2); also invoked per cited paper by LITERATURE. |
| [`check-sentence-by-sentence`](check-sentence-by-sentence/SKILL.md) | Audit paper claims against code locations (`file:line` or `NOT FOUND`). Invoked from `lc-from-paper`'s REVIEW close-out (opt-in); also user-invokable directly. |
| [`figure-comparison`](figure-comparison/SKILL.md) | Build a self-contained HTML side-by-side: original figures/tables/numerics vs replicated. Invoked from `lc-from-paper`'s REVIEW close-out (mandatory); also user-invokable directly. |

The full reproduction story spans these skills. `lc-from-paper`'s `SKILL.md` names each by role and tells the agent when to invoke them; the siblings stand alone and don't know about `lc-from-paper`.

### Why bundle (not depend on plugin install)

- **Testability.** We want to verify `lc-from-paper` invokes its sibling skills correctly. That only works when all are in the same checkout.
- **Single install path.** `lc init` brings the full toolkit. Adding a separate plugin-marketplace step is friction we don't need.
- **Future consolidation is open.** The long-run shape may be `astra` ships skills in `astra`, `lc` ships skills in `lightcone-cli`, plus a centralized external-skills list. Today: bundle it all. See [[lightcone/skills-location-policy]].
