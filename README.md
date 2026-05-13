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

Then tell the agent what you have to start from — a research question (`/lc-new`), existing code (`/lc-from-code`), or a paper to reproduce (`/lc-from-paper`). After the spec exists, work with the agent however suits you; the substrate (`astra.yaml`, `lc run`, `lc status`, `lc verify`) keeps things in sync.

## Skills

The `/lc-from-*` family is parallel by what you start from: a question, code, or a paper.

### `/lc-new` — Scope and specify an analysis

Guides you from a research question to a complete `astra.yaml` specification through interactive conversation. The agent will:

- Help you identify the key decisions (methodological choices) in your analysis
- Search for and read relevant papers, extracting prior insights with exact verified quotes
- Structure decisions with options, defaults, and constraints between them
- Build universe files representing defensible alternative analysis paths
- Link literature evidence (prior insights) to the decision options it supports

You don't write any code or YAML during this phase — the agent produces the full specification.

### `/lc-from-code` — Bring an existing project into ASTRA

Scans an existing codebase, drafts an `astra.yaml` that captures its inputs, outputs, and analytical decisions, parameterizes the code so decisions can vary across universes, and runs the analysis through `lc` until every output materializes. Existing logic is left intact — changes are confined to parameter plumbing.

### `/lc-from-paper` — Reproduce a published paper

ORIENT-first driver for reproducing a published paper in ASTRA. ORIENT runs in the user's main session in seven stages — asks for the paper, runs `/paper-extraction` inline to acquire it, interviews the user (grounded in the paper), clones the reference code and runs `/lc-from-code` scan-only (if a repo exists), optionally follows up, then drafts a per-paper `constitution.md` (the ralph loop's driving document) + `CLAUDE.md` (auto-loading rules + accumulators) from the full paper-plus-code context for user review. Then the rest of the reproduction hands off to a **ralph loop** whose iterations carry the long middle: ARCHITECT → SPECIFY → LITERATURE → IMPLEMENT → RUN → COMPARE. Each iteration runs in a fresh tmux session against the constitution; the fresh-context property between iterations is what makes per-phase review work. When the loop closes (constitution `status: closed` after COMPARE returns `pass`), REVIEW runs back in the user's main session. Composes a bundle of sibling skills (`ralph`, `paper-extraction`, `narrative`, `figure-comparison`, `check-sentence-by-sentence`). See [`claude/lightcone/skills/README.md`](claude/lightcone/skills/README.md) for the full bundle map.

### `/lc-feedback` — Report a bug

Files a GitHub issue against the right repo (ASTRA or lightcone-cli) with version info and error context auto-collected from your session.

### Building and verifying

Once `astra.yaml` exists, you (or the agent) build it however suits you. The typical flow is `lc run` to materialize outputs, `lc status` to track progress, `astra validate astra.yaml` for spec validity, and `lc verify` for provenance integrity — agent-driven, ralph-looped, or hand-written, the `lc` substrate stays in sync.

## CLI Reference

### Global configuration

The first `lc` invocation auto-creates `~/.lightcone/config.yaml` with `container.runtime: auto`. To pin a runtime or change other settings, edit the file directly.

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
lc export wrroc                     # export a Workflow Run RO-Crate bundle for publication
lc export wrroc --zip -o run.zip    # zip the bundle for upload to Zenodo / WorkflowHub
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

### Publishing analyses

`lc export wrroc` walks your manifests and emits a [Workflow Run RO-Crate](https://www.researchobject.org/workflow-run-crate/) bundle — a JSON-LD package readable by WorkflowHub, Zenodo's RO-Crate plugin, and any RO-Crate-aware archive. Each materialization becomes a `CreateAction` with `object` (inputs, including upstream datasets via stable `@id` references) and `result` (the output dataset); decisions become `PropertyValue` entities; the workflow is captured as a `ComputationalWorkflow`. The lightcone manifest format on disk is unchanged — WRROC is the publication view, generated on demand. Use `--metadata-only` to ship only the provenance graph (useful when data files are huge), `--zip` to package the bundle for upload, or `-u <universe>` to restrict to specific universes.

## License

BSD 3-Clause
