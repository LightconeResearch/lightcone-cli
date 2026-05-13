# lightcone-cli skills

Each subdirectory is one Claude Code skill: `SKILL.md` plus optional `references/`, `assets/`, and `scripts/`. `lc init` copies these into a project's `.claude/skills/` so they are discoverable to Claude Code sessions.

## Project lifecycle skills

| Skill | Role |
|---|---|
| `lc-new` | Scaffold a new ASTRA-shaped project from a research question. |
| `lc-from-code` | Bring an existing codebase into ASTRA — scan, spec, parameterize. |
| `lc-from-paper` | Reproduce a published paper in ASTRA (paper-reproduction bundle entry point — see below). |
| `lc-feedback` | Report bugs and feature requests upstream. |
| `ralph` | Author a constitution and run a ralph loop against it (authoring + launching + iterating in one skill). `lc-from-paper` uses this for the long middle of a reproduction; standalone for any other long-running work. |

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
| [`lc-from-paper`](lc-from-paper/SKILL.md) | **Reproduction driver.** ORIENT-first; one pre-loop phase in the user's main session that asks for the paper, runs `/paper-extraction` inline, interviews the user (grounded in the paper), clones the reference code and runs `/lc-from-code` scan-only (when a repo exists), and drafts the per-paper `constitution.md` + `CLAUDE.md`. Then hands off to a ralph loop whose iterations carry the long middle: ARCHITECT → SPECIFY → LITERATURE → IMPLEMENT → RUN → COMPARE. When the loop closes (constitution `status: closed` after COMPARE returns `pass`), REVIEW runs back in the user's main session. Fidelity intent — captured as prose at ORIENT — is what every iteration reads when sizing its next move, and what COMPARE grades opportunities against. |
| [`ralph`](ralph/SKILL.md) | The loop substrate. `lc-from-paper`'s ORIENT invokes `/ralph`'s Authoring mode to draft the per-paper constitution; the loop launcher hands off after ORIENT lands. Each iteration runs `/ralph`'s Loop protocol against the constitution. |
| [`narrative`](narrative/SKILL.md) | Author the `narrative:` prose and decision `rationale:` in `astra.yaml`. Invoked by `lc-from-paper`'s ARCHITECT (for the structural narrative) and SPECIFY (for anchored content narrative). |
| [`paper-extraction`](paper-extraction/SKILL.md) | Turn an arXiv ID or DOI into a standardized `work/reference/` directory: structural index (figures, tables, outline, citations with resolved DOIs) plus a stub `astra.yaml` for the paper. Primary acquisition path for `lc-from-paper`'s ORIENT (Stage 2); also invoked per cited paper by LITERATURE. |
| [`check-sentence-by-sentence`](check-sentence-by-sentence/SKILL.md) | Audit paper claims against code locations (`file:line` or `NOT FOUND`). Invoked from `lc-from-paper`'s REVIEW close-out (opt-in); also user-invokable directly. |
| [`figure-comparison`](figure-comparison/SKILL.md) | Build a self-contained HTML side-by-side: original figures/tables/numerics vs replicated. Invoked from `lc-from-paper`'s REVIEW close-out (mandatory); also user-invokable directly. |

The full reproduction story spans these skills. `lc-from-paper`'s `SKILL.md` names each by role and tells the agent when to invoke them; the siblings stand alone and don't know about `lc-from-paper`.

### Why bundle (not depend on plugin install)

- **Testability.** We want to verify `lc-from-paper` invokes its sibling skills correctly. That only works when all are in the same checkout.
- **Single install path.** `lc init` brings the full toolkit. Adding a separate plugin-marketplace step is friction we don't need.
- **Future consolidation is open.** The long-run shape may be `astra` ships skills in `astra`, `lc` ships skills in `lightcone-cli`, plus a centralized external-skills list. Today: bundle it all. See [[lightcone/skills-location-policy]].
