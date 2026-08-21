# CLI Reference

The `lc` CLI is a thin wrapper around the engine. The user-facing
surface is small on purpose — `astra.yaml` carries the analysis
description, and the CLI is the durable, scriptable way to execute
and audit it.

## Global behavior

- **The current directory is the project.** Every command except
  `init` assumes it is invoked from the project root; there is no
  walk-up and no global configuration. Outside a project, a command
  errors cleanly.
- **Nothing waits on a human.** No command prompts or opens an
  interactive shell — every verb runs to completion on its arguments
  alone, which is what makes the CLI safe to drive from scripts and
  agents.
- **Refusals carry their remedy.** When a command refuses (a dirty
  tree, a login node, a missing image), the message names the exact
  command that fixes it.

## Commands

| Command | Purpose |
|---------|---------|
| [`lc init`](init.md) | Converge a directory into a Lightcone project (idempotent). |
| [`lc materialize`](materialize.md) | Make the analysis's outputs; commit each one as it lands. |
| [`lc status`](status.md) | Report the state of every output. Reads only; always exits 0. |
| [`lc run`](run.md) | Run an ad-hoc command in the project environment, under isolation. |
| [`lc build`](build.md) | Containerized projects: build the image and commit it. |

## Global options

```text
lc [OPTIONS] COMMAND [ARGS]...

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.
```

## Exit codes

- `0` — the command did what it says.
- `1` — a refusal or a failure, with the reason on stderr. For
  `lc materialize --check` and `lc init --check`, exit 1 means "work
  would be done" — the gate form scripts branch on.
- `lc run` is a proxy: it exits with the command's own code
  (`128 + N` for a signal), so pipelines read it exactly as they would
  the bare command.

Every verb with a report takes `--json` for the machine-readable form;
each verb's page shows its shape.
