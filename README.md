# lightcone-cli

[![License](https://img.shields.io/badge/License-BSD_3--Clause-426b78.svg?style=flat)](https://opensource.org/licenses/BSD-3-Clause)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-4e5a70?style=flat)](https://pypi.org/project/lightcone-cli/)
[![PyPI](https://img.shields.io/pypi/v/lightcone-cli?style=flat&color=f8f7f3)](https://pypi.org/project/lightcone-cli/)
[![Tests](https://img.shields.io/github/actions/workflow/status/LightconeResearch/lightcone-cli/tests.yml?style=flat&color=darkgreen)](https://github.com/LightconeResearch/lightcone-cli/actions/workflows/tests.yml)

**lightcone-cli** (`lc`) is the execution layer for
[ASTRA](https://astra-spec.org/latest/) (Agentic Schema for Transparent
Research Analysis). Describe your analysis in an `astra.yaml`
specification and `lc` takes care of the rest — execution, environments,
and provenance.

## Quick Start

**lightcone-cli** only requires you to have `uv` installed on your environment, and will take care of everything else. See how to install uv here: [https://docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation). 

Then to install **lightcone-cli**:
```bash
uv tool install lightcone-cli
```

Once the CLI is installed, you can use it to create an ASTRA project and generate outputs like so:

```bash
lc init my-analysis
cd my-analysis
# describe your analysis in astra.yaml, write your scripts,
# declare what they import through normal uv interactions:
uv add numpy
# When you are done with your edits, commit:
git add -A && git commit -m "First analysis"
# Use the lightcone CLI to generate your outputs with full provenance tracking
lc materialize
```

ASTRA specs are plain, structured YAML — they work well hand-written or
drafted with any AI coding assistant.

→ [Full getting-started guide](https://docs.lightconeresearch.org/user/getting-started/)

## Capabilities

- **Multiverse analysis** — declare methodological decisions with multiple defensible options; `lc` materializes your analysis across every universe you define
- **Provenance by construction** — every output is committed to git together with a content-addressed manifest and a re-runnable run record; git-annex carries the bytes, so results travel with the repository
- **Locked, isolated execution** — a project's environment is `pyproject.toml` + `uv.lock`; recipes run in it under a sandbox (Landlock on Linux, Seatbelt on macOS) that keeps undeclared files out and stray writes contained
- **Containers and HPC** — declare `[tool.lightcone.image]` and recipes run in a content-addressed image archived in the repository itself; use every node of a SLURM allocation or attach to a cluster from JupyterLab's Lightcone sidebar
- **Publication view** — declare a license and `lc materialize` maintains an [RO-Crate](https://www.researchobject.org/ro-crate/) of the project and its provenance, ready to archive or deposit

→ [Full documentation](https://docs.lightconeresearch.org)

## Compute clusters

Start a cluster in JupyterLab's **Lightcone sidebar › Compute**, then run
`lc materialize` as usual. The CLI chooses an execution target in this order:

1. An existing SLURM allocation, when `SLURM_JOB_ID` is set.
2. The one compatible live cluster registered by the sidebar.
3. This machine, subject to the existing HPC login-node guard.

The run names its target, and `--json` includes a `venue` object. If several
clusters can serve the project, stop all but one. A selected cluster that is
queued, unreachable, or incompatible with the installed engine produces an
actionable refusal; the run does not silently switch targets. Workers need
the same Lightcone, Dask Distributed and Python versions as the CLI, and must
see the project at the same filesystem path.

Clusters remain available after each run. The CLI closes its connection;
cluster creation, scaling and shutdown remain with the sidebar and its
backend. Interrupting an attached run leaves unfinished outputs uncommitted:
stop the cluster and inspect them before restoring or rerunning. Direct-mode
recipes receive the invoking shell's environment while retaining the workers'
own host and job settings; containers retain their existing environment policy.

Local and SLURM clusters also support containerized projects. Using several
worker hosts requires a shared image store such as `podman-hpc`. Dask Gateway
supports direct-mode projects in the same JupyterHub image; install its
optional client and use the hub's Gateway configuration:

```bash
uv tool install 'lightcone-cli[gateway]'
```

## License

BSD 3-Clause — see [LICENSE](LICENSE) for details.
