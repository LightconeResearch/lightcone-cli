# CLI Reference

The `lc` CLI is the main entry point for all project and execution operations.

For a visual overview of how each command flows — inputs, steps, outputs,
and hooks — see the **[Command Schematics](schematics.md)** reference.

## Global behaviour

- Most commands require `astra.yaml` in the current directory; exceptions:
  `pilot`, `init`, `update`.
- Pilot resolution order: `--pilot NAME` flag → project's
  `.lightcone/lightcone.yaml: pilot:` → the single configured pilot in
  `~/.lightcone/pilots/` → fall back to local execution.
- No user-level config file: pilots, cluster cache, and worker venvs all
  live under `~/.lightcone/{pilots,cache,envs}/`.

## Commands at a glance

| Command | Purpose |
|---------|---------|
| [`lc init`](init.md) | Create a new ASTRA project (or add lightcone-cli to an existing one) |
| [`lc run`](run.md) | Materialise outputs locally or via a pilot |
| [`lc build`](build.md) | Build container images from `Containerfile` specs |
| [`lc status`](status.md) | Show materialisation status table |
| [`lc dev`](dev.md) | Launch the Dagster webserver UI |
| [`lc pilot`](pilot.md) | Manage long-lived SLURM Dask pilots |
| [`lc update`](update.md) | Upgrade the package and sync plugin files |

## Global options

```
lc [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.
```
