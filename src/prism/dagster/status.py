"""Materialization status queries for ASTRA outputs."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import dagster as dg
from astra.helpers import get_outputs, load_yaml, resolve_analysis_tree

from prism.dagster.tree import TreeOutput, collect_tree_outputs

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

    Returns dict mapping qualified output_id to status string:
    - "no_recipe": output declared but has no recipe block
    - "pending": has recipe, not yet materialized
    - "materialized": has recipe and Dagster event log confirms materialization

    For sub-analysis outputs, keys are qualified: "analysis_id/output_id".
    Root-level outputs use just "output_id".
    """
    spec = load_yaml(project_path / "astra.yaml")
    # Resolve sub-analysis tree
    spec = resolve_analysis_tree(spec, project_path)

    if instance is None:
        instance = _get_dagster_instance(project_path)

    # Collect all outputs from the tree
    tree_outputs = collect_tree_outputs(spec)

    # Build asset keys for outputs with recipes, then batch-query Dagster
    recipe_keys: dict[str, dg.AssetKey] = {}  # qualified_id -> asset key
    for tree_out in tree_outputs:
        out_id = tree_out.output_id
        if not out_id or not tree_out.output_def.get("recipe"):
            continue
        if tree_out.analysis_id:
            qualified = f"{tree_out.analysis_id}/{out_id}"
            key = dg.AssetKey([universe_id, tree_out.analysis_id, out_id])
        else:
            qualified = out_id
            key = dg.AssetKey([universe_id, out_id])
        recipe_keys[qualified] = key

    materialized: set[str] = set()
    if instance is not None and recipe_keys:
        events = instance.get_latest_materialization_events(list(recipe_keys.values()))
        materialized_asset_keys = {k for k, v in events.items() if v is not None}
        for qualified, key in recipe_keys.items():
            if key in materialized_asset_keys:
                materialized.add(qualified)

    status: dict[str, str] = {}
    for tree_out in tree_outputs:
        out_id = tree_out.output_id
        if not out_id:
            continue
        if tree_out.analysis_id:
            qualified = f"{tree_out.analysis_id}/{out_id}"
        else:
            qualified = out_id

        if not tree_out.output_def.get("recipe"):
            # Check for alias outputs (from: sub.output)
            from_ref = tree_out.output_def.get("from")
            if from_ref and tree_out.analysis_id is None:
                status[qualified] = "alias"
                continue
            status[qualified] = "no_recipe"
        elif qualified in materialized:
            status[qualified] = "materialized"
        else:
            status[qualified] = "pending"

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
