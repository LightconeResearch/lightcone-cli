"""Materialization status queries for ASTRA outputs."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import dagster as dg
from astra.helpers import get_outputs, load_yaml

logger = logging.getLogger(__name__)


def _get_dagster_instance(project_path: Path) -> dg.DagsterInstance | None:
    """Load a DagsterInstance from the project's dagster.yaml.

    Returns None if dagster.yaml doesn't exist or the instance can't be loaded
    (e.g. corrupted SQLite). Callers treat None as "no events recorded."

    Temporarily changes to project_path so that relative paths in dagster.yaml
    (e.g. ``base_dir: results/.dagster``) resolve correctly.
    """
    # Check .prism/ first, then root for backwards compat
    dagster_yaml = project_path / ".prism" / "dagster.yaml"
    if not dagster_yaml.exists():
        dagster_yaml = project_path / "dagster.yaml"
    if not dagster_yaml.exists():
        return None
    config_dir = dagster_yaml.parent
    old_cwd = os.getcwd()
    try:
        os.chdir(project_path)
        return dg.DagsterInstance.from_config(str(config_dir))
    except Exception:
        logger.warning("Failed to load Dagster instance from %s", project_path, exc_info=True)
        return None
    finally:
        os.chdir(old_cwd)


def get_output_status(
    project_path: Path,
    universe_id: str,
    instance: dg.DagsterInstance | None = None,
) -> dict[str, str]:
    """Get materialization status for all outputs in a universe.

    Returns dict mapping output_id to status string:
    - "no_recipe": output declared but has no recipe block
    - "pending": has recipe, not yet materialized
    - "materialized": has recipe and Dagster event log confirms materialization
    """
    spec = load_yaml(project_path / "astra.yaml")
    outputs = get_outputs(spec)

    if instance is None:
        instance = _get_dagster_instance(project_path)

    # Collect asset keys for outputs with recipes, then batch-query Dagster
    recipe_ids = [
        out["id"] for out in outputs
        if out.get("id") and out.get("recipe")
    ]
    materialized: set[str] = set()
    if instance is not None and recipe_ids:
        keys = [dg.AssetKey([universe_id, oid]) for oid in recipe_ids]
        events = instance.get_latest_materialization_events(keys)
        materialized = {
            k.path[-1] for k, v in events.items() if v is not None
        }

    status: dict[str, str] = {}
    for out in outputs:
        out_id = out.get("id")
        if not out_id:
            continue
        if not out.get("recipe"):
            status[out_id] = "no_recipe"
        elif out_id in materialized:
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

    # Create instance once and share across all universe checks
    instance = _get_dagster_instance(project_path)

    result: dict[str, dict[str, str]] = {}
    for universe_file in sorted(universes_dir.glob("*.yaml")):
        universe_data = load_yaml(universe_file)
        universe_id = universe_data.get("id", universe_file.stem)
        result[universe_id] = get_output_status(project_path, universe_id, instance=instance)

    return result
