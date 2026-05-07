# CLI Reference

The `lc` CLI is a thin wrapper around the engine. The user-facing
surface is small on purpose — the heavy lifting happens through Claude
Code skills, with the CLI as the durable, scriptable backstop.

## Global behavior

- All commands except `setup`, `init`, and `eval` require
  `~/.lightcone/config.yaml` to exist. If it doesn't, the command
  errors out telling you to run `lc setup`.
- All commands except `setup` and `init` walk up from the cwd looking
  for `astra.yaml`. If none is found, the command errors out.

## Commands

| Command | Purpose |
|---------|---------|
| [`lc init`](init.md) | Scaffold a new ASTRA project (`astra.yaml`, `.claude/`, `.lightcone/`, optional venv & git). |
| [`lc run`](run.md) | Generate the Snakefile and dispatch through Snakemake + Dask. |
| [`lc build`](build.md) | Build container images declared in `astra.yaml`. |
| [`lc status`](status.md) | Manifest-driven status report. No Snakemake import needed. |
| [`lc verify`](verify.md) | Recompute hashes, walk the input chain, surface tampering. |
| [`lc export`](export.md) | Emit interoperable bundles (Workflow Run RO-Crate) for publication. |
| [`lc setup`](setup.md) | Write a minimal `~/.lightcone/config.yaml`. |

## Global options

```
lc [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.
```

## Removed commands

For historical context: `lc dev`, `lc target`, and `lc update` no
longer exist; `lc eval` is partially wired (its sub-commands are
defined in `src/lightcone/eval/cli.py` but the group is not registered
on `main`, so `lc eval` will fail with "No such command"). See the
removal pages for details.
