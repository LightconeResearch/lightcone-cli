"""Manifest-driven status walker.

For each output declared in a project's ``astra.yaml``, determines
whether it is materialized, stale, missing, pre-migration, or an alias —
by reading the per-output manifest at
``<output_dir>/.lightcone-manifest.json``.

Offline and local-only by invariant: this module reads the project tree
(spec, manifests, ``pyproject.toml``) and never the network. It never
imports Snakemake — ``lc status`` works on a fresh clone with no
``.snakemake/`` directory and on frozen archives.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from astra.helpers import load_yaml, resolve_analysis_tree

from lightcone.engine.environment import EnvironmentSpec, load_environment
from lightcone.engine.manifest import code_version, is_pre_migration, read_manifest
from lightcone.engine.tree import (
    collect_tree_outputs,
    load_universe_decisions,
    resolve_output_path,
    scoped_decisions_for_output,
)

StatusLiteral = Literal["ok", "stale", "missing", "alias", "pre_migration"]


@dataclass
class OutputStatus:
    output_id: str
    universe_id: str
    analysis_id: str | None
    output_dir: Path
    status: StatusLiteral
    manifest: dict[str, Any] | None
    recipe_command: str | None


def get_output_status(
    project_path: Path,
    *,
    universe_id: str,
    env: EnvironmentSpec | None = None,
    spec: dict[str, Any] | None = None,
) -> Iterator[OutputStatus]:
    """Yield an :class:`OutputStatus` for every declared output in the project.

    The recomputed ``code_version`` mirrors the Snakefile generator's
    exactly — both call the one shared
    :func:`lightcone.engine.manifest.code_version` (via the shared
    decision scoping in :mod:`lightcone.engine.tree`), so they can
    never disagree. *env* and *spec* are loaded when omitted; callers
    iterating several universes pass them through to avoid re-parsing
    per universe.
    """
    if env is None:
        env = load_environment(project_path)
    if spec is None:
        spec = resolve_analysis_tree(
            load_yaml(project_path / "astra.yaml"), project_path
        )
    universe_decisions = load_universe_decisions(project_path, spec, universe_id)

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
                recipe_command=None,
            )
            continue

        recipe_command = recipe.get("command", "")

        manifest = read_manifest(out_dir)
        if manifest is None:
            status: StatusLiteral = "missing"
        elif is_pre_migration(manifest):
            # An earlier-schema manifest cannot be compared against the
            # current identity formula — surfaced distinctly, treated as
            # stale by materialize.
            status = "pre_migration"
        else:
            current_cv = code_version(
                recipe=recipe_command,
                decisions=scoped_decisions_for_output(
                    tree_out, universe_decisions
                ),
                env_version=env.env_version,
                writable_project=(
                    tree_out.qualified_id in env.writable_project_outputs
                ),
            )
            status = "ok" if manifest.get("code_version") == current_cv else "stale"

        yield OutputStatus(
            output_id=tree_out.output_id,
            universe_id=universe_id,
            analysis_id=tree_out.analysis_id,
            output_dir=out_dir,
            status=status,
            manifest=manifest,
            recipe_command=recipe_command,
        )


def env_blast_radius(
    project_path: Path,
    *,
    universes: list[str],
    env: EnvironmentSpec | None = None,
) -> int:
    """How many materialized outputs the current environment change stales.

    Counts manifests whose recorded ``env_version`` differs from the
    environment's current one — printed as
    "environment changed: N materialized outputs are now stale" by
    ``lc status`` and the materialize preflight, including at escalation
    time (declaring the image table IS an environment edit).
    """
    if env is None:
        env = load_environment(project_path)
    count = 0
    for u in universes:
        for s in get_output_status(project_path, universe_id=u, env=env):
            if s.manifest is None:
                continue
            recorded = s.manifest.get("env_version")
            if recorded is not None and recorded != env.env_version:
                count += 1
    return count
