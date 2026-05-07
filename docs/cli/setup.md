# lc setup

Write a minimal global configuration at `~/.lightcone/config.yaml`. Run
once after install. There is no wizard, no prompt, no flags.

## Synopsis

```
lc setup
```

## What it does

If `~/.lightcone/config.yaml` already exists, prints the path and exits
with no changes. Otherwise creates it with:

```yaml
container:
  runtime: auto
```

That's the entire file today. The rest of lightcone-cli requires
`~/.lightcone/config.yaml` to *exist* (so it knows you've at least
opted in once); the file's contents control container runtime
resolution.

## `container.runtime` values

| Value | Behavior |
|-------|----------|
| `auto` (default) | Pick the first usable runtime in `(podman, docker, podman-hpc)`. Skips docker if its daemon isn't reachable. Falls back to `none` if nothing's available. |
| `docker` | Pin to docker. Errors at run time if the binary isn't on PATH. |
| `podman` | Pin to podman. |
| `podman-hpc` | Pin to podman-hpc — typical on NERSC Perlmutter login nodes. |
| `none` | Explicit opt-out from containers. Recipes run on the host. |

Edit the file by hand to change runtimes:

```bash
$EDITOR ~/.lightcone/config.yaml
```

Setting `runtime: none` explicitly silences the provenance warning
that `lc run` shows when `auto` falls back to `none`. (See
[`lc run`](run.md#provenance-gotcha).)

## Removed flags

The `--list`, `--show NAME`, and `--default NAME` options are gone —
they belonged to the now-removed target system. See
[`lc target`](target.md) for the redirect.
