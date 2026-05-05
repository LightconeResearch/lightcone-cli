# lightcone-cli skills

Each subdirectory is one Claude Code skill: `SKILL.md` plus optional `references/`, `assets/`, and `scripts/`. `lc init` copies these into a project's `.claude/skills/` so they are discoverable to Claude Code sessions.

## Project lifecycle skills

| Skill | Role |
|---|---|
| `lc-new` | Scaffold a new ASTRA-shaped project from scratch. |
| `lc-build` | Build container images and dependencies for a project. |
| `lc-verify` | Run validation across an ASTRA project. |
| `lc-migrate` | Migrate legacy projects to current conventions. |
| `lc-feedback` | Report bugs and feature requests upstream. |

## Paper-reproduction bundle

A self-contained toolkit for reproducing published papers in ASTRA. The bundle is co-located so a single `lc init` brings the full toolkit into a project — no plugin marketplace, no separate installs.

| Skill | Role |
|---|---|
| [`paper2astra`](paper2astra/SKILL.md) | **Orchestrator.** Interview-first; drafts a per-paper reproduction constitution and per-paper `CLAUDE.md`, then launches one of three runtime modes (interactive, bash-loop, tmux-orchestrated) against the constitution. |
| [`narrative`](narrative/SKILL.md) | Author the `narrative:` prose and decision `rationale:` in `astra.yaml`. Invoked by paper2astra during SPECIFY. |
| [`constitution`](constitution/SKILL.md) | Draft a constitution — a markdown spec for an iteration runner. Invoked by paper2astra during the interview. |
| [`ralph-loops`](ralph-loops/SKILL.md) | Drive an autonomous iteration loop. Includes `scripts/ralph` runner. Used by paper2astra's bash-loop and tmux-orchestrated runtime modes. |
| [`managing-bibliography`](managing-bibliography/SKILL.md) | Read arXiv LaTeX source; manage BibTeX via ADS API. Primary acquisition path for paper2astra's ACQUIRE phase. |
| `figure-comparison` | HTML side-by-side: original figures/tables/numerics vs replicated. **Auto-invoked** by paper2astra as a sub-agent at the end of SUMMARIZE_RUN. *(pending bundle integration)* |
| `check-sentence-by-sentence` | Paper-vs-code TeX audit via sub-agents; locates `file:line` or `NOT FOUND`. **Opt-in** suggestion to the user after SUMMARIZE_RUN — token-expensive, never auto-invoked. *(pending bundle integration)* |

The full reproduction story spans these seven skills. paper2astra's `SKILL.md` names each by role and tells the agent when to invoke them; the siblings stand alone and don't know about paper2astra.

### Why bundle (not depend on plugin install)

- **Testability.** We want to verify paper2astra invokes constitution + ralph-loops + the others correctly. That only works when all are in the same checkout.
- **Single install path.** `lc init` brings the full toolkit. Adding a separate plugin-marketplace step is friction we don't need.
- **Future consolidation is open.** The long-run shape may be `astra` ships skills in `astra`, `lc` ships skills in `lightcone-cli`, plus a centralized external-skills list. Today: bundle it all. See [[lightcone/skills-location-policy]].
