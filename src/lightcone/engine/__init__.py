"""Lightcone execution engine — Dagster assets, runners, and HPC targets.

Provides:
- build_definitions(): Generate Dagster Definitions from astra.yaml
- ASTRAContainerRunner: Execute recipes in Docker/SLURM containers
- ASTRAIOManager: Map (asset, universe) to filesystem paths
- get_output_status(): Query materialization status
"""
from lightcone.engine.io_manager import ASTRAIOManager
from lightcone.engine.runner import (
    ASTRAContainerRunner,
    generate_sbatch_script,
    translate_resources_to_slurm_directives,
)
from lightcone.engine.status import get_all_universe_status, get_output_status
from lightcone.engine.targets import list_targets, load_target, save_target

__all__ = [
    "ASTRAContainerRunner",
    "ASTRAIOManager",
    "build_asset_definitions",
    "build_definitions",
    "generate_sbatch_script",
    "get_output_status",
    "get_all_universe_status",
    "list_targets",
    "load_target",
    "save_target",
    "translate_resources_to_slurm_directives",
]


def build_definitions(*args, **kwargs):
    """Build Dagster Definitions from astra.yaml. Requires dagster to be installed."""
    from lightcone.engine.assets import build_definitions as _build
    return _build(*args, **kwargs)


def build_asset_definitions(*args, **kwargs):
    """Build asset definitions from astra.yaml. Requires dagster to be installed."""
    from lightcone.engine.assets import build_asset_definitions as _build
    return _build(*args, **kwargs)
