# prism.cli

Click CLI entry point. Implements all `prism` commands as Click decorated functions.

---

## Entry point: `main`

```python
@click.group()
def main(): ...
```

The root Click group. Auto-triggers `setup` if `~/.prism/config.yaml` does not exist (except for `setup`, `target`, `update`, `eval` subcommands).

---

## `PERMISSION_TIERS`

```python
PERMISSION_TIERS: dict[str, dict[str, list[str]]]
```

Defines Claude Code permission tiers for `.claude/settings.json`. See [Architecture: Permission tiers](../architecture.md#permission-tiers) for the full description.

Keys: `"yolo"`, `"recommended"`, `"minimal"`.

---

## Plugin discovery: `_get_plugin_source_dir() → Path | None`

Finds the Prism plugin source directory. Checks:

1. **Bundled** (installed package): `prism/claude/prism/`
2. **Development** (repo checkout): `{repo_root}/claude/prism/`

Returns `None` if neither exists.

---

## Path helpers

### `_find_prism_yaml(project_path) → Path | None`

Finds `.prism/prism.yaml`, falling back to `prism.yaml` in root for backward compatibility.

### `_find_dagster_yaml(project_path) → Path | None`

Finds `.prism/dagster.yaml`, falling back to `dagster.yaml` in root.

### `_load_prism_config(project_path) → dict`

Loads `.prism/prism.yaml`. Returns `{}` if missing.

---

## Permission resolution: `_resolve_permission_tier(flag_value) → str`

Priority order:

1. `--permissions` flag
2. Saved default in `~/.prism/config.yaml`
3. Interactive prompt (`_prompt_permission_tier()`)

---

## Project creation helpers

| Function | Purpose |
|----------|---------|
| `_create_dagster_yaml(directory)` | Write `.prism/dagster.yaml` |
| `_create_boilerplate_astra_yaml(directory)` | Write `astra.yaml`, `Containerfile`, `requirements.txt`, `universes/baseline.yaml` |
| `_create_claude_settings(directory, tier, target)` | Copy plugin files and write `.claude/settings.json` + `settings.local.json` |
| `_create_prism_config(directory, target_name)` | Write `.prism/prism.yaml` |
| `_create_claude_md(directory)` | Write `CLAUDE.md` from plugin template |
| `_create_venv(directory, no_venv)` | Create `.venv/` and install `lightcone-prism` |
| `_init_git_repo(directory, no_git)` | Run `git init` + initial commit |
| `_init_existing_project(...)` | Add Prism infrastructure to an existing code directory |
| `_init_sub_analysis(directory)` | Scaffold sub-analysis and wire into parent spec |

---

## `_create_claude_settings()` detail

This function:

1. Copies `scripts/`, `hooks/`, `skills/`, `agents/`, `guides/` from the plugin source to `.claude/`.
2. Makes `.sh` scripts and `.py` hooks executable.
3. Applies extraction model config to `agents/prism-extractor.md`.
4. Builds permission dict from the selected tier.
5. Merges site-specific deny rules (e.g. Perlmutter scratch paths).
6. Writes `.claude/settings.json` with full hook registrations.
7. Writes `.claude/settings.local.json` with Langfuse credentials.

---

## Update helpers

### `_sync_project_plugins(project_dir) → bool`

Syncs `skills/`, `hooks/`, `scripts/`, `agents/`, `guides/` into `project_dir/.claude/`. Updates the managed portion of `CLAUDE.md` (above `## Analysis Context`) while preserving user content below.

### `_update_extractor_agent_model(agents_dir)`

Reads `extraction_model` from `~/.prism/config.yaml` and sets (or removes) the `model:` field in `agents/prism-extractor.md` frontmatter.

---

## Status display helpers

### `_status_label(s) → str`

Maps status strings to Rich-formatted labels:

| Status | Display |
|--------|---------|
| `"materialized"` | `ok` (green) |
| `"pending"` | `pending` (dim) |
| `"alias"` | `alias` (cyan) |
| other | `no recipe` (yellow) |

### `_display_tree_status(name, groups, all_status)`

Renders a Rich tree with sub-analyses as branches.

### `_display_flat_status(name, outputs, all_status)`

Renders a Rich table (outputs × universes).
