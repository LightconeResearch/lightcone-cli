"""Lightcone execution engine — Dagster assets, runner, pilots."""
from lightcone.engine.io_manager import ASTRAIOManager
from lightcone.engine.pilots import list_pilots, load_pilot_config, save_pilot_config
from lightcone.engine.runner import ASTRAContainerRunner
from lightcone.engine.status import get_all_universe_status, get_output_status

__all__ = [
    "ASTRAContainerRunner",
    "ASTRAIOManager",
    "build_asset_definitions",
    "build_definitions",
    "get_all_universe_status",
    "get_output_status",
    "list_pilots",
    "load_pilot_config",
    "save_pilot_config",
]


def build_definitions(*args, **kwargs):
    """Build Dagster Definitions from astra.yaml. Requires dagster to be installed."""
    from lightcone.engine.assets import build_definitions as _build
    return _build(*args, **kwargs)


def build_asset_definitions(*args, **kwargs):
    """Build asset definitions from astra.yaml. Requires dagster to be installed."""
    from lightcone.engine.assets import build_asset_definitions as _build
    return _build(*args, **kwargs)
