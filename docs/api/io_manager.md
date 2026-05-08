# lightcone.engine.io_manager (removed)

The Dagster IO manager was retired. Output paths are now baked into the
generated Snakefile by [engine/snakefile](snakefile.md), and the canonical
location of every output directory is computed by
[`resolve_output_path`](tree.md) — root outputs land at
`results/<universe>/<output_id>/`, and path-rooted sub-analyses land at
`<sub_path>/results/<universe>/<output_id>/`.
