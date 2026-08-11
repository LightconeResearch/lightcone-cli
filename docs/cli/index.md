# CLI Reference

The `lc` CLI is a thin wrapper around the engine. The user-facing
surface is small on purpose — `astra.yaml` carries the analysis
description, and the CLI is the durable, scriptable way to execute
and audit it.

## Global behavior

- `~/.lightcone/config.yaml` is created automatically on first use of
  any `lc` command. You do not need to create it manually.
- All commands except `init` walk up from the cwd looking for
  `astra.yaml`. If none is found, the command errors out.

## Commands

| Command | Purpose |
|---------|---------|
| [`lc init`](init.md) | Scaffold a new ASTRA project (`astra.yaml`, `Containerfile`, `.lightcone/`, MyST report template, optional venv & git). |
| [`lc run`](run.md) | Generate the Snakefile and dispatch through Snakemake + Dask. |
| [`lc build`](build.md) | Build container images declared in `astra.yaml`. |
| [`lc status`](status.md) | Manifest-driven status report. No Snakemake import needed. |
| [`lc verify`](verify.md) | Recompute hashes, walk the input chain, surface tampering. |
| [`lc export`](export.md) | Emit interoperable bundles (Workflow Run RO-Crate) for publication. |

## Global options

```text
lc [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.
```

## Removed commands

For historical context: `lc dev`, `lc setup`, `lc target`, and `lc update` no
longer exist as explicit commands. See the removal pages for details.
