# lightcone-cli skills

Each subdirectory is one Claude Code skill: `SKILL.md` plus optional `references/`, `assets/`, and `scripts/`. `lc init` copies these into a project's `.claude/skills/` so they are discoverable to Claude Code sessions.

## Project lifecycle skills

| Skill | Role |
|---|---|
| `lc-from-question` | Scaffold a new ASTRA-shaped project from a research question. |
| `lc-from-code` | Bring an existing codebase into ASTRA — scan, spec, parameterize. |
| `lc-from-paper` | Reproduce a published paper in ASTRA (paper-reproduction bundle entry point — see below). |
| `lc-build` | Build container images and dependencies for a project. |
| `lc-verify` | Run validation across an ASTRA project. |
| `lc-feedback` | Report bugs and feature requests upstream. |

## Paper-reproduction bundle

A self-contained toolkit for reproducing published papers in ASTRA. The bundle is co-located so a single `lc init` brings the full toolkit into a project — no plugin marketplace, no separate installs.

| Skill | Role |
|---|---|
| [`lc-from-paper`](lc-from-paper/SKILL.md) | **Orchestrator.** Interview-first; drafts a per-paper reproduction constitution and per-paper `CLAUDE.md`, then launches one of three runtime modes (interactive, bash-loop, tmux-orchestrated) against the constitution. The constitution carries 9 phases — INTERVIEW → ACQUIRE → ARCHITECT → SPECIFY → LITERATURE → IMPLEMENT → RUN → COMPARE → REVIEW — bookended by two always-interactive seams (INTERVIEW at start, REVIEW at close-out); every other phase is configurable per the user's per-phase mode choice, with ARCHITECT, SPECIFY, LITERATURE, and IMPLEMENT additionally tuned by a frugality / rigor dial that drives each phase's internal fresh-context self-review. |
| [`narrative`](narrative/SKILL.md) | Author the `narrative:` prose and decision `rationale:` in `astra.yaml`. Invoked by lc-from-paper during SPECIFY. |
| [`constitution`](constitution/SKILL.md) | Draft a constitution — a markdown spec for an iteration runner. Invoked by lc-from-paper during the interview. |
| [`ralph-loops`](ralph-loops/SKILL.md) | Drive an autonomous iteration loop. Includes `scripts/ralph` runner. Used by lc-from-paper's bash-loop and tmux-orchestrated runtime modes. |
| [`paper-extraction`](paper-extraction/SKILL.md) | Turn an arXiv ID or DOI into a standardized `work/reference/` directory: structural index (figures, tables, outline, citations) plus a stub `astra.yaml` for the paper. Primary acquisition path for lc-from-paper's ACQUIRE phase. |
| [`check-sentence-by-sentence`](check-sentence-by-sentence/SKILL.md) | Audit paper claims against code locations (`file:line` or `NOT FOUND`). Invoked from lc-from-paper's REVIEW close-out (opt-in); also user-invokable directly. |
| [`figure-comparison`](figure-comparison/SKILL.md) | Build a self-contained HTML side-by-side: original figures/tables/numerics vs replicated. Invoked from lc-from-paper's REVIEW close-out (mandatory); also user-invokable directly. |

The full reproduction story spans these seven skills. lc-from-paper's `SKILL.md` names each by role and tells the agent when to invoke them; the siblings stand alone and don't know about lc-from-paper.

### Why bundle (not depend on plugin install)

- **Testability.** We want to verify lc-from-paper invokes constitution + ralph-loops + the others correctly. That only works when all are in the same checkout.
- **Single install path.** `lc init` brings the full toolkit. Adding a separate plugin-marketplace step is friction we don't need.
- **Future consolidation is open.** The long-run shape may be `astra` ships skills in `astra`, `lc` ships skills in `lightcone-cli`, plus a centralized external-skills list. Today: bundle it all. See [[lightcone/skills-location-policy]].
