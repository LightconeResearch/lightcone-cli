# lightcone.engine.resources

Canonical parsing for ASTRA `Recipe.resources`.

Source: `src/lightcone/engine/resources.py`.

`parse_recipe_resources()` normalizes portable ASTRA values to CPUs, MB,
GPUs, and seconds. `RecipeResources.snakemake()` maps those to
`cpus_per_task`, `mem_mb`, `gpus_per_task`, and minute-based `runtime`.
Synchronous Snakefile generation and asynchronous SLURM sizing both use this
module, preventing the two paths from interpreting a recipe differently.
