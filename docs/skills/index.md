# Skills

Skills shape the agent's workflow around recurring research tasks. They are
Claude Code and Codex slash commands. They no longer ship inside
`lightcone-cli`. They live in the
[LightconeResearch/agent-skills](https://github.com/LightconeResearch/agent-skills)
marketplace and install as plugins.

`lc init` registers the marketplace with both harnesses. On Claude Code it
writes the project's `.claude/settings.json`, and Claude Code offers to install
the `lightcone` plugin when you trust the folder. On Codex it runs
`codex plugin` (global registration) when `codex` is on PATH. See
[Install](../user/install.md) for the by-hand commands.

## Plugins

The marketplace ships three plugins.

| Plugin | Skills | Notes |
|---|---|---|
| `astra` | `astra` | Standalone ASTRA authoring skill. Ships the validate-on-save and activate-on-read hooks. |
| `lightcone` *(recommended)* | `new`, `report`, `cli`, `feedback` | Bundles `astra`. Ships the venv and session hooks plus the `lc-extractor` subagent. This one plugin is all you need for interactive work. |
| `lightcone-experimental` | `from-paper`, `from-code`, `paper-extraction`, `ralph`, `check-sentence-by-sentence`, `figure-comparison` | Opt-in. Requires `lightcone`. Under active development. |

## Skills

Plugin skills are namespaced by plugin. Install `lightcone`, then use these.

| Skill | Command | What it does |
|---|---|---|
| `new` | `/lightcone:new` | Scope a research question into an `astra.yaml`, with optional literature extraction. |
| `report` | `/lightcone:report` | Author the MyST report that references `astra.yaml` elements by path. |
| `cli` | `/lightcone:cli` | The `lc` execution reference — spec–code invariant, status interpretation, failure diagnosis. |
| `feedback` | `/lightcone:feedback` | File a bug report with version and error context. |
| `astra` | `/lightcone:astra` | Author an `astra.yaml` — orientation and judgment (bundled from the `astra` plugin). |

The `lightcone-experimental` plugin adds `/lightcone-experimental:from-paper`,
`/lightcone-experimental:from-code`, `/lightcone-experimental:paper-extraction`,
`/lightcone-experimental:ralph`,
`/lightcone-experimental:check-sentence-by-sentence`, and
`/lightcone-experimental:figure-comparison`.

## Authoring

Skill sources, helper scripts, and authoring guidance now live in the
[agent-skills](https://github.com/LightconeResearch/agent-skills) repository.
Each skill follows the open [Agent Skills standard](https://agentskills.io) — a
`SKILL.md` plus optional `references/`, `scripts/`, and `assets/`.
