# lc verify

Recompute hashes for every materialized output and walk the recorded
input chain. Catches tampering, drift, and forged manifests.

## Synopsis

```
lc verify [OPTIONS]
```

## Options

| Option | Default | Effect |
|--------|---------|--------|
| `--universe`, `-u NAME` | every universe | Restrict to one universe. |

## Output

```
Universe baseline
  ✓ ok    accuracy
  ✗ tampered_data    precision  recorded 'sha256:abc…' != actual 'sha256:def…'
  ✗ broken_chain     recall     upstream 'features' data_version drifted
  ✗ missing_manifest f1         No manifest found at output directory
```

Exit code is non-zero if any output failed.

## Failure modes

| Failure | What it means |
|---------|----------------|
| `missing_manifest` | The output directory exists but `.lightcone-manifest.json` is missing or unparseable. Most innocent cause: someone deleted the manifest. Most concerning: the directory was created by something other than `lc run`. |
| `tampered_data` | The bytes inside the output directory no longer hash to the `data_version` recorded in the manifest. Files were edited, regenerated outside the harness, or the directory contents differ from what was originally written. |
| `broken_chain` | The manifest records a specific upstream `data_version`, but the upstream's current `data_version` doesn't match. Usually means the upstream was rerun without rebuilding the downstream. Fix: `lc run` the downstream. |

## Outputs without recipes

Alias outputs (declared in `astra.yaml` without their own `recipe:`)
are skipped — there's no manifest to verify. They are checked
implicitly via the upstream output they reference.

## Outputs that aren't materialized

If an output's directory doesn't exist at all, `lc verify` skips it
(no failure to report). Use [`lc status`](status.md) to know what's
missing in the first place.

## Examples

```bash
lc verify                        # every output, every universe — non-zero exit on any failure
lc verify --universe baseline    # just baseline
```

## When to run

- Before publishing a result.
- After moving a project between machines.
- Periodically on shared archives.
- Whenever `lc status` shows `ok` but the data feels suspicious.

## Related

- [api/verify](../api/verify.md) — implementation and `VerifyResult`.
- [api/manifest](../api/manifest.md) — the manifest schema and what's hashed.
