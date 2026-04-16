# Python API Reference

Prism's public Python API is split across several modules. Most maintainer work happens in the Dagster integration modules and the CLI.

## Module overview

| Module | Description |
|--------|-------------|
| [`prism.cli`](cli.md) | Click CLI: `init`, `run`, `build`, `status`, `dev`, `setup`, `target`, `update` |
| [`prism.container`](container.md) | Content-addressed container builds (Docker / podman-hpc) |
| [`prism.dagster.assets`](assets.md) | Asset factory — turns `astra.yaml` recipes into Dagster assets |
| [`prism.dagster.runner`](runner.md) | Execution backends: Docker, local, venv, SLURM |
| [`prism.dagster.io_manager`](io_manager.md) | Enforces `results/{universe}/{output}/` path convention |
| [`prism.dagster.status`](status.md) | Materialisation status queries via Dagster SQLite |
| [`prism.dagster.targets`](targets.md) | User-level target config management (`~/.prism/targets/`) |
| [`prism.dagster.site_registry`](site_registry.md) | Known HPC site defaults (Perlmutter, etc.) |

## Key entry points

```python
from prism.dagster.assets import build_definitions

# Build a Dagster Definitions object from a project
defs = build_definitions(
    project_path=Path("my-analysis"),
    target_config=None,      # None → local execution
    universe_id="baseline",
    no_build=False,
)

# Materialise all assets
import dagster as dg
result = dg.materialize(assets=list(defs.assets))
```

```python
from prism.dagster.status import get_all_universe_status

# {'baseline': {'accuracy': 'materialized', 'conclusion': 'pending'}}
status = get_all_universe_status(Path("my-analysis"))
```

```python
from prism.container import detect_container_runtime, resolve_container_spec

runtime = detect_container_runtime()   # 'docker', 'podman', or None
tag = resolve_container_spec("Containerfile", Path("."), "my-project")
```
