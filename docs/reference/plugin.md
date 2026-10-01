# Agent plugin

The `lightcone` plugin teaches a coding agent to scope, run and publish
a Lightcone project. It works in Claude Code and Codex, and ships two
skills and three hooks. It bundles the `astra` plugin, so it is the
only plugin to install: don't install `astra` alongside it.
Installation is covered in [Install](installation.md#1-the-agent-plugin).

## What it ships

| Part | What it does |
|---|---|
| `lightcone` skill | The project companion: scoping a new analysis, resuming one, running it with `lc`, diagnosing failures, writing the report, publishing. |
| `astra` skill | Writing and checking `astra.yaml`: its structure, decisions, universes, and citations with verifiable quotes. Bundled from the `astra` plugin. |
| Session-start hook: `lc` check | Tells the agent whether `lc` is installed and recent enough, before it tries to use it. |
| Session-start hook: project orientation | In a directory with an `astra.yaml`, gives the agent the file's location and a summary of the analysis. |
| Validate-on-save hook | Re-validates the project whenever the agent saves `astra.yaml` or a universe file, and hands the result back to the agent. |

## Invoking the skills

Skills are namespaced by plugin: `/<plugin>:<skill>` in Claude,
`$<plugin>:<skill>` in the Codex CLI.

| Skill | Claude Code, Claude App, Codex App | Codex CLI |
|---|---|---|
| `lightcone` | `/lightcone:lightcone` | `$lightcone:lightcone` |
| `astra` | `/lightcone:astra` | `$lightcone:astra` |

You rarely need to. The agent loads the `lightcone` skill on its own
when you ask to start, resume, run, debug or publish an analysis, and
whenever the directory holds an `astra.yaml` and you ask to run, fix or
interpret it. It loads the `astra` skill whenever it reads or writes
`astra.yaml`.

## The `lightcone` skill

The skill routes the agent by what you are doing:

| You are | The agent |
|---|---|
| Starting from a research question | Interviews you to scope the analysis, and writes no code until the scope is agreed. Optionally reads the literature first and records what it finds. |
| Picking up an existing project | Runs `lc status` and summarizes where things stand before asking what's next, rather than re-interviewing you. |
| Writing or debugging a script | Tries it with `lc run`, which gives the command the same sandbox a recipe gets. |
| Producing an output | Wires the recipe into `astra.yaml`, commits, and runs `lc materialize`. It is done when `lc materialize --check` passes. |
| Hitting a refusal or a failing recipe | Follows the remedy `lc` names, rather than working around it. |
| Writing the report | Keeps the MyST report (`index.md`) referencing the analysis rather than restating it. |
| Sharing or archiving | Walks you through declaring a license, which turns on the RO-Crate view `lc materialize` maintains. |

A few rules it holds to:

- **It never installs or upgrades `lc` unasked** when someone is there
  to answer. In a headless session it acts and says what it changed.
- It drives `lc` with `--json` and quotes the engine's own reasons
  rather than guessing at them.
- It runs everything through `lc`: never the container runtime, the
  sandbox or a scheduler directly, and never writes into `results/` by
  hand.
- It keeps the **Project Notes** in the project's `AGENTS.md` current,
  so a later session can pick the work up.

## The `astra` skill

The `astra` skill carries the judgment a schema cannot: what deserves
to be a decision, when to split an analysis, how to back a claim with
evidence. It runs the `astra` CLI through `uvx` at a pinned version,
never whatever `astra` is on your `PATH`, and re-validates after every
change. For citations, it caches each paper when it is cited, copies
quotes verbatim from the cached PDF, and verifies them before calling
the work done.

## Hooks

The hooks report; they never install anything. Each runs with a
90-second timeout and adds its findings to the agent's context.

**`lc` check** (session start). Runs in every session, with or without
a project, since the skill's first job is often to create one. It
checks that `lc` is on `PATH`, that `lc --version` answers, and that
the version is at least the one the skill was written against. When
all is well it reports `Lightcone CLI ready: lc <version>`, and the
agent skips its own check. Otherwise it names the problem and the
remedy: in an interactive session the agent asks you before installing,
upgrading or repairing `lc`; in a headless one (`claude -p`, an SDK
embed, or CI) it does so itself and says so.

**Project orientation** (session start). Only when `./astra.yaml`
exists. It runs `astra info` and gives the agent the spec's location
and the analysis's shape. If uv is missing, it tells the agent to ask
you to install it.

**Validate on save** (after the agent's `Write`, `Edit` or
`apply_patch`). When the save mentions `astra.yaml` or a universe file
and `./astra.yaml` exists, it validates the whole project, the
analysis file and every universe file, so an edit that strands a
universe fails at once. A failure is passed to the agent verbatim.

## Requirements and versions

- **uv.** The `astra` CLI runs through `uvx`, which installs it on
  first use.
- **`lc`.** Not installed by the plugin; see
  [Install](installation.md#2-the-lc-cli). The `lc` check
  names the minimum version.
- **bash**, which runs the hook scripts.

The plugin pins the tools it drives. In plugin version 0.0.3, the
`astra` CLI is `astra-tools` 0.2.17 and the minimum `lc` is
`lightcone-cli` 0.5.0rc3. The source is
[LightconeResearch/agent-skills](https://github.com/LightconeResearch/agent-skills).
