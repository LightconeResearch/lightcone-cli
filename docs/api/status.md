# lightcone.engine.status

Materialisation status queries for ASTRA outputs. Reads from the Dagster SQLite event log.

---

## `get_output_status(project_path, universe_id, instance) → dict[str, str]`

Returns a dict mapping qualified output IDs to status strings.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `project_path` | `Path` | ASTRA project root |
| `universe_id` | `str` | Universe to query |
| `instance` | `DagsterInstance \| None` | Reuse an existing instance (optional) |

**Returns:** `{qualified_output_id: status_string}`

- Root-level outputs use `output_id` as key.
- Sub-analysis outputs use `analysis_id/output_id` as key.

---

## `get_all_universe_status(project_path) → dict[str, dict[str, str]]`

Returns `{universe_id: output_status_dict}` for all universes in `project_path/universes/`.

Shares a single `DagsterInstance` across all universe queries.

---

## Status values

| Value | Meaning |
|-------|---------|
| `"no_recipe"` | Output declared in spec, no `recipe` block |
| `"pending"` | Has a recipe, never materialised |
| `"materialized"` | Dagster event log confirms a successful run |
| `"alias"` | Root-level output with a `from:` reference (no recipe needed) |

---

## Implementation notes

`get_output_status()` builds a map of `qualified_id → AssetKey` for all outputs that have recipes, then calls `DagsterInstance.get_latest_materialization_events()` in a single batch query to avoid N+1 database hits.

A `None` instance (missing or corrupted `dagster.yaml`) is treated as "no events recorded" — all recipe outputs report `"pending"`.
