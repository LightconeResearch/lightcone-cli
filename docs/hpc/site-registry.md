# Site Registry

See the full API reference at [api/site_registry.md](../api/site_registry.md).

## How site detection works

When adding a cluster via `lc cluster add`, the user provides a hostname. `detect_site()` checks the hostname against `hostname_patterns` for each registered site:

```python
for site_key, site in SITE_DEFAULTS.items():
    if site_key in normalized_hostname:
        return site_key
    for pattern in site.get("hostname_patterns", []):
        if pattern in normalized_hostname:
            return site_key
```

## Site defaults applied to new clusters

When a site is detected, `get_site_defaults()` fills in:

- `container_runtime`: the container CLI used on compute nodes (e.g. `podman-hpc`)
- `suggested_options`: QoS and constraint choices shown during `lc cluster add`
- `slurm.scratch_root`: default scratch filesystem root for sbatch scripts
- `slurm.default_qos` / `slurm.default_walltime`: sane defaults for the cluster YAML
- `slurm.worker_init_template`: shell snippet prepended to the sbatch worker init block
- `scratch_paths`: HPC scratch paths used as Claude Code `Edit()` deny rules

## Perlmutter specifics

| Node type | Constraint | Notes |
|-----------|-----------|-------|
| CPU only | `cpu` | 128 cores/node |
| GPU (A100 40GB) | `gpu` | 4 GPUs/node |
| GPU (A100 80GB) | `gpu&hbm80g` | 256 nodes |

Scratch paths guarded against accidental writes:
- `//pscratch/**`
- `//global/cscratch1/**`
- `//global/cfs/cdirs/**`

## Adding a new site

See [API reference: site_registry](../api/site_registry.md#adding-a-new-site).
