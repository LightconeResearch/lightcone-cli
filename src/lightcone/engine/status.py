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

from lightcone.engine.container import make_image_tag_resolver
from lightcone.engine.manifest import code_version, read_manifest
from lightcone.engine.tree import (
    TreeOutput,
    collect_tree_outputs,
    resolve_container_spec,
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


def _decisions_for(
    tree_output: TreeOutput,
    universe_decisions: dict[str, Any],
) -> dict[str, Any]:
    """Return the decisions visible to a given output for code_version
    computation.

    v0.0.7: ``Output.decisions`` is the explicit provenance contract —
    the set of decisions whose option choices can change this output.
    The Snakefile generator hashes only those into ``code_version``;
    we mirror that scoping here so ``lc status`` stays in sync.
    Outputs that do not declare decisions hash an empty dict.
    """
    declared = tree_output.output_def.get("decisions") or []
    if not declared:
        return {}
    scoped: dict[str, Any] = {}
    prefix = f"{tree_output.analysis_id}." if tree_output.analysis_id else ""
    for dec_id in declared:
        if prefix and (qualified := f"{prefix}{dec_id}") in universe_decisions:
            scoped[dec_id] = universe_decisions[qualified]
        elif dec_id in universe_decisions:
            scoped[dec_id] = universe_decisions[dec_id]
    return scoped


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
    project_name = (spec.get("name") or project_path.name).lower().replace(" ", "-")
    resolve_image = make_image_tag_resolver(project_path, project_name)

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

        # Mirror the snakefile generator's image-tag resolution so the
        # recomputed code_version matches what was written into the
        # manifest at run time.
        image_tag = resolve_image(resolve_container_spec(tree_out, spec))
        current_cv = code_version(
            recipe=recipe.get("command", ""),
            container_image=image_tag,
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
