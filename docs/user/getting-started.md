# Getting Started

This page walks you from "nothing on my disk" to a project that's ready
for the agent to fill in. The actual *doing science* part is in the
[tutorial](tutorial.md); this page is the orientation.

Make sure you've finished the [install](install.md) first.

## 1. Create a project

    lc init my-analysis
    cd my-analysis

`lc init` is a one-shot setup. It creates a small, opinionated
directory layout and stops; it doesn't ask any questions.

## 2. What you got

    my-analysis/
    ├── astra.yaml                  # the spec — this is where everything lives
    ├── CLAUDE.md                   # short note for the agent (resumes context across sessions)
    ├── .gitignore
    ├── .git                        # initialized git repository (skip with --no-git)
    ├── .venv/                      # Python virtual env (skip with --no-venv)
    ├── .claude/                    # Claude Code plugin — skills, agents, hooks
    ├── .lightcone/                 # internal scratchpad — don't edit by hand
    ├── Containerfile               # build instructions for a local testing container — don't edit by hand
    ├── requirements.txt            # software dependencies — don't edit by hand
    ├── universes/                  # 
    ├── src/                        # placeholder directories for now
    └── results/                    # 

The two files you'll actually look at:

### `astra.yaml`

The single source of truth for your analysis. Inputs, outputs,
methodological decisions, recipes. Everything else lightcone-cli does
is downstream of this file.

The boilerplate written by `lc init` is one example output and an
empty decisions block — enough to run an `lc run` and see something
materialize, but not yet a real analysis.

### `CLAUDE.md`

A short note that tells Claude Code about the project. The skills will
update this as you go (filling in working notes, design context). You
can edit it by hand whenever you want.

## 3. Open Claude Code

    claude

That opens an interactive session inside `my-analysis/`. Claude Code
reads `astra.yaml` and `CLAUDE.md` so it has context.

## 4. The five slash commands

Inside Claude Code:

| Command | Use it when… |
|---------|--------------|
| `/lc-new` | You're starting from a research question and an empty `astra.yaml`. |
| `/lc-build` | You have a scoped `astra.yaml` and you want the analysis implemented and run. |
| `/lc-verify` | You finished a build and want a read-only audit. |
| `/lc-migrate` | You have an existing codebase you want wrapped in ASTRA. |
| `/lc-feedback` | Something broke and you want to file a GitHub issue without leaving the session. |

The next page, [The Agentic Workflow](agent-workflow.md),
explains each of these in more detail.

## 5. The four CLI commands you'll actually type

You can mostly stay inside Claude Code, but the durable workhorse is
the `lc` CLI. The four commands you'll touch by hand:

```bash
lc run                          # produce all outputs declared in astra.yaml
lc status                       # what's done, stale, or missing — fast and offline
lc verify                       # heavier audit: recomputes hashes, walks the input chain
lc build                        # build container images declared in astra.yaml
```

Each of these has a one-page reference in the
[CLI section](../cli/index.md) of the maintainer docs if you want
exact flags.

## 6. Read on

- [The Agentic Workflow](agent-workflow.md) — how each slash
  command actually flows.
- [Tutorial: Your First Analysis](tutorial.md) — end-to-end, with the
  agent doing most of the typing.
- [Glossary](glossary.md) — terminology in plain language.
