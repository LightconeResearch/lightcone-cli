"""Analysis tree helpers — walk resolved sub-analysis trees.

After ``resolve_analysis_tree()`` from astra.helpers expands ``path:``
references, this module provides utilities to:

- Collect all outputs across the tree (with their sub-analysis context)
- Resolve ``from:`` references on inputs to concrete output paths
- Resolve ``from:`` references on decisions to parent decision values
- Build merged decision dicts from composable universe files
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astra.helpers import get_inputs, get_outputs, load_yaml

logger = logging.getLogger(__name__)


@dataclass
class TreeOutput:
    """An output from the resolved analysis tree, with its sub-analysis context."""

    output_id: str
    output_def: dict[str, Any]
    analysis_id: str | None  # None for root-level outputs
    analysis_path: str | None  # relative path, e.g. "./analyses/hod_fitting"
    analysis_spec: dict[str, Any]  # the sub-analysis spec dict


def collect_tree_outputs(spec: dict[str, Any]) -> list[TreeOutput]:
    """Walk the resolved tree and collect all outputs with context.

    Returns outputs from root level (analysis_id=None) and from each
    sub-analysis. Root-level outputs with ``from:`` pointing to a
    sub-analysis output are included but flagged for alias handling.
    """
    results: list[TreeOutput] = []

    # Root-level outputs
    for out in get_outputs(spec):
        results.append(TreeOutput(
            output_id=out.get("id", ""),
            output_def=out,
            analysis_id=None,
            analysis_path=None,
            analysis_spec=spec,
        ))

    # Sub-analysis outputs
    for analysis_id, analysis_node in (spec.get("analyses") or {}).items():
        sub_path = analysis_node.get("path")
        for out in get_outputs(analysis_node):
            results.append(TreeOutput(
                output_id=out.get("id", ""),
                output_def=out,
                analysis_id=analysis_id,
                analysis_path=sub_path,
                analysis_spec=analysis_node,
            ))

    return results


def collect_tree_inputs(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect all inputs from root and sub-analyses.

    Returns {qualified_id: input_def} where qualified_id is:
    - "input_id" for root inputs
    - "analysis_id.input_id" for sub-analysis inputs
    """
    result: dict[str, dict[str, Any]] = {}

    for inp in get_inputs(spec):
        inp_id = inp.get("id", "")
        if inp_id:
            result[inp_id] = inp

    for analysis_id, analysis_node in (spec.get("analyses") or {}).items():
        for inp in get_inputs(analysis_node):
            inp_id = inp.get("id", "")
            if inp_id:
                result[f"{analysis_id}.{inp_id}"] = inp

    return result


def resolve_universe_decisions(
    project_path: Path,
    spec: dict[str, Any],
    universe_id: str,
) -> dict[str, Any]:
    """Load and merge universe decisions from root and sub-analysis universes.

    Returns a flat dict of all decisions for execution:
    - Root decisions from ``universes/<universe_id>.yaml``
    - Sub-analysis decisions from ``<sub_path>/universes/<sub_universe_id>.yaml``
    - ``from:`` decisions in sub-analyses are resolved to parent values

    The returned dict uses qualified keys for sub-analysis decisions:
    ``{analysis_id}.{decision_id}`` to avoid collisions.
    """
    # Load root universe
    root_universe_file = project_path / "universes" / f"{universe_id}.yaml"
    root_decisions: dict[str, Any] = {}
    sub_universe_refs: dict[str, str] = {}

    if root_universe_file.exists():
        root_data = load_yaml(root_universe_file)
        root_decisions = root_data.get("decisions", {})
        # Parse sub-analysis universe references
        for analysis_id, ref in (root_data.get("analyses") or {}).items():
            if isinstance(ref, dict) and ref.get("universe"):
                sub_universe_refs[analysis_id] = ref["universe"]

    merged: dict[str, Any] = dict(root_decisions)

    # Load sub-analysis universes
    for analysis_id, analysis_node in (spec.get("analyses") or {}).items():
        sub_path = analysis_node.get("path")
        if not sub_path:
            continue

        sub_universe_id = sub_universe_refs.get(analysis_id, universe_id)
        sub_dir = (project_path / sub_path).resolve()
        sub_universe_file = sub_dir / "universes" / f"{sub_universe_id}.yaml"

        if sub_universe_file.exists():
            sub_data = load_yaml(sub_universe_file)
            sub_decisions = sub_data.get("decisions", {})
        else:
            sub_decisions = {}

        # Resolve from: references in sub-analysis decisions
        for decision_id, decision_def in (analysis_node.get("decisions") or {}).items():
            if isinstance(decision_def, dict) and decision_def.get("from"):
                from_ref = decision_def["from"]
                # ../parent_decision -> look up in root decisions
                if from_ref.startswith("../"):
                    parent_key = from_ref[3:]
                    if parent_key in root_decisions:
                        merged[f"{analysis_id}.{decision_id}"] = root_decisions[parent_key]
                    else:
                        logger.warning(
                            "Decision '%s' in '%s' references '%s' which is not in "
                            "root universe decisions",
                            decision_id, analysis_id, from_ref,
                        )
            elif decision_id in sub_decisions:
                merged[f"{analysis_id}.{decision_id}"] = sub_decisions[decision_id]

        # Also add un-referenced local decisions from the sub-universe
        for decision_id, value in sub_decisions.items():
            key = f"{analysis_id}.{decision_id}"
            if key not in merged:
                merged[key] = value

    return merged


def get_decisions_for_analysis(
    merged_decisions: dict[str, Any],
    analysis_id: str | None,
) -> dict[str, Any]:
    """Extract the decisions relevant to a specific analysis from merged dict.

    For root (analysis_id=None): returns unqualified keys.
    For sub-analysis: returns decisions with matching prefix, stripped to local names.
    Also includes root-level decisions (for from: references).
    """
    if analysis_id is None:
        # Root analysis: return all unqualified keys
        return {k: v for k, v in merged_decisions.items() if "." not in k}

    prefix = f"{analysis_id}."
    result: dict[str, Any] = {}

    # Add qualified decisions with prefix stripped
    for k, v in merged_decisions.items():
        if k.startswith(prefix):
            local_key = k[len(prefix):]
            result[local_key] = v

    return result


def resolve_output_path(
    project_path: Path,
    tree_output: TreeOutput,
    universe_id: str,
) -> Path:
    """Resolve the results directory for an output.

    Root outputs: ``results/<universe_id>/``
    Sub-analysis outputs: ``<sub_path>/results/<universe_id>/``
    """
    if tree_output.analysis_path:
        return (project_path / tree_output.analysis_path).resolve() / "results" / universe_id
    return project_path / "results" / universe_id


def resolve_container_spec(
    tree_output: TreeOutput,
    root_spec: dict[str, Any],
) -> str | None:
    """Pick the container declaration in priority order:
    recipe-level > sub-analysis-level > root-level.
    Returns the raw spec string (Containerfile path or registry image
    tag), or ``None`` when no container is declared at any level.
    """
    recipe = tree_output.output_def.get("recipe") or {}
    if "container" in recipe:
        return recipe["container"]  # type: ignore[no-any-return]
    if tree_output.analysis_id is not None:
        sub = tree_output.analysis_spec.get("container")
        if sub is not None:
            return sub  # type: ignore[no-any-return]
    return root_spec.get("container")


def find_upstream_output(
    consumer: TreeOutput,
    inp_id: str,
    all_outputs: list[TreeOutput],
) -> TreeOutput | None:
    """Resolve a recipe input id to the producing :class:`TreeOutput`, if any.

    Mirrors the lookup the Snakefile generator does for ``rule.input``:

    * dotted ``"<analysis_id>.<output_id>"`` → match by qualified key
    * inside a sub-analysis, bare ``inp_id`` → first try
      ``"<consumer.analysis_id>.<inp_id>"``, then bare
    * ``inp_id`` referencing an analysis-level input with a ``from:``
      pointing at a sibling output → resolve through that

    Returns ``None`` for inputs that refer to external files (no upstream
    rule produces them).
    """
    by_qualified: dict[str, TreeOutput] = {}
    by_bare: dict[str, TreeOutput] = {}
    for to in all_outputs:
        if to.output_def.get("recipe") is None:
            continue
        if to.analysis_id is not None:
            by_qualified[f"{to.analysis_id}.{to.output_id}"] = to
        else:
            by_qualified[to.output_id] = to
        by_bare.setdefault(to.output_id, to)

    if "." in inp_id and inp_id in by_qualified:
        return by_qualified[inp_id]

    if consumer.analysis_id is not None:
        qualified = f"{consumer.analysis_id}.{inp_id}"
        if qualified in by_qualified:
            return by_qualified[qualified]

    if inp_id in by_qualified:
        return by_qualified[inp_id]

    # ``from:`` redirects on the consumer's analysis-level inputs.
    analysis_inputs = {i.get("id"): i for i in get_inputs(consumer.analysis_spec)}
    inp_def = analysis_inputs.get(inp_id)
    if inp_def and inp_def.get("from"):
        ref = inp_def["from"].removeprefix("../").removeprefix("/")
        if ref in by_qualified:
            return by_qualified[ref]
        if "." not in ref and ref in by_bare:
            return by_bare[ref]

    return None


def resolve_input_path(
    project_path: Path,
    spec: dict[str, Any],
    from_ref: str,
    universe_id: str,
) -> str | None:
    """Resolve a ``from:`` reference on an input to a concrete filesystem path.

    Handles:
    - ``../parent_input`` -> root input's source
    - ``../sibling.output_id`` -> sibling sub-analysis's results path
    - ``sibling.output_id`` -> sibling sub-analysis's results path (no ../ needed at root)
    """
    # Strip leading ../ if present
    ref = from_ref.removeprefix("../")
    ref = ref.removeprefix("/")

    # Check if it's a root input reference
    for inp in get_inputs(spec):
        if inp.get("id") == ref:
            source = inp.get("source")
            if source and isinstance(source, str) and source.startswith("/"):
                return source
            return None

    # Check if it's a sibling.output_id reference
    if "." in ref:
        analysis_id, output_id = ref.split(".", 1)
        analyses = spec.get("analyses") or {}
        if analysis_id in analyses:
            sub_node = analyses[analysis_id]
            sub_path = sub_node.get("path")
            if sub_path:
                return str(
                    (project_path / sub_path).resolve()
                    / "results" / universe_id / output_id
                )

    return None


