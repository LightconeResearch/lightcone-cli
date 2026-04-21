# lc setup

Set up execution targets (interactive first-time experience).

## Synopsis

```
lc setup [OPTIONS]
```

## Description

`lc setup` configures connection details and container runtimes for execution backends. Settings are stored at the user level in `~/.lightcone/targets/` and referenced by projects via `.lightcone/lightcone.yaml`.

If `~/.lightcone/config.yaml` does not exist, the full setup wizard runs. If it already exists, a management menu is shown instead.

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
5. The default target is saved to `~/.lightcone/config.yaml`.

## Management menu

When `~/.lightcone/config.yaml` already exists, running `lc setup` shows a menu:

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
| `~/.lightcone/config.yaml` | `default_target`, `default_permission_tier`, `extraction_model` |
| `~/.lightcone/targets/{name}.yaml` | Per-target connection, scheduler, runtime config |

## Examples

```bash
lc setup                          # interactive wizard (first time) or menu
lc setup --list                   # list configured targets
lc setup --show perlmutter-gpu    # show a target's config
lc setup --default local          # change default target
```
