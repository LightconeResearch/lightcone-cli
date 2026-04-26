"""Manifest-driven status walker.

For each output declared in a project's ``astra.yaml``, determines whether
it is materialized, stale, missing, or an alias — by reading the per-output
manifest written at ``<output_dir>/.lightcone-manifest.json``.

This module never imports Snakemake. ``lc status`` works on a fresh clone
with no ``.snakemake/`` directory and on frozen archives.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from astra.helpers import load_yaml, resolve_analysis_tree

from lightcone.engine.manifest import code_version, read_manifest
from lightcone.engine.tree import (
    TreeOutput,
    collect_tree_outputs,
    resolve_output_path,
    resolve_universe_decisions,
)

StatusLiteral = Literal["ok", "stale", "missing", "alias"]


@dataclass
class OutputStatus:
    output_id: str
    universe_id: str
    analysis_id: str | None
    output_dir: Path
    status: StatusLiteral
    manifest: dict[str, Any] | None


def _resolve_recipe_container(tree_output: TreeOutput, root_spec: dict[str, Any]) -> str | None:
    """Pick the container declaration in priority order:
    recipe-level > sub-analysis-level > root-level.
    Returns the raw spec string (e.g. Containerfile path or image tag);
    we hash the string identity, not the resolved image content.
    """
    recipe = tree_output.output_def.get("recipe") or {}
    if "container" in recipe:
        return recipe["container"]  # type: ignore[no-any-return]
    if tree_output.analysis_id is not None:
        sub = tree_output.analysis_spec.get("container")
        if sub is not None:
            return sub  # type: ignore[no-any-return]
    return root_spec.get("container")  # type: ignore[no-any-return]


def _decisions_for(
    tree_output: TreeOutput,
    universe_decisions: dict[str, Any],
) -> dict[str, Any]:
    """Return the decisions visible to a given output for code_version
    computation. We use the full merged universe decision set, so any
    decision change anywhere in the universe invalidates downstream.
    """
    return universe_decisions


def _load_universe_decisions(
    project_path: Path,
    spec: dict[str, Any],
    universe_id: str,
) -> dict[str, Any]:
    """Load merged universe decisions if the file exists; empty dict otherwise.

    Universe files are optional during interactive work, so we tolerate
    their absence rather than erroring.
    """
    universe_yaml = project_path / "universes" / f"{universe_id}.yaml"
    if not universe_yaml.exists():
        return {}
    try:
        return resolve_universe_decisions(project_path, spec, universe_id)
    except (FileNotFoundError, KeyError):
        return {}


def get_output_status(
    project_path: Path,
    *,
    universe_id: str,
) -> Iterator[OutputStatus]:
    """Yield an :class:`OutputStatus` for every declared output in the project."""
    spec_path = project_path / "astra.yaml"
    spec = resolve_analysis_tree(load_yaml(spec_path), project_path)
    universe_decisions = _load_universe_decisions(project_path, spec, universe_id)

    for tree_out in collect_tree_outputs(spec):
        out_dir = resolve_output_path(project_path, tree_out, universe_id) / tree_out.output_id

        # Aliases — outputs without their own recipe — are materialized as
        # a side effect of their upstream. They have no independent status.
        recipe = tree_out.output_def.get("recipe")
        if recipe is None:
            yield OutputStatus(
                output_id=tree_out.output_id,
                universe_id=universe_id,
                analysis_id=tree_out.analysis_id,
                output_dir=out_dir,
                status="alias",
                manifest=None,
            )
            continue

        manifest = read_manifest(out_dir)
        if manifest is None:
            yield OutputStatus(
                output_id=tree_out.output_id,
                universe_id=universe_id,
                analysis_id=tree_out.analysis_id,
                output_dir=out_dir,
                status="missing",
                manifest=None,
            )
            continue

        current_cv = code_version(
            recipe=recipe.get("command", ""),
            container_image=_resolve_recipe_container(tree_out, spec),
            decisions=_decisions_for(tree_out, universe_decisions),
        )
        if manifest.get("code_version") != current_cv:
            yield OutputStatus(
                output_id=tree_out.output_id,
                universe_id=universe_id,
                analysis_id=tree_out.analysis_id,
                output_dir=out_dir,
                status="stale",
                manifest=manifest,
            )
            continue

        yield OutputStatus(
            output_id=tree_out.output_id,
            universe_id=universe_id,
            analysis_id=tree_out.analysis_id,
            output_dir=out_dir,
            status="ok",
            manifest=manifest,
        )
