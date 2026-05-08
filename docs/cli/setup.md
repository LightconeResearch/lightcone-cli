# lc setup (removed)

The `lc setup` command no longer exists. The global configuration file
(`~/.lightcone/config.yaml`) is now created automatically the first time
you run any `lc` command — there is nothing to run manually.

## Global config file

`~/.lightcone/config.yaml` is created with these defaults on first use:

```yaml
container:
  runtime: auto
```

Edit the file by hand to change the container runtime:

```bash
$EDITOR ~/.lightcone/config.yaml
```

## `container.runtime` values

| Value | Behavior |
|-------|----------|
| `auto` (default) | Pick the first usable runtime in `(podman, docker, podman-hpc)`. Skips docker if its daemon isn't reachable. Falls back to `none` if nothing's available. |
| `docker` | Pin to docker. Errors at run time if the binary isn't on PATH. |
| `podman` | Pin to podman. |
| `podman-hpc` | Pin to podman-hpc — typical on NERSC Perlmutter login nodes. |
| `none` | Explicit opt-out from containers. Recipes run on the host. |

Setting `runtime: none` explicitly silences the provenance warning
that `lc run` shows when `auto` falls back to `none`. (See
[`lc run`](run.md#provenance-gotcha).)
