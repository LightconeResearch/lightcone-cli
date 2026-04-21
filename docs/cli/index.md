# CLI Reference

The `lc` CLI is the main entry point for all project and execution operations.

For a visual overview of how each command and skill flows — inputs, steps, outputs, and hooks — see the **[Command Schematics](schematics.md)** reference.

## Global behaviour

- Any command (except `setup`, `target`, `update`, `eval`) triggers an auto-setup wizard if `~/.lightcone/config.yaml` does not exist yet.
- Commands that operate on a project require `astra.yaml` to be present in the current directory.
- Target resolution order: `--target` flag › `.lightcone/lightcone.yaml` › `~/.lightcone/config.yaml` › `"local"`.

## Commands at a glance

| Command | Purpose |
|---------|---------|
| [`lc init`](init.md) | Create a new ASTRA project (or add lightcone-cli to an existing one) |
| [`lc run`](run.md) | Materialise outputs via Dagster |
| [`lc build`](build.md) | Build container images from `Containerfile` specs |
| [`lc status`](status.md) | Show materialisation status table |
| [`lc dev`](dev.md) | Launch the Dagster webserver UI |
| [`lc setup`](setup.md) | Configure execution targets (interactive wizard) |
| [`lc target`](target.md) | Manage project targets |
| [`lc update`](update.md) | Upgrade the package and sync plugin files |

## Global options

```
lc [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.
```
