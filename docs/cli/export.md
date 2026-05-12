# lc export

Export project artifacts in interoperable formats. Currently the only
exporter is `wrroc` (Workflow Run RO-Crate); the group is shaped to host
future formats without breaking the CLI surface.

## Synopsis

```text
lc export wrroc [OPTIONS]
```

## lc export wrroc

Walk the project's per-output `.lightcone-manifest.json` sidecars and
emit a [Workflow Run RO-Crate](https://www.researchobject.org/workflow-run-crate/)
bundle — a JSON-LD package readable by WorkflowHub, Zenodo's RO-Crate
plugin, and any RO-Crate-aware archive. The on-disk manifest format is
unchanged; the bundle is the *publication view*, generated on demand.

### Options

| Option | Default | Effect |
|--------|---------|--------|
| `--output`, `-o PATH` | `./wrroc` | Bundle directory, or `.zip` path when `--zip` is set. |
| `--universe`, `-u NAME` | every universe with materialized outputs | Restrict to specific universes. Repeatable. |
| `--author "NAME <email>"` | git `user.name` / `user.email`, then `LIGHTCONE_AUTHOR` env | Override the author recorded in the bundle. |
| `--license URL` | `https://creativecommons.org/licenses/by/4.0/` | License URL or SPDX identifier for the bundle. Required by the WRROC profile. |
| `--zip` / `--no-zip` | `--no-zip` | Package the bundle as a single `.zip` after building. |
| `--metadata-only` | off | Skip data files; bundle manifests + `astra.yaml` + universe files only. |

### What gets bundled

- `astra.yaml` → `ComputationalWorkflow` (`programmingLanguage: snakemake`).
- Each materialized output → `Dataset` with `version = data_version`.
- Each recipe execution → `CreateAction` with `object` (inputs, both upstream datasets via stable `@id` and external files), `result` (the produced dataset), and `instrument` (the recipe `SoftwareApplication`).
- Each container → `SoftwareApplication` referenced via `softwareRequirements`.
- Each decision → `FormalParameter` + per-run `PropertyValue`.
- Author → `Person`.

Provenance metadata (`code_version`, `data_version`, `git_sha`, `lc_version`, host) lands as `PropertyValue` entries on the relevant entities.

### Examples

```bash
lc export wrroc                                # ./wrroc/ directory
lc export wrroc -o run.zip --zip               # zip bundle for upload
lc export wrroc --metadata-only                # provenance graph only, no data files
lc export wrroc -u baseline -u alt_method      # restrict to specific universes
lc export wrroc --author "Ada Lovelace <ada@example.org>"
lc export wrroc --license https://opensource.org/licenses/MIT
```

### Output

```text
✓ Wrote WRROC directory: ./wrroc
  Captured 7 runs across universes: baseline, alt_method
```

If no materialized outputs are found, the bundle still writes — but only
contains the workflow definition, and a warning is printed:

```text
✓ Wrote WRROC directory: ./wrroc
Warning: no materialized outputs were found — the bundle contains only
  the workflow definition.
  This usually means recipes haven't been run yet (try lc run) or the
  .lightcone-manifest.json sidecars are missing.
  Workflow-only bundles will not pass strict Provenance Run Crate
  validation; that profile requires at least one materialized run.
```

### Failure modes

| Error | Cause |
|---|---|
| `No astra.yaml at <path>; cannot export.` | The cwd is not inside an ASTRA project. |
| `<path> is non-empty; refuse to clobber.` | The target directory already has contents. Pass a fresh path or remove the existing one. |
| `<path> is an existing directory; cannot overwrite with a zip.` | `--zip` was requested but the output path resolves to a directory. Use a file path like `bundle.zip`. |

Manifests that exist but are unreadable (e.g. cross-user symlinks under
`results/` with permission denied) are warned about and skipped — they
do not abort the export.

### Validation

The bundle conforms to the [Provenance Run Crate 0.5](https://w3id.org/ro/wfrun/provenance/0.5)
profile. To validate locally:

```bash
pip install git+https://github.com/crs4/rocrate-validator.git
rocrate-validator -y validate ./wrroc/
```

### When to run

- Before submitting a paper or depositing artifacts in Zenodo / WorkflowHub.
- After a clean run (`lc verify` clean) on the final commit you intend to publish.
- For external collaborators who don't have `lc` installed but need to inspect provenance.

### Related

- [`lc verify`](verify.md) — confirm the manifest chain is intact before exporting.
- [`lc status`](status.md) — see which outputs will be captured by the export.
- [api/manifest](../api/manifest.md) — the on-disk format the export reads from.
