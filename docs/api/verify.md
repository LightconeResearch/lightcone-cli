# lightcone.engine.verify

Recompute on-disk hashes and walk the recorded input chain. Catches
tampering, drift, and forged manifests. Like `status`, this module
never imports Snakemake.

Source: `src/lightcone/engine/verify.py`.

## Public surface

### `verify_outputs(project_path, *, universe_id) → Iterator[VerifyResult]`

Yield a `VerifyResult` for every materialized output (i.e. every output
with a recipe whose directory exists on disk) in the named universe.

Outputs that aren't materialized at all are silently skipped — that's a
question for `lc status`, not `lc verify`.

For each materialized output:

1. Read its manifest. If missing or unparseable → `missing_manifest`.
2. Recompute `sha256_dir(output_dir)`. If it doesn't match the recorded
   `data_version` → `tampered_data` (with a `recorded …  != actual …`
   detail message).
3. Walk recorded `input_versions`:
   - For each declared recipe input, look up the upstream output via
     `find_upstream_output`.
   - If upstream is external (no producer rule) → nothing to chain to.
   - If upstream's current manifest is missing → `broken_chain`
     ("upstream … missing manifest").
   - If upstream's current `data_version` ≠ recorded → `broken_chain`
     ("upstream … data_version drifted").
   - If the input is missing from the manifest entirely →
     `broken_chain` ("input … missing from manifest").
4. Otherwise `passed=True`.

### `VerifyResult` (dataclass)

```python
@dataclass
class VerifyResult:
    output_id: str
    universe_id: str
    output_dir: Path
    passed: bool
    failure: FailureKind | None
    detail: str | None = None
```

### `FailureKind`

```python
FailureKind = Literal["missing_manifest", "tampered_data", "broken_chain"]
```

## Performance notes

`sha256_dir` is the dominant cost. Hashing 10 GB of float arrays takes
real wall time. `lc status` is the cheap version that recomputes
`code_version` only — use that for the day-to-day "is this stale?"
question, and `lc verify` for periodic / pre-publication audits.

## Tests

`tests/test_verify.py` covers each failure kind end-to-end against tmp
projects, plus the chain-walking through nested sub-analyses.
