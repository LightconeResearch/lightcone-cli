# Python API Reference

The interesting public surface lives in `lightcone.engine.*`. The CLI
is a thin Click wrapper around these modules.

## Module map

| Module | Role |
|--------|------|
| [`lightcone.cli.commands`](cli.md) | Click CLI: `init`, `run`, `status`, `verify`, `build`, `export`, plus `eval` when the extra is installed. |
| [`lightcone.engine.manifest`](manifest.md) | Per-output `.lightcone-manifest.json` write/read; `code_version`, `sha256_dir`. The integrity layer. |
| [`lightcone.engine.snakefile`](snakefile.md) | Generate `.lightcone/Snakefile` and `snakefile-config.json` from `astra.yaml`. |
| [`lightcone.engine.runner`](runner.md) | `run_rule` — the body of every generated rule; the `SENTINEL` output protocol. |
| [`lightcone.engine.container`](container.md) | Runtime detection, content-addressed image identity, `wrap_recipe`. |
| [`lightcone.engine.cloudbuild`](cloudbuild.md) | GCP Cloud Build backend — remote image builds where no OCI runtime exists. |
| [`lightcone.engine.dask_cluster`](dask_cluster.md) | Cluster lifecycle for `lc run` (external scheduler / Dask Gateway / SLURM / local). |
| [`lightcone.engine.scratch`](scratch.md) | Resolve the scratch root; per-run dirs, the run lock, the `.snakemake` symlink. |
| [`lightcone.engine.status`](status.md) | Manifest-driven status walker. |
| [`lightcone.engine.verify`](verify.md) | Recompute hashes; walk the input chain. |
| [`lightcone.engine.tree`](tree.md) | Sub-analysis tree helpers — outputs, decisions, `from:` resolution. |
| [`lightcone.engine.validation`](validation.md) | Post-recipe sanity checks (empty dir, all-NaN columns, …). |
| [`lightcone.engine.wrroc`](wrroc.md) | Workflow Run RO-Crate exporter behind `lc export wrroc`. |
| [`lightcone.engine.site_registry`](site_registry.md) | Known-site defaults; `detect_current_site()` drives scratch and runtime selection. |
| [`snakemake_executor_plugin_dask`](dask_executor.md) | Snakemake executor plugin → `dask.distributed`. |

## Common entry points

```python
from pathlib import Path
from lightcone.engine.snakefile import generate, discover_universes
from lightcone.engine.container import load_runtime

project = Path("my-analysis")
runtime = load_runtime(project_path=project).runtime
universes = discover_universes(project)        # ['baseline', 'experiment']
snakefile, cfg = generate(project, universes=universes, runtime=runtime)
# Now invoke `snakemake -s snakefile -d project --executor dask ...`
```

```python
from lightcone.engine.status import get_output_status

for s in get_output_status(project, universe_id="baseline"):
    print(s.status, s.output_id)        # 'ok', 'stale', 'missing', or 'alias'
```

```python
from lightcone.engine.verify import verify_outputs

failed = [r for r in verify_outputs(project, universe_id="baseline") if not r.passed]
for r in failed:
    print(r.failure, r.output_id, r.detail)
```

```python
from lightcone.engine.container import (
    detect_runtime,
    compute_image_tag,
    build_image,
)

runtime = detect_runtime()                                   # 'podman-hpc' / 'podman' / 'docker' / 'kubernetes' / None
tag = compute_image_tag("my-project", Path("Containerfile"), Path("."))
build_image(tag, Path("Containerfile"), Path("."), runtime=runtime)
```
