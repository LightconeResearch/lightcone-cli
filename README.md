# lightcone-cli

[![License](https://img.shields.io/badge/License-BSD_3--Clause-426b78.svg?style=flat)](https://opensource.org/licenses/BSD-3-Clause)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-4e5a70?style=flat)](https://pypi.org/project/lightcone-cli/)
[![PyPI](https://img.shields.io/pypi/v/lightcone-cli?style=flat&color=f8f7f3)](https://pypi.org/project/lightcone-cli/)
[![Tests](https://img.shields.io/github/actions/workflow/status/LightconeResearch/lightcone-cli/tests.yml?style=flat&color=darkgreen)](https://github.com/LightconeResearch/lightcone-cli/actions/workflows/tests.yml)

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
claude   # trust the folder to install the lightcone plugin
```

The skills ship as a plugin, not with `lightcone-cli`. `lc init` registers the
[agent-skills](https://github.com/LightconeResearch/agent-skills) marketplace,
and Claude Code offers to install the `lightcone` plugin when you trust the
folder. To install it by hand:

```bash
claude plugin marketplace add LightconeResearch/agent-skills
claude plugin install lightcone@lightcone-research
```

Then tell the agent what you have to start from — a research question
(`/lightcone:new`), existing code (`/lightcone-experimental:from-code`), or a
paper to reproduce (`/lightcone-experimental:from-paper`).

→ [Full getting-started guide](https://docs.lightconeresearch.org/user/getting-started/)

## Skills

Skills live in the
[agent-skills](https://github.com/LightconeResearch/agent-skills) marketplace.

| Skill | What it does |
|---|---|
| `/lightcone:new` | Scope a new analysis from a research question into a full `astra.yaml` spec |
| `/lightcone:report` | Author the MyST report that references `astra.yaml` elements by path |
| `/lightcone:feedback` | File a bug report with version and error context auto-collected |
| `/lightcone-experimental:from-code` | Bring an existing codebase into ASTRA |
| `/lightcone-experimental:from-paper` | Reproduce a published paper end-to-end |

## Capabilities

- **Multiverse analysis** — define methodological decisions with multiple options; `lc` runs your analysis across all defensible paths automatically
- **Provenance integrity** — every output gets a content-addressed manifest; `lc verify` detects tampering or broken chains
- **HPC-ready execution** — Snakemake-backed DAG dispatch with SLURM and container support (Docker, Podman, Apptainer) out of the box
- **Reproducible publishing** — `lc export wrroc` emits a [Workflow Run RO-Crate](https://www.researchobject.org/workflow-run-crate/) bundle ready for Zenodo or WorkflowHub

→ [Full documentation](https://docs.lightconeresearch.org)

## License

BSD 3-Clause — see [LICENSE](LICENSE) for details.
