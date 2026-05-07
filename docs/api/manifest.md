# lightcone.engine.manifest

The integrity layer. Every materialized output gets a sidecar
`.lightcone-manifest.json` written by this module on the host
immediately after the recipe shell exits.

Source: `src/lightcone/engine/manifest.py`. Schema version: `1`
(`SCHEMA_VERSION = 1` — bump if you change the manifest shape).

## Public surface

```python
__all__ = [
    "MANIFEST_FILENAME",      # ".lightcone-manifest.json"
    "SCHEMA_VERSION",         # 1
    "code_version",
    "fingerprint_external",
    "read_manifest",
    "sha256_dir",
    "write_manifest",
]
```

## `code_version(*, recipe, container_image, decisions) → str`

Deterministic content hash of everything that defines what the recipe
*does*: the recipe text, the resolved container image identifier, and
the canonicalized decision dict. Returns `"sha256:<hex>"`.

The runtime used to invoke the container (docker / podman / podman-hpc)
is intentionally excluded — the same image produces the same data
regardless of which OCI tool launched it.

`code_version` is embedded in each rule's `params.cfg` so Snakemake's
`params` rerun-trigger detects drift automatically.

## `sha256_dir(path) → str`

Deterministic content hash of a directory tree. Walks recursively,
hashes each file along with its relative path (so renames change the
hash), and excludes:

- `.lightcone-manifest.json` (chicken-and-egg)
- `.snakemake_timestamp` (touched by Snakemake *after* the rule body
  completes — including it would make every hash unstable)

Raises `FileNotFoundError` if `path` does not exist.

## `fingerprint_external(path, *, strict=False) → str`

External (non-manifested) input fingerprint:

- File: `mtime-size:<ns>-<bytes>` by default; `sha256:<hex>` when
  `strict=True`.
- Directory: always `sha256:<hex>` (via `sha256_dir`).
- Missing path: literal string `"missing"`.

## `read_manifest(output_dir) → dict | None`

Read `<output_dir>/.lightcone-manifest.json`. Returns `None` if the
file is missing or unparseable. **Does not** catch `OSError` — a
permission-denied or I/O error is surfaced rather than silently
masquerading as "missing", because it would otherwise hide real
problems from `lc verify` / `lc status`.

## `write_manifest(*, output_dir, inputs, cfg) → Path`

Atomically write the manifest for an already-materialized output.
Called from each rule's `run:` block.

Required keys in `cfg`:

- `output_id`, `universe_id`
- `recipe`, `container_image`, `decisions`
- `code_version`
- `git_sha`, `lc_version`

`inputs` is a `dict[str, Path]` mapping declared input id → filesystem
path. For each input, the function reads the upstream manifest if
present and records its `data_version`; otherwise falls back to
`fingerprint_external`.

Atomicity: writes to `<filename>.tmp`, then `os.replace()` rename.
Either both data and manifest exist at the end, or Snakemake reruns
the rule.

## Manifest shape

```jsonc
{
  "schema_version": 1,
  "output_id": "accuracy",
  "universe_id": "baseline",
  "code_version":  "sha256:…",
  "data_version":  "sha256:…",
  "container_image": "lc-myproject-abc123",
  "recipe": "python scripts/eval.py",
  "decisions": { "scaling": "standard", "use_pca": "no" },
  "input_versions": { "features": "sha256:…", "labels": "mtime-size:…-…" },
  "git_sha": "...",
  "lc_version": "0.4.0",
  "host": "saul01",
  "slurm_job_id": "1234567",
  "finished_at": 1717000000.0
}
```

## Tests

`tests/test_manifest.py` covers `code_version` determinism, `sha256_dir`
exclusions, `fingerprint_external` modes, and `write_manifest` end-to-end
including the atomic rename.
