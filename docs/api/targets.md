# prism.dagster.targets

User-level target configuration management. Targets are stored as YAML files in `~/.prism/targets/`.

---

## `get_targets_dir() → Path`

Returns `~/.prism/targets/`.

---

## `list_targets() → list[str]`

Returns sorted list of target names (stems of `*.yaml` files in the targets directory).

---

## `load_target(name) → dict | None`

Loads a saved target by name. Returns `None` if the file does not exist.

---

## `save_target(name, config) → Path`

Saves `config` to `~/.prism/targets/{name}.yaml`. Creates the directory if needed. Returns the path.

---

## `get_config_path() → Path`

Returns `~/.prism/config.yaml`.

---

## `load_user_config() → dict`

Loads `~/.prism/config.yaml`. Returns `{}` if it does not exist.

---

## `save_user_config(config) → Path`

Saves `config` to `~/.prism/config.yaml`. Creates parent directories if needed.

---

## File layout

```
~/.prism/
├── config.yaml           # default_target, default_permission_tier, extraction_model
└── targets/
    ├── local.yaml
    ├── perlmutter-gpu.yaml
    └── custom-cluster.yaml
```

## User config fields

| Key | Type | Description |
|-----|------|-------------|
| `default_target` | `str` | Used when no `--target` flag or `.prism/prism.yaml` |
| `default_permission_tier` | `str` | `yolo`, `recommended`, or `minimal` |
| `extraction_model` | `str` | Model for `/prism-new` literature extraction (`""` = inherit) |
