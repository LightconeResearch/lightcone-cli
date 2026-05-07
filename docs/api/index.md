# Python API Reference

The interesting public surface lives in `lightcone.engine.*`. The CLI
is a thin Click wrapper around these modules.

## Module map

| Module | Role |
|--------|------|
| [`lightcone.cli.commands`](cli.md) | Click CLI: `init`, `run`, `build`, `status`, `verify`, `setup`. |
| [`lightcone.engine.manifest`](manifest.md) | Per-output `.lightcone-manifest.json` write/read; `code_version`, `sha256_dir`. The integrity layer. |
| [`lightcone.engine.snakefile`](snakefile.md) | Generate `.lightcone/Snakefile` and `snakefile-config.json` from `astra.yaml`. |
| [`lightcone.engine.container`](container.md) | Runtime detection, content-addressed image tags, `wrap_recipe`. |
| [`lightcone.engine.dask_cluster`](dask_cluster.md) | Cluster lifecycle for `lc run` (local / SLURM / external). |
| [`lightcone.engine.status`](status.md) | Manifest-driven status walker. |
| [`lightcone.engine.verify`](verify.md) | Recompute hashes; walk the input chain. |
| [`lightcone.engine.tree`](tree.md) | Sub-analysis tree helpers — outputs, decisions, `from:` resolution. |
| [`lightcone.engine.validation`](validation.md) | Post-recipe sanity checks (empty dir, all-NaN columns, …). |
| [`snakemake_executor_plugin_dask`](dask_executor.md) | Snakemake executor plugin → `dask.distributed`. |
| `lightcone.engine.site_registry` | Vestigial — no active code path imports it. See [api/site_registry](site_registry.md). |

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

runtime = detect_runtime()                                   # 'podman' / 'docker' / 'podman-hpc' / None
tag = compute_image_tag("my-project", Path("Containerfile"), Path("."))
build_image(tag, Path("Containerfile"), Path("."), runtime=runtime)
```
