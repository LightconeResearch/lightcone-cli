"""Analysis tree helpers — walk resolved sub-analysis trees.

After ``resolve_analysis_tree()`` from astra.helpers expands ``path:``
references, this module provides utilities to:

- Collect all outputs across the tree (with their sub-analysis context).
- Resolve declared ``Output.inputs`` IDs to concrete upstream artifacts
  (sibling outputs, parent inputs reached via ``from:`` aliases, or
  external dataset paths).
- Resolve sub-analysis decisions to merged universe values, honouring
  the v0.0.7 ``from: ../id`` (and ``../../id``) grammar.
- Pick the right ``container:`` declaration for an output (recipe →
  sub-analysis → root).

ASTRA v0.0.7 (`from:` aliasing) reshapes how Inputs/Outputs/Decisions
reference each other:

* ``Input.from``  uses ``../id`` for an ancestor input,
                  ``../../id`` for a grandparent,
                  ``../sibling.out_id`` for a sibling sub's output.
* ``Output.from`` is a re-export and uses ``child.out_id``
                  (own child sub) or deeper.
* ``Decision.from`` is upward only: ``../id``, ``../../id``, …

Aliased nodes carry only ``id`` + ``from`` (+ optional ``when``); the
content is inherited from the target.
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
    sub-analysis. Outputs declared with ``from:`` (re-exports) are
    included; consumers that care only about materializable outputs
    should filter on ``recipe is not None``.
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


def _strip_up_prefix(ref: str) -> tuple[int, str]:
    """Split a ``from:`` path into (up_levels, remainder).

    ``../foo`` -> (1, "foo")
    ``../../foo`` -> (2, "foo")
    ``foo`` -> (0, "foo")
    """
    up = 0
    while ref.startswith("../"):
        up += 1
        ref = ref[3:]
    return up, ref


def resolve_universe_decisions(
    project_path: Path,
    spec: dict[str, Any],
    universe_id: str,
) -> dict[str, Any]:
    """Load and merge universe decisions from root and sub-analysis universes.

    Returns a flat dict of all decisions for execution:
    - Root decisions from ``universes/<universe_id>.yaml``
    - Sub-analysis decisions from ``<sub_path>/universes/<sub_universe_id>.yaml``
    - ``from:`` decisions in sub-analyses are resolved to ancestor values

    The returned dict uses qualified keys for sub-analysis decisions:
    ``{analysis_id}.{decision_id}`` to avoid collisions.

    v0.0.7 ``from:`` grammar: ``../id`` walks one scope up, ``../../id``
    walks two. We currently model a 2-level tree (root + sub), so any
    ``../`` count above 1 falls off the top and is logged as a warning.
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
                up, target = _strip_up_prefix(from_ref)
                if up == 1 and target in root_decisions:
                    merged[f"{analysis_id}.{decision_id}"] = root_decisions[target]
                else:
                    logger.warning(
                        "Decision '%s' in '%s' references '%s' which is not "
                        "resolvable in the universe (only ../<root_id> is "
                        "currently supported)",
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
    """Resolve a declared ``Output.inputs`` id to the producing :class:`TreeOutput`.

    Per v0.0.7, ``Output.inputs`` references use plain artifact IDs and
    resolve through the surrounding analysis scope: a sibling output, a
    local input, or a local input that is itself a ``from:`` alias of
    something further up.

    Returns ``None`` for inputs that resolve to external sources (no
    upstream rule produces them) — :func:`resolve_external_input`
    handles those.
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

    # Resolve through ``from:`` aliases on the consumer's analysis-level
    # inputs. v0.0.7 grammar: ``../id`` (parent input), ``../../id``
    # (grandparent), ``../sibling.out_id`` (sibling sub-analysis output).
    analysis_inputs = {i.get("id"): i for i in get_inputs(consumer.analysis_spec)}
    inp_def = analysis_inputs.get(inp_id)
    if inp_def and inp_def.get("from"):
        _, target = _strip_up_prefix(inp_def["from"])
        if target in by_qualified:
            return by_qualified[target]
        if "." not in target and target in by_bare:
            return by_bare[target]

    return None


def resolve_external_input(
    consumer: TreeOutput,
    inp_id: str,
    root_spec: dict[str, Any],
) -> str | None:
    """Resolve a declared ``Output.inputs`` id to an external source string.

    Used when the input is not produced by another rule (so
    :func:`find_upstream_output` returned ``None``). Walks the
    surrounding-scope ``Input`` declarations to find one matching
    ``inp_id``; if it has a ``source:``, returns that. If it has a
    ``from:`` alias, walks one hop further to the source.

    Returns ``None`` when the id is unresolvable. Recipes that reference
    such an id via ``{inputs.<id>}`` will surface a runtime ``KeyError``
    — that scenario is also caught by ``astra validate``.
    """
    # Inputs visible to the consumer: the surrounding analysis's own
    # inputs, plus root inputs when the consumer is at root.
    analysis_inputs = {i.get("id"): i for i in get_inputs(consumer.analysis_spec)}
    inp_def = analysis_inputs.get(inp_id)

    # If the consumer is in a sub-analysis and the bare id isn't a
    # local Input there, also try the root inputs (a ``../id`` alias
    # would have already redirected us, but plain id resolution per the
    # spec walks the scope chain).
    if inp_def is None and consumer.analysis_id is not None:
        root_inputs = {i.get("id"): i for i in get_inputs(root_spec)}
        inp_def = root_inputs.get(inp_id)
    if inp_def is None:
        return None

    # Direct source.
    src = inp_def.get("source")
    if isinstance(src, str) and src:
        return src

    # ``from:`` alias to an ancestor input — walk one hop.
    from_ref = inp_def.get("from")
    if isinstance(from_ref, str) and from_ref:
        _, target = _strip_up_prefix(from_ref)
        # Only resolve to root inputs (parent of a sub). Sibling-output
        # references would have been handled by find_upstream_output.
        if "." not in target:
            root_inputs = {i.get("id"): i for i in get_inputs(root_spec)}
            target_def = root_inputs.get(target)
            if target_def is not None:
                target_src = target_def.get("source")
                if isinstance(target_src, str) and target_src:
                    return target_src
    return None


__all__ = [
    "TreeOutput",
    "collect_tree_inputs",
    "collect_tree_outputs",
    "find_upstream_output",
    "get_decisions_for_analysis",
    "resolve_container_spec",
    "resolve_external_input",
    "resolve_output_path",
    "resolve_universe_decisions",
]
