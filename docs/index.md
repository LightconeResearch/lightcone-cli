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
- [Skills](skills/index.md) — what each `/lc-*` skill does (including the `/lc-from-*` family)
- [Contributing](contributing/setup.md) — getting the dev loop running
