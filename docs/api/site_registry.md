# lightcone.engine.site_registry

Known HPC site defaults. Used by `lc cluster add` to auto-populate scheduler configuration.

---

## `detect_site(hostname_or_name) → str | None`

Returns the site key (e.g. `"perlmutter"`) if the hostname matches any registered site's `hostname_patterns`. Returns `None` otherwise.

---

## `get_site_defaults(site_key) → dict | None`

Returns the full defaults dict for a site, or `None` if not registered.

---

## `list_known_sites() → list[tuple[str, str]]`

Returns `[(site_key, display_name), ...]` for all sites in `SITE_DEFAULTS`.

---

## `get_site_scratch_deny_rules(site_key) → list[str]`

Returns Claude Code `Edit()` deny rules for the site's scratch paths. For Perlmutter:

```python
["Edit(//pscratch/**)", "Edit(//global/cscratch1/**)", "Edit(//global/cfs/cdirs/**)"]
```

These are merged into `.claude/settings.json` by `lc init` when an HPC cluster is configured.

---

## Adding a new site

Add an entry to `SITE_DEFAULTS` in `src/lightcone/engine/site_registry.py`:

```python
SITE_DEFAULTS["frontier"] = {
    "hostname_patterns": ["frontier.olcf.ornl.gov", "frontier"],
    "display_name": "OLCF Frontier",
    "container_runtime": "singularity",
    "suggested_options": {
        "qos": {
            "default": "normal",
            "choices": {
                "normal": "Standard priority",
            },
        },
        "constraint": {
            "default": "gpu",
            "choices": {
                "gpu": "AMD MI250X GPU nodes",
            },
        },
    },
    # SLURM substrate defaults
    "slurm": {
        "scratch_root": "$MEMBERWORK/prj123",
        "default_qos": "normal",
        "default_walltime": "2h",
        "worker_init_template": (
            "module load python\n"
            "source $HOME/.lightcone/envs/frontier/bin/activate\n"
        ),
    },
    "scratch_paths": ["//lustre/orion/**"],
}
```

## Currently registered sites

| Key | Display name | Hostname |
|-----|-------------|---------|
| `perlmutter` | NERSC Perlmutter | `perlmutter.nersc.gov` |
