# lightcone.engine.status

Manifest-driven status walker. Reads only the per-output
`.lightcone-manifest.json` files; does **not** import Snakemake.

Source: `src/lightcone/engine/status.py`.

## Public surface

### `get_output_status(project_path, *, universe_id) → Iterator[OutputStatus]`

Yield an `OutputStatus` for every declared output in `project_path`'s
`astra.yaml`, against the named universe. Used by `lc status` and by
external tooling that wants a structured view.

The function:

1. Loads and resolves the analysis tree.
2. Loads merged universe decisions (tolerates a missing universe file —
   returns an empty dict).
3. For each tree output:
   - If it has no `recipe:` → `alias`.
   - If no manifest at the output dir → `missing`.
   - Otherwise recomputes `code_version` against the current spec and
     compares to the manifest's recorded value. Match → `ok`,
     mismatch → `stale`.

### `OutputStatus` (dataclass)

```python
@dataclass
class OutputStatus:
    output_id: str
    universe_id: str
    analysis_id: str | None       # None for root-level outputs
    output_dir: Path
    status: StatusLiteral          # "ok" | "stale" | "missing" | "alias"
    manifest: dict | None          # None for missing/alias
```

### `StatusLiteral`

```python
StatusLiteral = Literal["ok", "stale", "missing", "alias"]
```

## Why `code_version` is the staleness signal

The manifest records the `code_version` that produced the data. Drift
detection just recomputes the current `code_version` from the live
spec and compares. Anything that touches recipe text, container image
tag, or decisions changes `code_version`; everything else is irrelevant
for staleness.

For staleness against external inputs (e.g. someone edited a CSV under
`inputs/`), `lc status` doesn't catch it — that's outside `code_version`'s
scope. Use `lc verify` or rely on Snakemake's `mtime`/`input` rerun
triggers in `lc run`.

## Tests

`tests/test_status.py` covers the four status branches end-to-end
against tmp projects, including the alias and decision-drift paths.
