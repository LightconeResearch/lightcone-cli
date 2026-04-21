# lc dev

Launch the Dagster webserver UI for the current project.

## Synopsis

```
lc dev [OPTIONS]
```

## Description

Starts `dagster-webserver` with the current project's asset definitions loaded. Opens the Dagster UI at `http://localhost:{port}` showing the asset graph, run history, and materialisation status.

The webserver generates a temporary Python file that calls `build_definitions()` for the specified universe and passes it to `dagster-webserver -f`. The file is deleted on exit.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--port`, `-p` | `3000` | Port for the Dagster webserver |
| `--universe`, `-u` | `baseline` | Universe to load asset definitions for |

## Examples

```bash
lc dev
lc dev --port 8080
lc dev --universe experiment1
```

## Notes

- Container builds are always skipped in `dev` mode (`no_build=True`).
- The Dagster instance uses `results/.dagster/` for event storage, so run history is shared with `lc run`.
- Press `Ctrl+C` to stop the webserver.
