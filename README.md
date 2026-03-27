# Prism

[![Tests](https://github.com/LightconeResearch/Prism/actions/workflows/tests.yml/badge.svg)](https://github.com/LightconeResearch/Prism/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
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
- Search for and read relevant papers, extracting insights with exact verified quotes
- Structure decisions with options, defaults, and constraints between them
- Build universe files representing defensible alternative analysis paths
- Link literature evidence to the decision options it supports

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

Targets configure where Prism executes jobs. They're user-level (`~/.prism/targets/`), shared across projects, and work with any SLURM cluster. **Create separate targets for each run profile** rather than editing one repeatedly.

```bash
prism setup                            # interactive setup wizard (first-time)
prism setup --list                     # list configured targets
prism setup --show perlmutter-gpu      # show a target's config
prism setup --default perlmutter-gpu   # change user-wide default target
prism target add                       # create a new target interactively
prism target edit perlmutter-gpu       # edit an existing target
prism target --set perlmutter-gpu      # set project target
prism target --list                    # list available targets
```

For example, on Perlmutter:

```bash
prism target add perlmutter-debug   # gpu, qos: debug, 30min max
prism target add perlmutter-gpu     # gpu (A100 40GB), qos: regular
prism target add perlmutter-cpu     # cpu (128 cores/node), qos: regular
prism target add perlmutter-hbm80   # gpu_hbm80 (A100 80GB), qos: regular
prism target add perlmutter-preempt # gpu, qos: preempt (0.25x cost)
```

Resolution order: `--target` flag > `prism.yaml` > `~/.prism/config.yaml` > local.

**Extraction model:** Literature extraction subagents default to Sonnet. To change this, run `prism setup` and select "Change extraction model", or edit `extraction_model` in `~/.prism/config.yaml` directly (options: `sonnet`, `haiku`, or empty for inherit).

### Execution and monitoring

The agent runs these during `/prism-build`, but you can also run them directly:

```bash
prism run                              # materialize all outputs for all universes
prism run accuracy                     # materialize a specific output
prism run --universe baseline          # materialize for a specific universe
prism run --target perlmutter-gpu      # run on a SLURM target
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

The agent can search for papers, download PDFs by DOI, and extract insights with exact quotes. Quotes are machine-verified against the source PDFs using fuzzy matching with Unicode normalization. Insights are linked to the decision options they support, creating a traceable evidence chain.

### Sub-analyses

Complex analyses can be decomposed into nested stages, each with their own inputs, outputs, decisions, and recipes. Sub-analyses use the same schema as the top level, and can reference each other's outputs.

### Execution backends

Recipes run via Docker, local subprocess, or SLURM batch submission depending on your target configuration. Recipe dependencies are resolved automatically — if output B depends on output A, A runs first. Per-recipe resource requests (CPUs, GPUs, memory, time limit) are translated to the appropriate backend flags.

### Telemetry

Claude Code sessions are traced to Langfuse with full conversation structure, tool calls, and git commit linking. Disable with `TRACE_TO_LANGFUSE=false` in `.claude/settings.local.json`.

## License

Apache 2.0
