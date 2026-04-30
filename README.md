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

Then tell the agent `/lc-new` to scope your research question. After the spec exists, just tell the agent to build it — implementation is a normal Claude Code workflow guided by `.claude/guides/`.

## Skills

### `/lc-new` — Scope and specify an analysis

Guides you from a research question to a complete `astra.yaml` specification through interactive conversation. The agent will:

- Help you identify the key decisions (methodological choices) in your analysis
- Search for and read relevant papers, extracting prior insights with exact verified quotes
- Structure decisions with options, defaults, and constraints between them
- Build universe files representing defensible alternative analysis paths
- Link literature evidence (prior insights) to the decision options it supports

You don't write any code or YAML during this phase — the agent produces the full specification.

### `/lc-migrate` — Bring an existing project into ASTRA

Scans an existing codebase, drafts an `astra.yaml` that captures its inputs, outputs, and analytical decisions, parameterizes the code so decisions can vary across universes, and runs the analysis through `lc` until every output materializes. Existing logic is left intact — changes are confined to parameter plumbing.

### `/lc-feedback` — Report a bug

Files a GitHub issue against the right repo (ASTRA or lightcone-cli) with version info and error context auto-collected from your session.

### Building and verifying

There is no `/lc-build` or `/lc-verify` skill — building and verifying are part of the normal Claude Code workflow once `astra.yaml` exists. The agent reads `.claude/guides/lightcone-cli-reference.md` (workflow, commands, status meanings) and `.claude/guides/astra-reference.md` (spec syntax) and drives the build directly: write scripts under `src/`, run `lc run`, watch `lc status` until every output is `ok`, then `astra validate astra.yaml` and `lc verify` to confirm the spec is valid and the provenance chain is intact.

## CLI Reference

### Global configuration

The first `lc` invocation auto-creates `~/.lightcone/config.yaml` with `container.runtime: auto`. To pin a runtime or change other settings, edit the file directly.

**Extraction model:** Literature extraction subagents default to Sonnet. To change this, set `extraction_model:` in `~/.lightcone/config.yaml` (options: `sonnet`, `haiku`, or omit for inherit).

### Project scaffolding

```bash
lc init my-analysis                          # full scaffolding with Claude Code config
lc init my-analysis --no-git --no-venv       # skip git/venv creation
lc init my-analysis --permissions yolo       # Claude Code permission tier (yolo|recommended|minimal)
lc init my-analysis --scratch '$SCRATCH/lc'  # override scratch root for snakemake state and dask spill
```

### Execution and monitoring

The agent runs these as it builds out an analysis, but you can also run them directly:

```bash
lc run                              # materialize all outputs for all universes
lc run accuracy                     # materialize a specific output
lc run --universe baseline          # materialize for a specific universe
lc run --jobs 8                     # bound parallel job dispatch
lc run --force                      # force re-materialization
lc run --rerun-triggers code,input  # override Snakemake rerun-triggers (default: code,input,mtime,params)
lc status                           # show materialization status (ok / stale / missing / alias)
lc status --universe baseline       # status for a specific universe
lc status --json                    # machine-readable output
lc verify                           # recompute hashes and walk the provenance chain
lc build                            # pre-build container images from Containerfiles
lc build --force --runtime podman   # force rebuild and pin runtime
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

### Execution backend

`lc run` generates a Snakefile from `astra.yaml` and shells out to Snakemake. Snakemake owns DAG construction, staleness detection, parallel dispatch, and per-rule resource translation; `lc` owns the integrity layer (per-output content-addressed manifests written next to each result).

Jobs always dispatch through a Dask cluster: a `LocalCluster` on a workstation, srun-launched workers inside a SLURM allocation, or an existing scheduler if `DASK_SCHEDULER_ADDRESS` is set. Recipes execute inside their declared container via Docker, podman, or podman-hpc — set `container.runtime` in `~/.lightcone/config.yaml` (default `auto`) to pin one. Per-recipe `resources:` (cpus, gpus, memory, time limit) flow through to the cluster.

### Provenance integrity

Every materialized output gets a `.lightcone-manifest.json` capturing `code_version` (sha256 of recipe + container + decisions), `data_version` (sha256 of the output directory contents), input manifest versions, git SHA, lc version, and host. `lc verify` recomputes hashes and walks the chain — failures surface as `tampered_data`, `broken_chain`, or `missing_manifest`. `lc status` reads only manifests, so it works offline.

### Telemetry

Claude Code sessions are traced to Langfuse with full conversation structure, tool calls, and git commit linking. Disable with `TRACE_TO_LANGFUSE=false` in `.claude/settings.local.json`.

## License

BSD 3-Clause
