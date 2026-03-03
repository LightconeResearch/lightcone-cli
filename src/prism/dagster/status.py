"""Materialization status queries for ASTRA outputs."""
from __future__ import annotations

from pathlib import Path

from astra.helpers import get_outputs, load_yaml


def _output_is_materialized(results_dir: Path, output_id: str) -> bool:
    """Check whether an output has been materialized.

    Handles both conventions:
    - Flat file:  results/<universe>/<output_id>.<ext>
    - Directory:  results/<universe>/<output_id>/ (with files inside)
    """
    if not results_dir.exists():
        return False
    # Flat file — any extension
    if any(results_dir.glob(f"{output_id}.*")):
        return True
    # Directory with files
    output_dir = results_dir / output_id
    if output_dir.is_dir() and any(output_dir.iterdir()):
        return True
    return False


def get_output_status(
    project_path: Path,
    universe_id: str,
) -> dict[str, str]:
    """Get materialization status for all outputs in a universe.

    Returns dict mapping output_id to status string:
    - "no_recipe": output declared but has no recipe block
    - "pending": has recipe, not yet materialized
    - "materialized": has recipe and results exist
    """
    spec = load_yaml(project_path / "astra.yaml")
    outputs = get_outputs(spec)
    results_dir = project_path / "results" / universe_id

    status: dict[str, str] = {}
    for out in outputs:
        out_id = out.get("id")
        if not out_id:
            continue
        if not out.get("recipe"):
            status[out_id] = "no_recipe"
            continue
        if _output_is_materialized(results_dir, out_id):
            status[out_id] = "materialized"
        else:
            status[out_id] = "pending"

    return status


def get_all_universe_status(
    project_path: Path,
) -> dict[str, dict[str, str]]:
    """Get status for all universes.

    Returns dict mapping universe_id to output status dict.
    """
    universes_dir = project_path / "universes"
    if not universes_dir.exists():
        return {}

    result: dict[str, dict[str, str]] = {}
    for universe_file in sorted(universes_dir.glob("*.yaml")):
        universe_data = load_yaml(universe_file)
        universe_id = universe_data.get("id", universe_file.stem)
        result[universe_id] = get_output_status(project_path, universe_id)

    return result
