# lightcone-cli

[![Tests](https://github.com/LightconeResearch/lightcone-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/LightconeResearch/lightcone-cli/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-BSD_3--Clause-green.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**lightcone-cli** (`lc`) is the agentic layer for [ASTRA](https://github.com/LightconeResearch/ASTRA) (Agentic Schema for Transparent Research Analysis). You interact with lightcone-cli through Claude Code: describe what you want, and the agent handles the implementation.

> **Note on the `lc` executable.** `lc` is not a standard Unix tool, but some users have a personal shell alias `lc='ls --color'`. If that's you, installing lightcone-cli will shadow that alias — rebind it if needed.

## Quick Start

```bash
pip install lightcone-cli
lc init my-analysis
cd my-analysis
claude
```

Then tell the agent `/lc-new` to scope your research question.

## Skills

### `/lc-new` — Scope and specify an analysis

Guides you from a research question to a complete `astra.yaml` specification through interactive conversation. The agent will:

- Help you identify the key decisions (methodological choices) in your analysis
- Search for and read relevant papers, extracting prior insights with exact verified quotes
- Structure decisions with options, defaults, and constraints between them
- Build universe files representing defensible alternative analysis paths
- Link literature evidence (prior insights) to the decision options it supports

You don't write any code or YAML during this phase — the agent produces the full specification.

### `/lc-build` — Build the analysis

Takes the specification from `/lc-new` and iteratively implements it: writing scripts, building containers, running computations, and committing progress. The agent works in a loop, and if it hits something ambiguous it flags it as an open question for you to resolve before continuing.

### `/lc-verify` — Audit a completed analysis

Runs a read-only audit checking that the implementation matches the specification: schema validity, result files present, metrics in expected format, and that decision parameters are actually wired through the code (not hardcoded).

### `/lc-feedback` — Report a bug

Files a GitHub issue against the right repo (ASTRA or lightcone-cli) with version info and error context auto-collected from your session.

## CLI Reference

### Project setup

```bash
lc init my-analysis                         # full scaffolding with Claude Code config
lc init my-analysis --target perlmutter-gpu  # pre-configure for an HPC target
lc init my-analysis --no-git --no-venv      # skip git/venv creation
```

### Targets and setup

Targets configure where lightcone-cli executes jobs. They're user-level (`~/.lightcone/targets/`), shared across projects, and work with any SLURM cluster. **Create separate targets for each run profile** rather than editing one repeatedly.

```bash
lc setup                            # interactive setup wizard (first-time)
lc setup --list                     # list configured targets
lc setup --show perlmutter-gpu      # show a target's config
lc setup --default perlmutter-gpu   # change user-wide default target
lc target add                       # create a new target interactively
lc target edit perlmutter-gpu       # edit an existing target
lc target --set perlmutter-gpu      # set project target
lc target --list                    # list available targets
```

For example, on Perlmutter:

```bash
lc target add perlmutter-debug   # gpu, qos: debug, 30min max
lc target add perlmutter-gpu     # gpu (A100 40GB), qos: regular
lc target add perlmutter-cpu     # cpu (128 cores/node), qos: regular
lc target add perlmutter-hbm80   # gpu_hbm80 (A100 80GB), qos: regular
lc target add perlmutter-preempt # gpu, qos: preempt (0.25x cost)
```

Resolution order: `--target` flag > `.lightcone/lightcone.yaml` > `~/.lightcone/config.yaml` > local.

**Extraction model:** Literature extraction subagents default to Sonnet. To change this, run `lc setup` and select "Change extraction model", or edit `extraction_model` in `~/.lightcone/config.yaml` directly (options: `sonnet`, `haiku`, or empty for inherit).

### Execution and monitoring

The agent runs these during `/lc-build`, but you can also run them directly:

```bash
lc run                              # materialize all outputs for all universes
lc run accuracy                     # materialize a specific output
lc run --universe baseline          # materialize for a specific universe
lc run --target perlmutter-gpu      # run on a SLURM target
lc status                           # show materialization status (ok / pending / no recipe)
lc status --universe baseline       # status for a specific universe
lc dev                              # launch Dagster webserver UI
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

### Telemetry

Claude Code sessions are traced to Langfuse with full conversation structure, tool calls, and git commit linking. Disable with `TRACE_TO_LANGFUSE=false` in `.claude/settings.local.json`.

## License

BSD 3-Clause
