# lightcone-cli skills

Each subdirectory is one Claude Code skill: `SKILL.md` plus optional `references/`, `assets/`, and `scripts/`. These ship as the `lightcone` Claude Code plugin (manifest at `claude/lightcone/.claude-plugin/plugin.json`). `lc init` shells out to `claude plugin marketplace add` + `claude plugin install lightcone@lightcone-cli` so the skills register user-scoped — discoverable in every Claude Code session, not duplicated into each project's `.claude/`.

## Entry point

| Skill | Role |
|---|---|
| `lightcone` | **Start here.** The `/lightcone` skill carries the lay of the land (what ASTRA is, the Spec-Code Invariant, the core `lc` loop), bundles the two references you reach for in almost every session at `references/astra.md` (the `astra.yaml` spec) and `references/lc-cli.md` (the `lc` workflow), and routes you to the task skill for whatever you're doing. The PreToolUse skill-gate opens once it's activated, so invoke it first whenever an `astra.yaml` project is in play. |

## Project lifecycle skills

| Skill | Role |
|---|---|
| `lc-new` | Scaffold a new ASTRA-shaped project from a research question. |
| `lc-from-code` | Bring an existing codebase into ASTRA — scan, spec, parameterize. |
| `lc-from-paper` | Reproduce a published paper in ASTRA (paper-reproduction bundle entry point — see below). |
| `lc-feedback` | Report bugs and feature requests upstream. |

## References (bundled under `/lightcone`)

Not skills — these are reference documents bundled with the `/lightcone` entry point at `lightcone/references/`. Activate `/lightcone` and read the section you need; the references stay available throughout the session.

| Reference | Role |
|---|---|
| `references/astra.md` | Reference for the `astra.yaml` spec: structure, decisions, options, prior insights, findings, evidence, sub-analyses, narrative anchors, composition mechanics. |
| `references/lc-cli.md` | Reference for `lc` workflow: commands, the Spec-Code Invariant, status interpretation, failure diagnosis, multiverse runs, WRROC export. |

## Paper-reproduction bundle

A self-contained toolkit for reproducing published papers in ASTRA. The bundle is co-located inside the `lightcone` plugin so a single `lc init` brings the full toolkit — one marketplace registration, one plugin install, all skills available.

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
