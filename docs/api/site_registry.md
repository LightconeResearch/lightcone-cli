# lightcone.engine.site_registry

Known-site defaults. One question — *which site are we on?* — answered in
one place, so "the site asks for X" features don't each re-derive it.

Source: `src/lightcone/engine/site_registry.py`.

## Who calls it

| Caller | What it reads |
|--------|---------------|
| `lc init` (`cli/commands.py`) | `display_name` + `scratch_root`, to surface where `lc run` will keep its state |
| [`engine.scratch.resolve_scratch_root`](scratch.md) | `scratch_root`, as the third step of the resolution chain |
| `engine.container._site_preferred_runtime` | `container_runtime`, moved to the front of the detection order — and the only way `kubernetes` is ever selected automatically |

## `SITE_DEFAULTS`

A dict of site key → declared defaults. Recognised keys:

| Key | Meaning |
|-----|---------|
| `hostname_patterns` | Substrings matched against the hostname by `detect_site()`. |
| `env_markers` | Env var names that must *all* be set for `detect_site_from_env()` to match. |
| `display_name` | Human-readable name (`HostSite.display_name`). |
| `backend` | `slurm` / `kubernetes` / `local`. Sites with `backend: local` are skipped by hostname detection. |
| `container_runtime` | The runtime `engine.container` prefers here. |
| `scratch_root` | Shell expression (`$SCRATCH`, `$HOME`) expanded at run time. |
| `scratch_paths` | Shared filesystems the agent shouldn't edit; `get_site_scratch_deny_rules()` turns them into `Edit(...)` rules. |
| `suggested_options`, `cache_key_overrides`, `connection` | Perlmutter scheduler detail. |

Three sites ship today:

- **`perlmutter`** — matched by hostname (`perlmutter`, `saul`).
  `container_runtime: podman-hpc`, `scratch_root: $SCRATCH` (NERSC's
  `$HOME` and CFS are DVS-mounted and silently swallow `flock`).
- **`jupyterhub`** — a JupyterHub deployment with Dask Gateway. Pod
  hostnames are meaningless, so it declares **no** hostname patterns and
  is matched by `env_markers: ["DASK_GATEWAY__ADDRESS"]` instead.
  `container_runtime: kubernetes` (the worker pod *is* the container —
  see [api/container](container.md)); `scratch_root: $HOME`, because the
  `.snakemake` state the driver symlinks into scratch has to live on the
  NFS home every worker pod mounts, not in a pod-local `/tmp`.
- **`local`** — the fallback; no detection, no declared runtime.

## `detect_current_site() → HostSite`

The entry point for the rest of the codebase. **Env markers win over
hostname patterns** — a pod's hostname is noise, the injected env is the
signal:

```python
key = detect_site_from_env() or detect_site(socket.gethostname())
```

Returns a falsy `HostSite` (`key is None`, empty defaults) when nothing
matches — it never raises, so callers can treat it as an optional lookup.

### `HostSite` (frozen dataclass)

```python
@dataclass(frozen=True)
class HostSite:
    key: str | None
    defaults: Mapping[str, Any]

    def __bool__(self) -> bool: ...        # True when a site matched
    @property
    def display_name(self) -> str: ...     # declared name, else key, else "unknown"
    def get(self, name, default=None): ... # read a declared field
```

Typical use:

```python
from lightcone.engine.site_registry import detect_current_site

site = detect_current_site()
if site:
    print(site.display_name, site.get("scratch_root"))
```

## Other functions

### `detect_site(hostname_or_name) → str | None`

Substring match of a hostname (or a site name typed by a user) against
each site's key and `hostname_patterns`. Sites with `backend: local` are
skipped, so a machine literally named `local` doesn't match.

### `detect_site_from_env() → str | None`

Returns the first site whose declared `env_markers` are *all* present and
non-empty in the environment. Sites with no markers never match here.

### `get_site_defaults(site_key) → dict | None`

Raw defaults dict for a key.

### `list_known_sites() → list[tuple[str, str]]`

`(site_key, display_name)` for every entry.

### `get_site_scratch_deny_rules(site_key) → list[str]`

`Edit(<path>)` rules built from the site's `scratch_paths`. Not wired
into `lc init` today — the equivalent patterns are listed under `ask` in
`PERMISSION_TIERS` (`src/lightcone/cli/commands.py`).

## Adding a site

Append an entry to `SITE_DEFAULTS`. Declare `hostname_patterns` for a
machine you can recognise by name, or `env_markers` for a deployment you
can only recognise by the environment it injects. See
[Adding an HPC Site](../contributing/hpc-sites.md).
