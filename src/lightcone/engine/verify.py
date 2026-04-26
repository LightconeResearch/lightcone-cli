"""Integrity verification for materialized outputs.

For each output with a manifest, recomputes the on-disk data_version and
walks the recorded input_versions back through the upstream chain. Three
failure modes:

- ``missing_manifest``: a result directory exists but has no manifest, or
  the manifest is unparseable. This is the agent-forged-file scenario.
- ``tampered_data``: the manifest is present and well-formed, but the
  bytes on disk no longer hash to the recorded ``data_version``.
- ``broken_chain``: the recorded ``input_versions`` reference an upstream
  output whose own ``data_version`` no longer matches.

Like ``status``, this module never imports Snakemake.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from astra.helpers import load_yaml, resolve_analysis_tree

from lightcone.engine.manifest import read_manifest, sha256_dir
from lightcone.engine.tree import collect_tree_outputs, resolve_output_path

FailureKind = Literal["missing_manifest", "tampered_data", "broken_chain"]


@dataclass
class VerifyResult:
    output_id: str
    universe_id: str
    output_dir: Path
    passed: bool
    failure: FailureKind | None
    detail: str | None = None


def verify_outputs(
    project_path: Path,
    *,
    universe_id: str,
) -> Iterator[VerifyResult]:
    """Yield a :class:`VerifyResult` for every output with a recipe."""
    spec = resolve_analysis_tree(load_yaml(project_path / "astra.yaml"), project_path)

    for tree_out in collect_tree_outputs(spec):
        # Aliases have no own materialization to verify.
        if tree_out.output_def.get("recipe") is None:
            continue
        out_dir = resolve_output_path(project_path, tree_out, universe_id) / tree_out.output_id

        if not out_dir.exists():
            # Not a verification failure — there's just nothing materialized
            # to verify. ``lc status`` is the right report for that.
            continue

        manifest = read_manifest(out_dir)
        if manifest is None:
            yield VerifyResult(
                output_id=tree_out.output_id,
                universe_id=universe_id,
                output_dir=out_dir,
                passed=False,
                failure="missing_manifest",
                detail="No manifest found at output directory",
            )
            continue

        actual_dv = sha256_dir(out_dir)
        if actual_dv != manifest.get("data_version"):
            yield VerifyResult(
                output_id=tree_out.output_id,
                universe_id=universe_id,
                output_dir=out_dir,
                passed=False,
                failure="tampered_data",
                detail=(
                    f"recorded {manifest.get('data_version')!r} != "
                    f"actual {actual_dv!r}"
                ),
            )
            continue

        # Walk the recorded chain.
        chain_failure: str | None = None
        recipe_inputs = (tree_out.output_def.get("recipe") or {}).get("inputs") or []
        for inp_id in recipe_inputs:
            recorded = manifest.get("input_versions", {}).get(inp_id)
            if recorded is None:
                # The manifest doesn't even know about an upstream the
                # current spec declares — broken chain.
                chain_failure = f"input '{inp_id}' missing from manifest"
                break
            # Find the upstream output's current materialized state. We
            # accept either: (a) it's a sibling output we can locate via
            # the tree, or (b) it's an external input (then recorded is
            # an mtime-size or sha256 fingerprint, no chain to walk).
            up_dir = _find_upstream_dir(project_path, spec, inp_id, universe_id)
            if up_dir is None:
                continue  # external; nothing to chain to
            up_manifest = read_manifest(up_dir)
            if up_manifest is None:
                chain_failure = f"upstream '{inp_id}' missing manifest"
                break
            if up_manifest.get("data_version") != recorded:
                chain_failure = (
                    f"upstream '{inp_id}' data_version drifted"
                )
                break

        if chain_failure:
            yield VerifyResult(
                output_id=tree_out.output_id,
                universe_id=universe_id,
                output_dir=out_dir,
                passed=False,
                failure="broken_chain",
                detail=chain_failure,
            )
            continue

        yield VerifyResult(
            output_id=tree_out.output_id,
            universe_id=universe_id,
            output_dir=out_dir,
            passed=True,
            failure=None,
        )


def _find_upstream_dir(
    project_path: Path,
    spec: dict,
    inp_id: str,
    universe_id: str,
) -> Path | None:
    """Locate the materialized output directory for an input id, if it
    refers to a sibling output rather than an external input.
    """
    for tree_out in collect_tree_outputs(spec):
        if tree_out.output_id == inp_id and tree_out.output_def.get("recipe") is not None:
            return resolve_output_path(project_path, tree_out, universe_id) / inp_id
    return None
