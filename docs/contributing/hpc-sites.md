# Adding an HPC Site

HPC site defaults live in `src/prism/dagster/site_registry.py`. See the full reference at [API: site_registry](../api/site_registry.md#adding-a-new-site).

## Minimal example

```python
SITE_DEFAULTS["my_cluster"] = {
    "hostname_patterns": ["mycluster.example.org", "mycluster"],
    "display_name": "My HPC Cluster",
    "backend": "slurm",
    "connection": {
        "hostname": "mycluster.example.org",
    },
    "node_types": {
        "gpu": {
            "description": "GPU nodes",
            "constraint": "gpu",
            "container_flags": [],
        },
    },
    "qos_options": {
        "normal": {"description": "Standard priority", "default": True},
    },
    "container_runtimes": ["podman"],
    "resource_limits": {
        "max_nodes": 4,
        "max_walltime_minutes": 240,
        "max_concurrent_jobs": 4,
    },
}
```

## Testing

After adding a site, verify it appears in the setup wizard:

```bash
prism setup   # → select "Configure HPC" → your site should appear
```

And that `detect_site()` recognises the hostname:

```python
from prism.dagster.site_registry import detect_site
assert detect_site("mycluster.example.org") == "my_cluster"
```

## Documentation

Add a row to the sites table in `docs/hpc/site-registry.md`.
