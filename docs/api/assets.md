# lightcone.engine.assets

Asset factory that generates Dagster assets from `astra.yaml` output recipes.

## `build_definitions(project_path, target_config, universe_id, no_build) → dg.Definitions`

The main entry point for the Dagster integration. Loads `astra.yaml`, resolves the full sub-analysis tree, builds container images if needed, and returns a `dg.Definitions` object containing one asset per output with a recipe.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `project_path` | `Path` | — | Root directory of the ASTRA project |
| `target_config` | `dict \| None` | `None` | Parsed target YAML; `None` means local execution |
| `universe_id` | `str` | `"baseline"` | Universe to load definitions for |
| `no_build` | `bool` | `False` | If `True`, skip container image builds |

**Returns:** `dg.Definitions` — Dagster definitions object. Pass its `.assets` to `dg.materialize()`.

---

## `build_asset_definitions(spec, runner, universe_id, ...) → list`

Lower-level function that takes an already-loaded spec dict and returns a flat list of `AssetsDefinition | AssetSpec` objects.

Called by `build_definitions()` after setting up the runner and resolving the analysis-level container.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `spec` | `dict` | Resolved `astra.yaml` dict (sub-analyses already merged in) |
| `runner` | `ASTRAContainerRunner \| None` | Execution runner; if `None`, assets cannot be materialised |
| `universe_id` | `str` | Universe identifier |
| `project_path` | `Path \| None` | Project root (for universe param loading) |
| `project_name` | `str \| None` | Used as container image name prefix |
| `no_build` | `bool` | Skip container builds |
| `container_runtime` | `str \| None` | SLURM-side runtime (e.g. `"podman-hpc"`) |
| `local_runtime` | `str \| None` | Local runtime detected by `detect_container_runtime()` |

---

## `get_external_inputs(spec) → dict[str, str]`

Returns `{input_id: source_path}` for inputs with an absolute filesystem source path (e.g. pre-existing data files mounted on the system).

---

## Asset key scheme

| Output type | Key |
|-------------|-----|
| Root-level output | `[universe_id, output_id]` |
| Sub-analysis output | `[universe_id, analysis_id, output_id]` |
| External input | `[universe_id, input_id]` |
| Root alias (`from: sub.output`) | `[universe_id, output_id]` → deps on sub key |

## Container resolution order

Per recipe: `recipe.container` → sub-analysis: `analysis.container` → root: `spec.container`

Each level can be a pre-built image name or a `Containerfile` path. The resolver dispatches to the appropriate builder:

- **SLURM target** → `resolve_container_for_slurm()` (handles podman-hpc build + migrate)
- **Local with runtime** → `resolve_container_spec()` (Docker or Podman)
- **No runtime** → returns the raw string as-is (Docker pull deferred to runtime)

## Enabling autodoc

To generate full API docs from docstrings, install the `docs` dependency group and uncomment the `mkdocstrings` plugin in `mkdocs.yml`:

```bash
uv sync --group docs
# Then uncomment mkdocstrings in mkdocs.yml
```
