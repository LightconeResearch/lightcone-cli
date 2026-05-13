# lightcone-cli

**lightcone-cli** is Lightcone Research's agentic execution layer for
**ASTRA** (Agentic Schema for Transparent Research Analysis). It ships
the `lc` executable, a small set of Claude Code skills, and the
provenance/integrity machinery that ties an `astra.yaml` spec to a tree
of materialized outputs.

This site has two halves.

## I'm a researcher and I want to use this thing

Start at the [User Guide](user/index.md). Friendly, step-by-step, with
worked examples. You will not need to read any Python.

The shortest possible path:

=== "uv"
    ```bash
    uv tool install lightcone-cli
    lc init my-analysis && cd my-analysis
    claude                                # then, inside Claude Code: /lc-new
    ```

=== "pip"
    ```bash
    pip install lightcone-cli
    lc init my-analysis && cd my-analysis
    claude                               # then, inside Claude Code: /lc-new
    ```

## I work on lightcone-cli

Welcome — keep reading. The rest of this page is a fast tour for
contributors and maintainers; deep dives live in the sub-trees of the
nav.

---

## Two packages, one toolchain

| Layer | Package | Role |
|-------|---------|------|
| **ASTRA** | `astra-tools` | Pure specification: schema, validation, prior insights & findings, evidence verification helpers, the `astra` CLI. |
| **lightcone-cli** | `lightcone-cli` | Agentic layer: project scaffolding, Snakemake-based execution, Dask cluster management, container builds, Claude Code skills. |

`lightcone-cli` depends on `astra-tools`. The `astra` CLI handles the
spec itself (validation, paper management, evidence verification); the
`lc` CLI handles execution and the agent surface.

## Repository at a glance

```text
src/lightcone/                  # PEP 420 namespace package — NO __init__.py
├── cli/                        # Click surface
│   ├── __init__.py             # exposes main()
│   ├── commands.py             # init, run, status, verify, build, export
│   └── plugin.py               # plugin source-dir discovery
├── engine/                     # execution substrate
│   ├── manifest.py             # write_manifest, sha256_dir, code_version
│   ├── snakefile.py            # generate .lightcone/Snakefile from astra.yaml
│   ├── container.py            # docker/podman/podman-hpc build + recipe wrap
│   ├── dask_cluster.py         # cluster lifecycle (local/SLURM/external)
│   ├── status.py               # manifest-driven status walker (no Snakemake)
│   ├── verify.py               # recompute hashes, walk the chain
│   ├── tree.py                 # sub-analysis tree helpers
│   ├── validation.py           # post-recipe output sanity checks
│   └── site_registry.py        # vestigial; not imported by active code
└── eval/                       # evaluation harness for the agent loop
    ├── cli.py harness.py sandbox.py graders.py build.py report.py models.py

src/snakemake_executor_plugin_dask/   # Snakemake executor → dask.distributed

claude/lightcone/               # Claude Code plugin (force-included into the wheel)
├── skills/                     # lc-new, lc-build, lc-verify, lc-migrate, lc-feedback
├── agents/                     # lc-extractor (literature subagent)
├── guides/                     # astra-reference, lightcone-cli-reference, ui-brand
├── templates/                  # project CLAUDE.md template
└── scripts/                    # session hooks (bash): venv, validate-on-save, …

tests/                          # pytest, mirrors src/
pyproject.toml                  # hatchling + hatch-vcs; ASTRA + Snakemake as deps
```

The `lightcone.*` namespace is a PEP 420 implicit namespace package.
**Do not add `src/lightcone/__init__.py`** — that would turn it into a
regular package and break coexistence with future sibling distributions
(`lightcone-ui`, etc.). Any new `lightcone-*` package must live under
`src/lightcone/<name>/` and ship only its own subpackage.

## Execution flow

```text
astra.yaml ── snakefile.generate() ──► .lightcone/Snakefile + .lightcone/snakefile-config.json
                                              │
                                       snakemake -s … -d … --executor dask
                                              │
                       ┌──────────────────────┼──────────────────────┐
                       │                      │                      │
                  DAG resolution         per-rule run:           dask scheduler
                  (Snakemake)            shell(recipe)           (LocalCluster /
                                         + write_manifest()       SLURM-srun /
                                                                  external)
                       │
                       └─► results/<u>/<o>/data
                           results/<u>/<o>/.lightcone-manifest.json
```

What Snakemake owns (we don't write it): DAG construction, topological
execution, parallelism, dry-run, locking, retry, log capture,
per-rule resources, `--rerun-triggers` for staleness detection.

What we own: a Snakefile generator, the manifest layer (write/read/verify),
a status walker, a verify routine, the Dask cluster manager, the
container-runtime layer, and a Snakemake executor plugin that submits
each rule to a Dask scheduler.

## What every materialized output gets

A sidecar `.lightcone-manifest.json` next to its data, recording:

- `code_version` = `sha256(recipe + container_image + decisions)`
- `data_version` = `sha256_dir(output_dir)` excluding the manifest itself
- `input_versions` for each declared input (chained data_version when the
  input is another materialized output, `mtime-size` or `sha256` for
  external files)
- `container_image`, `recipe`, `decisions`, `git_sha`, `lc_version`,
  `host`, `slurm_job_id`, `finished_at`

`lc verify` recomputes `data_version` and walks the chain. Failures
surface as `tampered_data`, `broken_chain`, or `missing_manifest`. `lc
status` reads only manifests — works offline, no Snakemake or DB needed.

## Development setup

```bash
just install        # uv sync --all-groups
just test           # uv run pytest
just lint           # ruff + mypy
just docs-serve     # live docs preview
```

## Where to read next

- [Architecture](architecture.md) — the full execution and integrity story
- [CLI Reference](cli/index.md) — every command currently shipped
- [Python API](api/index.md) — the engine modules
- [Skills](skills/index.md) — what each `/lc-*` skill is supposed to do
- [Contributing](contributing/setup.md) — getting the dev loop running
