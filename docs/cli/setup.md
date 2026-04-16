# prism setup

Set up execution targets (interactive first-time experience).

## Synopsis

```
prism setup [OPTIONS]
```

## Description

`prism setup` configures connection details and container runtimes for execution backends. Settings are stored at the user level in `~/.prism/targets/` and referenced by projects via `.prism/prism.yaml`.

If `~/.prism/config.yaml` does not exist, the full setup wizard runs. If it already exists, a management menu is shown instead.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--list` | — | List all configured targets |
| `--show NAME` | — | Show a target's configuration |
| `--default NAME` | — | Change the default target |

## Setup wizard flow

1. **Configure HPC?** — If yes, select a site (Perlmutter, custom SLURM) or local Docker.
2. For known HPC sites: username, account/allocation, container runtime, target name.
3. For custom SLURM: cluster name, hostname, username, account, optional container runtime.
4. A `local` target is always created in addition to any HPC target.
5. The default target is saved to `~/.prism/config.yaml`.

## Management menu

When `~/.prism/config.yaml` already exists, running `prism setup` shows a menu:

```
1. Change permission level
2. Change extraction model
3. Add a target
4. Edit a target
5. Change default target
6. Re-run setup wizard
7. Exit
```

## Storage

| File | Contents |
|------|----------|
| `~/.prism/config.yaml` | `default_target`, `default_permission_tier`, `extraction_model` |
| `~/.prism/targets/{name}.yaml` | Per-target connection, scheduler, runtime config |

## Examples

```bash
prism setup                          # interactive wizard (first time) or menu
prism setup --list                   # list configured targets
prism setup --show perlmutter-gpu    # show a target's config
prism setup --default local          # change default target
```
