"""Materialization status queries for ASTRA outputs."""
from __future__ import annotations

import logging
from pathlib import Path

import dagster as dg
from astra.helpers import load_yaml, resolve_analysis_tree

from lightcone.engine.tree import collect_tree_outputs

logger = logging.getLogger(__name__)


def _get_dagster_instance(project_path: Path) -> dg.DagsterInstance | None:
    """Load a DagsterInstance for read-only status queries.

    Uses the project's active cluster's Postgres URL when one is up.
    Returns ``None`` otherwise — callers fall through to filesystem
    inspection (the IO manager guarantees ``results/<universe>/<output>/``
    is the canonical location for any materialized output).
    """
    try:
        from lightcone.engine.clusters import cluster_info
    except ImportError:
        return None
    info = cluster_info(project_path)
    if info is None or info.record is None or not info.record.postgres_url:
        return None
    try:
        cfg_dir = project_path / ".lightcone" / "dagster-instance"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "dagster.yaml").write_text(
            f"storage:\n  postgres:\n    postgres_url: {info.record.postgres_url}\n"
        )
        return dg.DagsterInstance.from_config(str(cfg_dir))
    except Exception:
        logger.warning(
            "Failed to load Dagster instance from cluster Postgres %s",
            info.record.postgres_url, exc_info=True,
        )
        return None


def _output_dir_materialized(project_path: Path, universe_id: str, qualified: str) -> bool:
    """Filesystem check: does the output dir exist and contain anything?

    The IO manager writes to ``results/<universe>/<qualified>/``; presence
    of any file in it is the canonical "materialized" signal when the
    Dagster instance isn't reachable.
    """
    out_dir = project_path / "results" / universe_id / qualified
    if not out_dir.is_dir():
        return False
    return any(out_dir.iterdir())


def get_output_status(
    project_path: Path,
    universe_id: str,
    instance: dg.DagsterInstance | None = None,
) -> dict[str, str]:
    """Get materialization status for all outputs in a universe.

    Returns dict mapping qualified output_id to status string:
    - ``"no_recipe"``: output declared but has no recipe block
    - ``"pending"``: has recipe, not yet materialized
    - ``"materialized"``: has recipe and either Dagster events confirm
      materialization or the IO manager's output dir is non-empty
    - ``"alias"``: aliased to another output (``from:`` reference)

    For sub-analysis outputs, keys are qualified: ``"analysis_id/output_id"``.
    Root-level outputs use just ``"output_id"``.
    """
    spec = load_yaml(project_path / "astra.yaml")
    spec = resolve_analysis_tree(spec, project_path)

    if instance is None:
        instance = _get_dagster_instance(project_path)

    tree_outputs = collect_tree_outputs(spec)

    recipe_keys: dict[str, dg.AssetKey] = {}
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
            from_ref = tree_out.output_def.get("from")
            if from_ref and tree_out.analysis_id is None:
                status[qualified] = "alias"
                continue
            status[qualified] = "no_recipe"
        elif qualified in materialized:
            status[qualified] = "materialized"
        elif _output_dir_materialized(project_path, universe_id, qualified):
            # Dagster instance unreachable but output exists on disk —
            # trust the filesystem (canonical IO manager layout).
            status[qualified] = "materialized"
        else:
            status[qualified] = "pending"

    return status


def get_all_universe_status(
    project_path: Path,
) -> dict[str, dict[str, str]]:
    """Get status for all universes."""
    universes_dir = project_path / "universes"
    if not universes_dir.exists():
        return {}

    instance = _get_dagster_instance(project_path)

    result: dict[str, dict[str, str]] = {}
    for universe_file in sorted(universes_dir.glob("*.yaml")):
        universe_data = load_yaml(universe_file)
        universe_id = universe_data.get("id", universe_file.stem)
        result[universe_id] = get_output_status(project_path, universe_id, instance=instance)

    return result
