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

## Developing with an AI assistant

Ground rules for code written in this repository, human- or AI-authored.
Each of these has been asked for in review at least once; none is optional:

- **Never reference the design spec in code or comments.** No `spec §7`,
  no section numbers, no "the spec says". Code and its comments must
  stand on their own; design rationale lives in the design documents.
- **No backward-compatibility code.** Nothing exists to honor the
  behavior of an older CLI, an older wire format, or trained fingers.
  If old behavior isn't promised, don't guard, version, or migrate it.
- **No foreshadowing.** No code, comment, flag, or user-facing message
  may mention a verb, layer, or feature that does not exist yet. The
  codebase is consistent with the project *at this point in time*.
- **No escape hatches around guarantees.** A feature that enforces
  something ships without a flag to turn the enforcement off.
- **Prefer literal behavior over invented convenience.** The current
  directory is the project root; erroring beats walking up, guessing,
  or auto-discovering.
- **Streamline before shipping.** No small helper functions or
  rendering layers where a few inline lines read fine; consolidate.
- **Be honest about provenance.** Third-party material we adapt is
  "inspired from" upstream, clearly marked — never passed off as
  verbatim, never left unattributed.
- **Leave working files alone.** Don't edit files that are fine just
  because a change nearby made them look touchable.

## License

BSD 3-Clause — see [LICENSE](LICENSE) for details.
