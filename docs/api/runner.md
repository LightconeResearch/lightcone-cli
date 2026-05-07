# lightcone.engine.runner (removed)

The pluggable runner (`docker`, `venv`, `local`, `slurm`) was replaced by
two thinner pieces:

- The Snakefile generator at [engine/snakefile](snakefile.md) wraps each
  recipe in a `<runtime> run --rm ...` invocation at generation time
  (or leaves it bare when no container is configured).
- The Dask cluster manager at [engine/dask_cluster](dask_cluster.md)
  decides whether the run is local, SLURM-backed via `srun`, or attached
  to an external scheduler.

There is no longer a single "backend" abstraction — those two
modules together cover what the runner used to do.
