# lc dev (removed)

This command no longer exists. lightcone-cli moved off the Dagster execution
backend in favor of Snakemake + Dask, and the `lc dev` Dagster-webserver
launcher was removed along with it.

If you need a visual representation of the analysis DAG, generate a Snakemake
DAG image directly:

```bash
lc run --dry-run | snakemake --dag | dot -Tsvg > dag.svg
```

(or rerun with `snakemake -s .lightcone/Snakefile --dag` after `lc run`).

For materialization status, use [`lc status`](status.md). For provenance
verification, use [`lc verify`](verify.md).
