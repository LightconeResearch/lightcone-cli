# Prism

[![Tests](https://github.com/LightconeResearch/Prism/actions/workflows/tests.yml/badge.svg)](https://github.com/LightconeResearch/Prism/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-BSD_3--Clause-green.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Prism is the agentic layer for [ASTRA](https://github.com/LightconeResearch/ASTRA) (Agentic Schema for Transparent Research Analysis). You interact with Prism through Claude Code: describe what you want, and the agent handles the implementation.

## Quick Start

```bash
prism init my-analysis
cd my-analysis
claude
```

Then tell the agent `/prism-new` to scope your research question.

## Skills

### `/prism-new` — Scope and specify an analysis

Guides you from a research question to a complete `astra.yaml` specification through interactive conversation. The agent will:

- Help you identify the key decisions (methodological choices) in your analysis
- Search for and read relevant papers, extracting prior insights with exact verified quotes
- Structure decisions with options, defaults, and constraints between them
- Build universe files representing defensible alternative analysis paths
- Link literature evidence (prior insights) to the decision options it supports

You don't write any code or YAML during this phase — the agent produces the full specification.

### `/prism-build` — Build the analysis

Takes the specification from `/prism-new` and iteratively implements it: writing scripts, building containers, running computations, and committing progress. The agent works in a loop, and if it hits something ambiguous it flags it as an open question for you to resolve before continuing.

### `/prism-verify` — Audit a completed analysis

Runs a read-only audit checking that the implementation matches the specification: schema validity, result files present, metrics in expected format, and that decision parameters are actually wired through the code (not hardcoded).

### `/prism-feedback` — Report a bug

Files a GitHub issue against the right repo (ASTRA or Prism) with version info and error context auto-collected from your session.

## CLI Reference

### Project setup

```bash
prism init my-analysis                        # full scaffolding with Claude Code config
prism init my-analysis --target perlmutter-gpu  # pre-configure for an HPC target
prism init my-analysis --no-git --no-venv     # skip git/venv creation
```

### Targets and setup

Targets configure where Prism executes jobs. They're user-level (`~/.prism/targets/`), shared across projects, and work with any SLURM cluster. **One target per machine** — each target knows about all your available queues, and Prism picks the right one at runtime.

```bash
prism setup                            # interactive setup wizard (first-time)
prism setup --list                     # list configured targets
prism setup --default perlmutter       # change user-wide default target
prism target add                       # create a new target interactively
prism target --show perlmutter         # show target config and QoS choices
prism target --set perlmutter          # set project target
prism target --list                    # list available targets
prism target refresh perlmutter        # re-query SLURM for QoS limits
```

QoS management — add, edit, remove, or change the default queue without editing YAML:

```bash
prism target add-qos perlmutter                    # interactive picker
prism target add-qos perlmutter gpu_shared \
    --constraint gpu --slurm-qos shared \
    --use-for "small GPU jobs"                      # direct mode
prism target edit-qos perlmutter gpu_debug \
    --use-for "quick tests"                         # edit description
prism target remove-qos perlmutter gpu_shared       # remove a queue
prism target set-default-qos perlmutter gpu_regular  # change default
```

Resolution order: `--target` flag > `prism.yaml` > `~/.prism/config.yaml` > local.

**Extraction model:** Literature extraction subagents default to Sonnet. To change this, run `prism setup` and select "Change extraction model", or edit `extraction_model` in `~/.prism/config.yaml` directly (options: `sonnet`, `haiku`, or empty for inherit).

### Execution and monitoring

The agent runs these during `/prism-build`, but you can also run them directly:

```bash
prism run                              # materialize all outputs for all universes
prism run accuracy                     # materialize a specific output
prism run --universe baseline          # materialize for a specific universe
prism run --target perlmutter          # run on a SLURM target
prism run --qos regular --time-limit 2h  # override QoS and time limit
prism run --strategy switch            # switch QoS instead of reducing resources
prism status                           # show materialization status (ok / pending / no recipe)
prism status --universe baseline       # status for a specific universe
prism dev                              # launch Dagster webserver UI
```

## Capabilities

### Multiverse analysis

Define decisions (methodological choices) with multiple options. Each universe file selects one option per decision, representing a complete defensible analysis path. The agent can generate and run across all universes automatically.

### Decision constraints

Decisions can be mutually exclusive (`incompatible_with`) or co-required (`requires`). Options can also be marked as `excluded` with a reason, documenting alternatives that were considered and rejected.

### Literature integration

The agent can search for papers, download PDFs by DOI, and extract prior insights with exact quotes. Quotes are machine-verified against the source PDFs using fuzzy matching with Unicode normalization. Prior insights are linked to the decision options they support, creating a traceable evidence chain. After the analysis runs, findings capture conclusions backed by the analysis outputs.

### Sub-analyses

Complex analyses can be decomposed into nested stages, each with their own inputs, outputs, decisions, and recipes. Sub-analyses use the same schema as the top level, and can reference each other's outputs.

### Execution backends

Recipes run via Docker, local subprocess, or SLURM batch submission depending on your target configuration. Recipe dependencies are resolved automatically — if output B depends on output A, A runs first. Per-recipe resource requests (CPUs, GPUs, memory, time limit) are translated to the appropriate backend flags.

### Dynamic SLURM discovery

Prism queries `sacctmgr` to discover available QoS and their resource limits on any SLURM cluster. Each queue in your target has a `use_for` description (e.g., "quick iteration", "production runs") that helps the agent choose. When a recipe exceeds the default queue's limits, Prism auto-adjusts:

- **`fit` strategy** (default): reduces resources (nodes, time limit) to stay in the current queue for faster turnaround
- **`switch` strategy**: keeps resources as-is and picks a queue that can handle them

Container flags (`--gpu`, `--mpi`) are derived automatically from recipe resources — no manual configuration needed.

### Telemetry

Claude Code sessions are traced to Langfuse with full conversation structure, tool calls, and git commit linking. Disable with `TRACE_TO_LANGFUSE=false` in `.claude/settings.local.json`.

## License

BSD 3-Clause
