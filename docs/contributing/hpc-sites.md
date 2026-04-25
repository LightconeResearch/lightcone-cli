# Adding an HPC Site

HPC site defaults live in `src/lightcone/engine/site_registry.py`. See the full reference at [API: site_registry](../api/site_registry.md#adding-a-new-site).

## Minimal example

```python
SITE_DEFAULTS["my_cluster"] = {
    "hostname_patterns": ["mycluster.example.org", "mycluster"],
    "display_name": "My HPC Cluster",
    "container_runtime": "singularity",
    "suggested_options": {
        "qos": {
            "default": "normal",
            "choices": {
                "normal": "Standard priority",
                "debug":  "Short interactive jobs",
            },
        },
        "constraint": {
            "default": "gpu",
            "choices": {
                "gpu": "GPU nodes",
                "cpu": "CPU-only nodes",
            },
        },
    },
    # SLURM substrate defaults
    "slurm": {
        "scratch_root": "$SCRATCH",
        "default_qos": "normal",
        "default_walltime": "4h",
        "worker_init_template": (
            "module load python\n"
            "source $HOME/.lightcone/envs/my_cluster/bin/activate\n"
        ),
    },
    "scratch_paths": ["//scratch/**"],
}
```

## Testing

After adding a site, verify that `detect_site()` recognises the hostname:

```python
from lightcone.engine.site_registry import detect_site
assert detect_site("mycluster.example.org") == "my_cluster"
```

And that `lc cluster add` surfaces the site name and pre-populates defaults:

```bash
lc cluster add my_cluster   # → wizard should detect the site and fill in defaults
```

## Documentation

Add a row to the sites table in `docs/hpc/site-registry.md`.
