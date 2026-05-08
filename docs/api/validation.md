# lightcone.engine.validation

Post-recipe sanity checks. Called by every rule's body after
`write_manifest`. **Never raises** — all problems are returned as
warning strings and printed to stderr.

Source: `src/lightcone/engine/validation.py`.

## `validate_output(output_dir, output_type, output_id) → list[str]`

Inspect the output directory after a successful recipe run. Empty list
means no problems. Returned strings are human-readable and prefixed by
the rule body with a `⚠ ` marker.

The check fires unconditionally on a few "this is almost certainly
wrong" situations:

- Output directory does not exist after a successful run.
- Output directory exists but is a file rather than a directory.
- Output directory is empty after a successful run.

Beyond that, behavior depends on the declared `type:` in `astra.yaml`:

| `output_type` | Check |
|---------------|-------|
| `metric` | At least one `*.json` file present, parseable, not all-null/all-NaN. |
| `table` | At least one `*.csv` file present; parseable; warns on individual all-NaN numeric columns and on tables where every numeric column is all-NaN. |
| `figure` | At least one `*.png/jpg/jpeg/svg/pdf/eps` file present; warns on zero-byte files. |
| anything else | Empty list (no specific check). |

## What it does *not* do

This is a smoke test, not a validator. It does not:

- Check the schema or shape of metric JSON beyond null-detection.
- Compare against expected values.
- Catch silent computational errors.
- Block the run — warnings are printed but the manifest is still
  written.

For deeper validation, layer your own checks in the recipe (`assert`,
`pydantic`, …). The point of `validate_output` is to flag the cheap
common silent failures: empty directories, NaN-only columns, missing
files.

## Tests

`tests/test_validation.py` covers each output-type branch including
the malformed-input edge cases (unparseable JSON, missing CSV, zero-byte
figures, …).
