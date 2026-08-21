# lightcone-cli

[![License](https://img.shields.io/badge/License-BSD_3--Clause-426b78.svg?style=flat)](https://opensource.org/licenses/BSD-3-Clause)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-4e5a70?style=flat)](https://pypi.org/project/lightcone-cli/)
[![PyPI](https://img.shields.io/pypi/v/lightcone-cli?style=flat&color=f8f7f3)](https://pypi.org/project/lightcone-cli/)
[![Tests](https://img.shields.io/github/actions/workflow/status/LightconeResearch/lightcone-cli/tests.yml?style=flat&color=darkgreen)](https://github.com/LightconeResearch/lightcone-cli/actions/workflows/tests.yml)

<!-- [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff) -->

**lightcone-cli** (`lc`) is the execution layer for
[ASTRA](https://astra-spec.org/latest/) (Agentic Schema for Transparent
Research Analysis). Describe your analysis in an `astra.yaml`
specification and `lc` takes care of the rest — execution and
provenance.

## Quick Start

```bash
uv tool install lightcone-cli   # or: pip install lightcone-cli
lc init my-analysis
cd my-analysis
# describe your analysis in astra.yaml, then:
lc run
```

ASTRA specs are plain, structured YAML — they work well hand-written or
drafted with any AI coding assistant.

→ [Full getting-started guide](https://docs.lightconeresearch.org/user/getting-started/)

## Capabilities

- **Multiverse analysis** — define methodological decisions with multiple options; `lc` runs your analysis across all defensible paths automatically
- **Provenance integrity** — every output gets a content-addressed manifest; `lc verify` detects tampering or broken chains
- **HPC-ready execution** — Snakemake-backed DAG dispatch with SLURM and container support (Docker, Podman, Apptainer) out of the box
- **Reproducible publishing** — `lc export wrroc` emits a [Workflow Run RO-Crate](https://www.researchobject.org/workflow-run-crate/) bundle ready for Zenodo or WorkflowHub

→ [Full documentation](https://docs.lightconeresearch.org)

## License

BSD 3-Clause — see [LICENSE](LICENSE) for details.
