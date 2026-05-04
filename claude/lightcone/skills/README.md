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

| Skill | Role | Origin |
|---|---|---|
| [`paper2astra`](paper2astra/SKILL.md) | **Orchestrator.** Interview-first; drafts a per-paper reproduction constitution and launches a ralph loop against it. | New for the bundle. |
| [`narrative`](narrative/SKILL.md) | Author the `narrative:` prose and decision `rationale:` in `astra.yaml`. Invoked by paper2astra during SPECIFY. | Cail's ([lightcone-cli#86](https://github.com/LightconeResearch/lightcone-cli/pull/86), ported from lightcone-ui#10). |
| [`constitution`](constitution/SKILL.md) | Draft a constitution — a markdown spec for an iteration runner. Invoked by paper2astra during the interview. | Merged from [`cailmdaley/skills/skills/constitution`](https://github.com/cailmdaley/skills/tree/main/skills/constitution) (procedural backbone) + Cail's personal felt references (taste — two diamonds, six stances, funnel ledger, qualitative self-check), with felt-optional framing. |
| [`ralph-loops`](ralph-loops/SKILL.md) | Drive an autonomous iteration loop. Includes `scripts/ralph` runner. Launched by paper2astra after the interview. | Direct copy from [`cailmdaley/skills/skills/ralph-loops`](https://github.com/cailmdaley/skills/tree/main/skills/ralph-loops). |
| [`managing-bibliography`](managing-bibliography/SKILL.md) | Read arXiv LaTeX source; manage BibTeX via ADS API. Primary acquisition path for paper2astra's ACQUIRE phase. | Direct copy of Cail's personal `~/.claude/skills/managing-bibliography` (newer than the public version). |
| [`check-sentence-by-sentence`](check-sentence-by-sentence/SKILL.md) | Complementary paper-vs-code source audit via sub-agents; locates `file:line` or `NOT FOUND`. | Copy of Nolan's. |
| [`figure-comparison`](figure-comparison/SKILL.md) | Generates a HTML side-by-side report: original figures/tables/numerics vs replicated. Useful for manual review. | Copy of Nolan's. |

The full reproduction story spans these seven skills. paper2astra's `SKILL.md` names each by role and tells the agent when to invoke them; the siblings stand alone and don't know about paper2astra.

### Why bundle (not depend on plugin install)

- **Testability.** We want to verify paper2astra invokes constitution + ralph-loops + the others correctly. That only works if all are in the same checkout.
- **Single install path.** `lc init` is the install path for lightcone-cli skills. Adding a separate "also install Cail's public skills via plugin marketplace" step is friction we don't need.
- **Copy-with-credit costs nothing.** The copied skills retain attribution to their original authors in the SKILL body; if those skills update upstream, we re-sync.
- **Future consolidation is open.** Per Francois's "next week we improve" framing, the long-run shape might be `astra` ships skills in `astra`, `lc` ships skills in `lightcone-cli`, plus a centralized external-skills list. Today: bundle it all.
