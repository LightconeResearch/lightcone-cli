# CLI Reference

The `prism` CLI is the main entry point for all project and execution operations.

For a visual overview of how each command and skill flows — inputs, steps, outputs, and hooks — see the **[Command Schematics](schematics.md)** reference.

## Global behaviour

- Any command (except `setup`, `target`, `update`, `eval`) triggers an auto-setup wizard if `~/.prism/config.yaml` does not exist yet.
- Commands that operate on a project require `astra.yaml` to be present in the current directory.
- Target resolution order: `--target` flag › `.prism/prism.yaml` › `~/.prism/config.yaml` › `"local"`.

## Commands at a glance

| Command | Purpose |
|---------|---------|
| [`prism init`](init.md) | Create a new ASTRA project (or add Prism to an existing one) |
| [`prism run`](run.md) | Materialise outputs via Dagster |
| [`prism build`](build.md) | Build container images from `Containerfile` specs |
| [`prism status`](status.md) | Show materialisation status table |
| [`prism dev`](dev.md) | Launch the Dagster webserver UI |
| [`prism setup`](setup.md) | Configure execution targets (interactive wizard) |
| [`prism target`](target.md) | Manage project targets |
| [`prism update`](update.md) | Upgrade the package and sync plugin files |

## Global options

```
prism [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.
```
