# prism.dagster.site_registry

Known HPC site defaults. Used by `prism setup` and `prism target add` to auto-populate scheduler configuration.

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

## `resolve_account(site_key, account, constraint) → str`

Applies site-specific account suffixes. For example, on Perlmutter GPU jobs need `m4031_g` instead of `m4031` — `resolve_account("perlmutter", "m4031", "gpu")` returns `"m4031_g"`.

---

## `get_site_scratch_deny_rules(site_key) → list[str]`

Returns Claude Code `Edit()` deny rules for the site's scratch paths. For Perlmutter:

```python
["Edit(//pscratch/**)", "Edit(//global/cscratch1/**)", "Edit(//global/cfs/cdirs/**)"]
```

These are merged into `.claude/settings.json` by `prism init` when a non-local target is configured.

---

## Adding a new site

Add an entry to `SITE_DEFAULTS` in `site_registry.py`:

```python
SITE_DEFAULTS["frontier"] = {
    "hostname_patterns": ["frontier.olcf.ornl.gov", "frontier"],
    "display_name": "OLCF Frontier",
    "backend": "slurm",
    "connection": {"hostname": "frontier.olcf.ornl.gov"},
    "node_types": {
        "gpu": {
            "description": "AMD MI250X GPU",
            "constraint": "gpu",
            "container_flags": [],
        },
    },
    "qos_options": {
        "normal": {"description": "Standard priority", "default": True},
    },
    "container_runtimes": ["singularity"],
    "resource_limits": {
        "max_nodes": 8,
        "max_walltime_minutes": 120,
        "max_concurrent_jobs": 4,
    },
    "scratch_paths": ["//lustre/orion/**"],
}
```

## Currently registered sites

| Key | Display name | Hostname |
|-----|-------------|---------|
| `perlmutter` | NERSC Perlmutter | `perlmutter.nersc.gov` |
| `local` | Local | — |
