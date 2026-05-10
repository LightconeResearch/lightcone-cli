# lightcone-cli skills

Each subdirectory is one Claude Code skill: `SKILL.md` plus optional `references/`, `assets/`, and `scripts/`. `lc init` copies these into a project's `.claude/skills/` so they are discoverable to Claude Code sessions.

## Project lifecycle skills

| Skill | Role |
|---|---|
| `lc-new` | Scaffold a new ASTRA-shaped project from a research question. |
| `lc-from-code` | Bring an existing codebase into ASTRA — scan, spec, parameterize. |
| `lc-from-paper` | Reproduce a published paper in ASTRA (paper-reproduction bundle entry point — see below). |
| `lc-feedback` | Report bugs and feature requests upstream. |

## Paper-reproduction bundle

A self-contained toolkit for reproducing published papers in ASTRA. The bundle is co-located so a single `lc init` brings the full toolkit into a project — no plugin marketplace, no separate installs.

| Skill | Role |
|---|---|
| [`lc-from-paper`](lc-from-paper/SKILL.md) | **Orchestrator.** Interview-first; drafts a per-paper `CLAUDE.md`, then runs as a persistent orchestrator session that spawns named per-phase sub-agents the user can drop into directly. Nine phases — INTERVIEW → ACQUIRE → ARCHITECT → SPECIFY → LITERATURE → IMPLEMENT → RUN → COMPARE → REVIEW — bookended by INTERVIEW and REVIEW running in the orchestrator session itself; the seven phases between are sub-agent dispatches. Rigor is chosen per spawn from CLAUDE.md's Rigor section, not as a global dial. |
| [`narrative`](narrative/SKILL.md) | Author the `narrative:` prose and decision `rationale:` in `astra.yaml`. Invoked by lc-from-paper during SPECIFY. |
| [`paper-extraction`](paper-extraction/SKILL.md) | Turn an arXiv ID or DOI into a standardized `work/reference/` directory: structural index (figures, tables, outline, citations) plus a stub `astra.yaml` for the paper. Primary acquisition path for lc-from-paper's ACQUIRE phase. |
| [`check-sentence-by-sentence`](check-sentence-by-sentence/SKILL.md) | Audit paper claims against code locations (`file:line` or `NOT FOUND`). Invoked from lc-from-paper's REVIEW close-out (opt-in); also user-invokable directly. |
| [`figure-comparison`](figure-comparison/SKILL.md) | Build a self-contained HTML side-by-side: original figures/tables/numerics vs replicated. Invoked from lc-from-paper's REVIEW close-out (mandatory); also user-invokable directly. |

The full reproduction story spans these five skills. lc-from-paper's `SKILL.md` names each by role and tells the agent when to invoke them; the siblings stand alone and don't know about lc-from-paper.

### Why bundle (not depend on plugin install)

- **Testability.** We want to verify lc-from-paper invokes its sibling skills correctly. That only works when all are in the same checkout.
- **Single install path.** `lc init` brings the full toolkit. Adding a separate plugin-marketplace step is friction we don't need.
- **Future consolidation is open.** The long-run shape may be `astra` ships skills in `astra`, `lc` ships skills in `lightcone-cli`, plus a centralized external-skills list. Today: bundle it all. See [[lightcone/skills-location-policy]].
