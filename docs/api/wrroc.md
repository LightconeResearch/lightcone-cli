# lightcone.engine.wrroc

The Workflow Run RO-Crate exporter behind
[`lc export wrroc`](../cli/export.md). Walks a project's
`.lightcone-manifest.json` sidecars and emits an
[RO-Crate](https://www.researchobject.org/workflow-run-crate/) bundle
suitable for WorkflowHub, Zenodo, or any RO-Crate-aware archive.

Source: `src/lightcone/engine/wrroc.py`.

The manifest layer stays the canonical internal format — WRROC is the
**publication** view, generated on demand. The exporter is one-shot: no
live crate is maintained.

## Profiles

We target the deepest of the three WRROC profiles, *Provenance Run
Crate*, because the manifest layer already captures the per-step data it
requires. All three are declared on the root's `conformsTo` (and, since
validators expect it, added to the graph as `CreativeWork` entities):

```python
PROCESS_RUN_CRATE_PROFILE     = "https://w3id.org/ro/wfrun/process/0.5"
WORKFLOW_RUN_CRATE_PROFILE    = "https://w3id.org/ro/wfrun/workflow/0.5"
PROVENANCE_RUN_CRATE_PROFILE  = "https://w3id.org/ro/wfrun/provenance/0.5"
```

## Entity mapping

| lightcone concept | WRROC entity |
|-------------------|--------------|
| `astra.yaml` | `ComputationalWorkflow` |
| each universe | `PropertyValue` set on the workflow |
| each materialized output dir | `Dataset` (with the data files inside) |
| each recipe execution | `CreateAction` — `object` = upstream Datasets / external Files, `result` = the output Dataset, `instrument` = the recipe `SoftwareApplication`, `agent` = the `Person` |
| each container image | `SoftwareApplication` |
| each decision value | `PropertyValue` on the workflow |

## `export_wrroc(project_path, output_path, *, universes=None, author=None, license=None, zip_bundle=False, include_data=True) → ExportResult`

The public entry point.

- **`universes`** — `None` discovers every universe from `universes/`.
  A universe with no materialized output contributes nothing and is left
  out of `universes_included`.
- **`author`** — `"Name <email>"`. Falls back to `git config
  user.name`/`user.email`, then the `LIGHTCONE_AUTHOR` env var.
- **`license`** — URL or SPDX identifier. Required by the Workflow
  RO-Crate profile; falls back to `astra.yaml`'s `license`, then to
  `DEFAULT_LICENSE` (CC-BY-4.0).
- **`include_data=False`** — bundle manifests, `astra.yaml`, and the
  universe files only. Useful for archiving provenance without
  re-uploading large data.
- **`zip_bundle`** — `output_path` is the `.zip` file rather than the
  bundle directory.

Raises `FileNotFoundError` when the project has no `astra.yaml`, and
`FileExistsError` rather than clobbering a non-empty output directory (or
an existing directory named as a zip target).

`rocrate` is imported lazily inside the function, so the module stays
importable without the optional dependency.

```python
@dataclass
class ExportResult:
    bundle_path: Path
    runs_included: int          # CreateActions emitted
    universes_included: list[str]
    is_zip: bool
```

`runs_included == 0` is not an error — the bundle then holds only the
workflow definition — but `lc export wrroc` warns, because that shape
will not pass strict Provenance Run Crate validation.

## `WRROCBuilder`

Accumulates lightcone state into one `ROCrate` instance. It owns the
`@id` minting strategy and de-duplicates repeated entities (the same
recipe `SoftwareApplication` is shared by every `CreateAction` that used
it).

| Method | Role |
|--------|------|
| `add_workflow() → str` | Add `astra.yaml` as the `ComputationalWorkflow`; returns its `@id`. |
| `add_universe_runs(universe_id, tree_outputs) → int` | Add `Dataset` + `CreateAction` for every materialized output in the universe; returns the count of actions added. |

## Public surface

```python
__all__ = [
    "ExportResult",
    "PROCESS_RUN_CRATE_PROFILE",
    "PROVENANCE_RUN_CRATE_PROFILE",
    "WORKFLOW_RUN_CRATE_PROFILE",
    "WRROCBuilder",
    "export_wrroc",
]
```

## Tests

`tests/test_wrroc.py` builds bundles against temporary projects and
asserts the graph shape — profile conformance, workflow-as-main-entity,
the preserved input chain across two steps, decision `PropertyValue`s —
plus the zip, author, universe-filter, and `--metadata-only` paths, and a
round-trip load back through `rocrate-py`.
