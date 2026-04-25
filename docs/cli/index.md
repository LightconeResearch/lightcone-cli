# CLI Reference

The `lc` CLI is the main entry point for all project and execution operations.

For a visual overview of how each command flows — inputs, steps, outputs,
and hooks — see the **[Command Schematics](schematics.md)** reference.

## Global behaviour

- Most commands require `astra.yaml` in the current directory; exceptions:
  `cluster`, `init`, `update`.
- Cluster resolution order: `--cluster NAME` flag → project's
  `.lightcone/lightcone.yaml: cluster:` → the single configured cluster in
  `~/.lightcone/clusters/` → fall back to local execution.
- No user-level config file: clusters, cluster cache, and worker venvs all
  live under `~/.lightcone/{clusters,cache,envs}/`.

## Commands at a glance

| Command | Purpose |
|---------|---------|
| [`lc init`](init.md) | Create a new ASTRA project (or add lightcone-cli to an existing one) |
| [`lc run`](run.md) | Materialise outputs locally or via a cluster |
| [`lc build`](build.md) | Build container images from `Containerfile` specs |
| [`lc status`](status.md) | Show materialisation status table |
| [`lc dev`](dev.md) | Launch the Dagster webserver UI |
| [`lc cluster`](cluster.md) | Manage long-lived SLURM Dask clusters |
| [`lc update`](update.md) | Upgrade the package and sync plugin files |

## Global options

```
lc [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.
```
