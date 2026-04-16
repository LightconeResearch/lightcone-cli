# Site Registry

See the full API reference at [api/site_registry.md](../api/site_registry.md).

## How site detection works

When creating a target via `prism target add`, the user provides a hostname. `detect_site()` checks the hostname against `hostname_patterns` for each registered site:

```python
for site_key, site in SITE_DEFAULTS.items():
    if site_key in normalized_hostname:
        return site_key
    for pattern in site.get("hostname_patterns", []):
        if pattern in normalized_hostname:
            return site_key
```

## Site defaults applied to new targets

When a site is detected, `get_site_defaults()` fills in:

- `backend`: always `"slurm"` for HPC sites
- `connection.hostname`: the canonical hostname
- `container_runtimes`: list of supported runtimes (user can pick)
- `node_types`: dict of node types with descriptions, constraint values, and container flags
- `qos_options`: dict of QOS with descriptions and defaults
- `resource_limits`: default caps for max nodes, walltime, and concurrent jobs
- `scratch_paths`: HPC scratch paths used as Claude Code `Edit()` deny rules

## Perlmutter specifics

| Node type | Constraint | Account suffix |
|-----------|-----------|---------------|
| GPU (A100 40GB) | `gpu` | `_g` |
| GPU (A100 80GB) | `gpu&hbm80g` | `_g` |
| CPU only | `cpu` | (none) |

The `_g` account suffix is applied automatically by `resolve_account()` for GPU jobs.

Scratch paths guarded against accidental writes:
- `//pscratch/**`
- `//global/cscratch1/**`
- `//global/cfs/cdirs/**`

## Adding a new site

See [API reference: site_registry](../api/site_registry.md#adding-a-new-site).
