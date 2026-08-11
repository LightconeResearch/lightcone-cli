# Design Spec: Harness-Agnostic Skills Repository

**Status:** Draft / RFC
**Date:** 2026-06-13
**Owners:** lightcone-cli maintainers
**Supersedes / relates to:** PR #76 (LCR-85 multi-harness), PR #148 (harness-agnostic plugin layer), PR #130 (Claude plugin repackage)

---

## 1. Summary

Move the Claude skills, subagents, and hooks currently vendored inside the
`lightcone-cli` wheel out into a standalone **`lightconeresearch/skills`**
repository, restructured so a single source of truth can serve multiple AI
coding harnesses — Claude Code, OpenAI Codex, OpenCode, and Pi — through each
harness's native install mechanism.

The repository is organized in two layers:

1. A **canonical skills layer** (`SKILL.md` files) that is genuinely portable
   across all four harnesses via the open Agent Skills standard.
2. A **thin per-harness adapter layer** for the things that are *not* portable —
   subagent definitions and hook wiring — plus per-harness packaging manifests.

Executable code that today hides inside skills (notably
`paper-extraction`'s ~1,475-line Python script and the `ralph` loop runner)
leaves the skills repo entirely and ships as versioned console scripts from a
Python package (`lightcone-cli[paper]`). Skills invoke those scripts; they do
not vendor them.

`lc init` shrinks to a harness detector that shells out to the correct native
installer and writes the per-project activation.

## 2. Motivation

### 2.1 Problems with the status quo

- **Skill iteration is coupled to CLI releases.** Skills ship via
  `force-include` inside the `lightcone-cli` wheel. A one-word fix to a skill
  requires cutting a CLI version.
- **Core and optional functionality are entangled.** The paper-reproduction
  bundle (`lc-from-paper`, `paper-extraction`, `figure-comparison`,
  `check-sentence-by-sentence`, plus `narrative` and `ralph`) is not core
  lightcone-cli functionality, yet it ships in the same unit as `astra`,
  `lc-cli`, and `lc-new`.
- **Executable code masquerades as skill content.**
  `paper-extraction/scripts/extract-paper-substrate.py` is a tool wearing a
  skill's clothes. It is not testable or versioned as a real dependency, and no
  cross-harness skill installer (`npx skills`) will carry it.
- **The multi-harness PRs reinvent distribution.** PR #76 hand-codes a
  per-tool path registry (`.claude/skills`, `.codex/`, `.cursor/commands`,
  Copilot `.prompt.md`, …). The `npx skills` ecosystem already solves
  "install a `SKILL.md` into 70+ harnesses at the right path and scope."

### 2.2 Goals

- One canonical copy of each skill, consumable by every supported harness.
- Clean separation of **core** vs **optional** bundles, activatable per project.
- Remove vendored executable code from skills; make it a real package.
- Reduce `lc init` to thin orchestration over native installers.
- Keep the door open for harnesses we do not yet support, one per addition.

### 2.3 Non-goals

- Building our own skill registry or marketplace server. We rely on Git-backed
  marketplaces and the `npx skills` resolver.
- Achieving feature parity for subagents/hooks on harnesses that lack the
  primitive (e.g. Pi has no subagents). Those skills degrade to inline behavior.
- Migrating away from the Claude-specific hook automation; it remains, gated.

## 3. Background: harness capability matrix

Verified against primary documentation, mid-2026.

| Capability | Claude Code | Codex | OpenCode | Pi |
|---|---|---|---|---|
| Skills (`SKILL.md`, open standard) | ✅ `.claude/skills/` | ✅ `.agents/skills/` | ✅ reads `.claude/`, `.agents/`, `.opencode/skills/` | ✅ `.pi/skills/`, `.agents/skills/` |
| `npx skills add` target | ✅ | ✅ | ✅ | ✅ |
| Marketplace / plugin install | ✅ mature (`.claude-plugin/`) | ✅ new (`.codex-plugin/`), self-publish pending | ⚠️ plugins (npm/JS); no central marketplace | ✅ packages (npm/git `pi` key) |
| Per-project enable | ✅ `enabledPlugins` (committed `settings.json`) | ⚠️ `config.toml` | ⚠️ `opencode.json` | ⚠️ `settings.json` |
| Subagents | ✅ `.claude/agents/*.md`, auto-delegates | ✅ `.codex/agents/*.toml`, explicit-only | ✅ `.opencode/agents/*.md` + `mode:` | ❌ none (extensions/tmux) |
| Hooks | ✅ declarative `hooks/hooks.json` | ✅ declarative `hooks.json` (~1:1 with Claude) | ❌ JS/TS plugin required | ❌ TS extension required (`pi.on`) |
| Main config | `settings.json` | `config.toml` | `opencode.json(c)` | `settings.json` |

**Three portability tiers fall out of this matrix:**

1. **Skills — universal.** One `SKILL.md` works everywhere; `npx skills`
   resolves path differences. ~90% of the bundle.
2. **Subagents — not portable.** Three incompatible formats (md / TOML /
   md+`mode:`) and one absence (Pi). Per-harness adapter files required.
3. **Hooks — split 2+2.** Claude and Codex share a declarative `hooks.json`
   (shared bash scripts, near-identical wiring). OpenCode and Pi require a
   code shim (JS/TS plugin / TS extension) around the same scripts.

## 4. Skill classification

Each existing skill is classified by what it *requires to be correct*, which
determines which bundle it lands in and whether it needs a non-Claude fallback.

| Skill | Bundle | Portability | Notes |
|---|---|---|---|
| `astra` | core | pure | reference; Read/Grep/Bash only |
| `lc-cli` | core | pure | reference |
| `lc-feedback` | core | pure | session bug report |
| `narrative` | core | pure | prose authoring |
| `lc-new` | core | subagents:optional | conversational; PDF extraction fan-out is a *context* optimization, not correctness — inline fallback on harnesses without subagents |
| `lc-from-code` | paper | subagents:optional | `Explore` fan-out is convenience; degrades to inline Grep/Glob |
| `figure-comparison` | paper | subagents:optional | single inventory subagent; "do not fan out" — inline-able |
| `check-sentence-by-sentence` | paper | subagents:required | per-section fan-out *is* the method |
| `lc-from-paper` | paper | subagents:required | drives the ralph loop + in-iteration fan-out |
| `paper-extraction` | paper | pure (after refactor) | becomes a thin wrapper over `lightcone-extract-paper` console script |

**`requires` taxonomy** (recorded in each skill's frontmatter `metadata`, see §6.3):

- `pure` — install on every harness.
- `subagents:optional` — install everywhere; skill text must include an inline
  fallback branch for harnesses lacking a subagent primitive.
- `subagents:required` — install only where the harness advertises subagents
  (Claude, Codex, OpenCode — not Pi).

## 5. Repository structure

`lightconeresearch/skills`:

```
lightcone-skills/
│
├── skills/                              # ① CANONICAL — single source of truth
│   ├── core/                            #    consumed directly by `npx skills`, all harnesses
│   │   ├── astra/SKILL.md
│   │   ├── lc-cli/SKILL.md
│   │   ├── lc-new/SKILL.md
│   │   ├── lc-feedback/SKILL.md
│   │   └── narrative/SKILL.md
│   └── paper/                           #    optional bundle
│       ├── lc-from-paper/SKILL.md
│       ├── paper-extraction/SKILL.md    #    invokes `lightcone-extract-paper`; no vendored .py
│       ├── figure-comparison/SKILL.md
│       ├── check-sentence-by-sentence/SKILL.md
│       └── lc-from-code/SKILL.md
│
├── agents/                              # ② SUBAGENTS — per-harness format (not portable)
│   ├── claude/lc-extractor.md           #    md + frontmatter
│   ├── codex/lc-extractor.toml          #    TOML
│   └── opencode/lc-extractor.md         #    md + mode: subagent
│   #   pi/ — none; subagents:* skills degrade to inline
│
├── hooks/                               # ③ HOOKS
│   ├── scripts/                         #    shared bash (the actual logic)
│   │   ├── activate-venv.sh
│   │   ├── session-start.sh
│   │   └── validate-on-save.sh
│   ├── claude/hooks.json                #    declarative → ${CLAUDE_PLUGIN_ROOT}/scripts/...
│   ├── codex/hooks.json                 #    declarative → near-identical
│   ├── opencode/plugin.ts               #    JS/TS shim calling scripts/
│   └── pi/extension.ts                  #    TS shim (pi.on) calling scripts/
│
├── dist/                                # ④ HARNESS PACKAGING (manifests + symlinks)
│   ├── claude/
│   │   ├── .claude-plugin/marketplace.json   # lists lightcone-core, lightcone-paper
│   │   ├── lightcone-core/
│   │   │   ├── .claude-plugin/plugin.json
│   │   │   ├── skills  -> ../../../skills/core      (symlink)
│   │   │   ├── agents  -> ../../../agents/claude    (symlink)
│   │   │   └── hooks/                               (hooks.json + scripts symlink)
│   │   └── lightcone-paper/ …
│   └── codex/
│       └── …  (.codex-plugin/plugin.json + symlinks to skills/paper, agents/codex)
│
├── README.md
└── CHANGELOG.md
```

Notes:

- **Skills never duplicate.** They live once under `skills/`. Claude and Codex
  require skills *under* the plugin root, satisfied via symlink. `npx skills`
  reads the canonical tree directly.
- **OpenCode and Pi need no `dist/` entry** for skills — `npx skills add`
  handles them. They only consume `agents/<harness>/` and `hooks/<harness>/`
  when the user opts into automation.

## 6. Manifests and frontmatter (illustrative)

### 6.1 Claude marketplace + plugin

`dist/claude/.claude-plugin/marketplace.json`:

```json
{
  "name": "lightcone",
  "plugins": [
    { "name": "lightcone-core",  "source": "./lightcone-core" },
    { "name": "lightcone-paper", "source": "./lightcone-paper" }
  ]
}
```

`dist/claude/lightcone-core/.claude-plugin/plugin.json`:

```json
{
  "name": "lightcone-core",
  "description": "Core ASTRA skills: astra, lc-cli, lc-new, narrative, feedback.",
  "version": "0.1.0",
  "repository": "https://github.com/lightconeresearch/skills"
}
```

### 6.2 Codex plugin

`dist/codex/lightcone-core/.codex-plugin/plugin.json`:

```json
{
  "name": "lightcone-core",
  "description": "Core ASTRA skills for Codex.",
  "skills": ["../../skills/core"],
  "agents": ["../../agents/codex"],
  "hooks": "../../hooks/codex/hooks.json"
}
```

### 6.3 Skill frontmatter (canonical, harness-neutral)

```yaml
---
name: lc-new
description: >
  Start a new ASTRA analysis from a research question …
metadata:
  requires: subagents:optional        # pure | subagents:optional | subagents:required
  bundle: core
---
```

Harness-specific frontmatter (`allowed-tools`, Claude's `argument-hint`, etc.)
stays minimal in the canonical file; harness installers may augment it.

## 7. Code that leaves the skills repo

| Today (vendored in skill) | Becomes |
|---|---|
| `paper-extraction/scripts/extract-paper-substrate.py` (~1,475 lines) | `lightcone-extract-paper` console script in `lightcone-cli[paper]` extra |
| `ralph/scripts/ralph` (tmux loop runner, already multi-backend) | `ralph` (or `lc ralph`) console script |

Rationale: `npx skills` is `SKILL.md`-centric and will not reliably carry
executable blobs. Shipping code through a skill installer is worse than a
versioned, tested pip dependency. The skills become thin wrappers:
`paper-extraction/SKILL.md` reduces to "run `lightcone-extract-paper <doi>`".

## 8. `lc init` behavior

`lc init` becomes a harness detector + orchestrator. Pseudocode:

```
harness = --harness flag or prompt(available)
scaffold project (astra.yaml, results/, .lightcone/, AGENTS.md)

match harness:
  claude:
    claude plugin marketplace add lightconeresearch/skills
    claude plugin install lightcone-core@lightcone
    write .claude/settings.json: enabledPlugins { "lightcone-core@lightcone": true }
  codex:
    codex plugin marketplace add lightconeresearch/skills
    codex plugin add lightcone-core@lightcone
  opencode | pi:
    npx skills add lightconeresearch/skills --agent <harness>
    (optionally wire hooks/<harness>/ into the harness config)

if installer binary absent on PATH:
  print manual-install hint; continue (clean scaffold either way)
```

This replaces almost all of the per-tool path-copying logic in PR #76/#148.
The residual `HarnessConfig` registry shrinks to: install command, marketplace
ref, whether the harness supports `enabledPlugins`-style per-project enable,
and capability flags (`subagents`, `hooks`) used to decide which bundles/skills
to offer.

## 9. Versioning and coupling

Splitting skills into their own repo introduces CLI ↔ skill version skew:
skills reference `lc` / `astra` CLI surface.

**Decision needed (see §11).** Options:

- **A. Loose coupling (recommended).** Skills target only the stable, documented
  CLI surface. Marketplace tracks `main`. Accept rare drift; pin a tag only for
  releases. Lowest maintenance.
- **B. Pinned tags.** The skills repo tags releases that map to a CLI version
  range; `lc init` installs the tag matching the installed CLI. Stronger
  guarantee, more release ceremony.

## 10. Migration plan

1. **Stand up `lightconeresearch/skills`** with the §5 structure; move skills
   verbatim, grouped into `core/` and `paper/`.
2. **Extract code to pip.** Add `lightcone-cli[paper]` with
   `lightcone-extract-paper` and `ralph` console scripts; rewrite the two
   skills to call them. Add tests for the extracted code.
3. **Author adapters.** `agents/{claude,codex,opencode}/lc-extractor.*`;
   `hooks/{claude,codex}/hooks.json` + shared `scripts/`; OpenCode/Pi shims.
4. **Add `dist/claude/` marketplace** (Claude first; it is the most mature and
   gives `enabledPlugins`). Defer `dist/codex/` until needed.
5. **Rewrite `lc init`** per §8; delete the vendored `plugin/`/`claude/` tree
   and `force-include` rules from `lightcone-cli`.
6. **Add inline fallbacks** to the `subagents:optional` skills so they run on
   Pi / subagent-less harnesses.
7. **Docs + troubleshooting** for marketplace install and the
   install-once / enable-per-project model.

## 11. Open questions

1. **Symlinks vs. build step** for `dist/` plugin roots. Symlinks are
   zero-infra but fragile on Windows/some CI; a small "assemble" script is more
   robust. *Lean: symlinks.*
2. **Which marketplaces ship at launch?** *Lean: Claude marketplace + `npx
   skills` for the rest; add `dist/codex/` on demand* (Codex self-publish is
   still pending).
3. **Versioning model** — §9 A vs B. *Lean: A (loose).*
4. **Does `lightcone-cli` keep any bundled skills** for an out-of-the-box,
   offline `lc` reference (e.g. `lc-cli`, `astra`), or ship zero and rely on the
   marketplace? *Open.*
5. **`lc init` npx dependency** — require Node/npx for OpenCode/Pi installs, or
   provide a copy fallback for air-gapped/CI? *Open.*

## 12. Appendix: sources

- Claude Code: [plugins](https://code.claude.com/docs/en/plugins.md),
  [skills](https://code.claude.com/docs/en/skills.md),
  [subagents](https://code.claude.com/docs/en/sub-agents.md),
  [hooks](https://code.claude.com/docs/en/hooks.md)
- Codex: [skills](https://developers.openai.com/codex/skills),
  [plugins](https://developers.openai.com/codex/plugins),
  [subagents](https://developers.openai.com/codex/subagents),
  [hooks](https://developers.openai.com/codex/hooks)
- OpenCode: [skills](https://opencode.ai/docs/skills/),
  [plugins](https://opencode.ai/docs/plugins/),
  [agents](https://opencode.ai/docs/agents/)
- Pi: [skills](https://pi.dev/docs/latest/skills),
  [extensions](https://pi.dev/docs/latest/extensions),
  [packages](https://pi.dev/packages)
- Cross-harness installer: [vercel-labs/skills (`npx skills`)](https://github.com/vercel-labs/skills)
- Agent Skills standard: [agentskills.io](https://agentskills.io) ·
  AGENTS.md: [agents.md](https://agents.md)
