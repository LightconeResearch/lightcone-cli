# lightcone.engine.io_manager

IO manager that enforces the canonical `results/{universe_id}/{output_id}/` path convention.

---

## `ASTRAIOManager`

```python
class ASTRAIOManager(dg.IOManager):
    def __init__(self, project_root: str): ...
```

Dagster IO manager attached to all assets built by `build_definitions()`.

### `get_output_path(universe_id, output_id) → Path`

Returns `{project_root}/results/{universe_id}/{output_id}/`.

### `get_input_paths(universe_id, input_ids) → dict[str, Path]`

Returns `{input_id: path}` for each input ID, pointing to the same path convention.

---

## Path convention

```
{project_root}/results/{universe_id}/{output_id}/
```

This is not customisable. Scripts should write their results to `$ASTRA_OUTPUT_DIR`, which the runner sets to the correct path before execution.

The IO manager is instantiated by `build_definitions()` and attached to all assets automatically. It is not called directly by user code.
