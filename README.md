# lightcone-cli

[![License](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![Python](https://img.shields.io/pypi/pyversions/lightcone-cli)](https://pypi.org/project/lightcone-cli/)
[![Tests](https://github.com/LightconeResearch/lightcone-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/LightconeResearch/lightcone-cli/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/lightcone-cli)](https://pypi.org/project/lightcone-cli/)

<!-- [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff) -->

**lightcone-cli** (`lc`) is the agentic execution layer for
[ASTRA](https://astra-spec.org/latest/) (Agentic Schema for Transparent
Research Analysis). Describe your analysis to an AI agent and `lc` takes
care of the rest — specification, execution, and provenance.

## Quick Start

```bash
uv tool install lightcone-cli
lc init my-analysis
cd my-analysis
claude
```

Then tell the agent what you have to start from — a research question
(`/lc-new`), existing code (`/lc-from-code`), or a paper to reproduce
(`/lc-from-paper`).

→ [Full getting-started guide](https://docs.lightconeresearch.org/user/getting-started/)

## Skills

| Skill | What it does |
|---|---|
| [`/lc-new`](https://docs.lightconeresearch.org/skills/lc-new/) | Scope a new analysis from a research question into a full `astra.yaml` spec |
| [`/lc-from-code`](https://docs.lightconeresearch.org/skills/lc-from-code/) | Bring an existing codebase into ASTRA |
| [`/lc-from-paper`](https://docs.lightconeresearch.org/skills/lc-from-paper/) | Reproduce a published paper end-to-end |
| [`/lc-feedback`](https://docs.lightconeresearch.org/skills/lc-feedback/) | File a bug report with version and error context auto-collected |

## Capabilities

- **Multiverse analysis** — define methodological decisions with multiple options; `lc` runs your analysis across all defensible paths automatically
- **Provenance integrity** — every output gets a content-addressed manifest; `lc verify` detects tampering or broken chains
- **HPC-ready execution** — Snakemake-backed DAG dispatch with SLURM and container support (Docker, Podman, Apptainer) out of the box
- **Reproducible publishing** — `lc export wrroc` emits a [Workflow Run RO-Crate](https://www.researchobject.org/workflow-run-crate/) bundle ready for Zenodo or WorkflowHub

→ [Full documentation](https://docs.lightconeresearch.org)

## License

BSD 3-Clause — see [LICENSE](LICENSE) for details.
