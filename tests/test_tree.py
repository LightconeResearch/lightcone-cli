"""Tests for analysis tree helpers (sub-analysis support)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
import pytest
from astra.helpers import load_yaml, resolve_analysis_tree
from conftest import materialize_via_dagster

from prism.dagster.assets import build_asset_definitions, build_definitions
from prism.dagster.status import get_output_status
from prism.dagster.tree import (
    TreeOutput,
    collect_tree_outputs,
    get_decisions_for_analysis,
    resolve_input_path,
    resolve_output_path,
    resolve_universe_decisions,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sub_analysis_project(tmp_path):
    """Create a project with sub-analyses using path: references."""
    # Root astra.yaml
    (tmp_path / "astra.yaml").write_text("""
version: "1.0"
name: "Test Pipeline"
inputs:
  - id: raw_data
    type: data
    source: /data/raw
decisions:
  z_min:
    label: "Min redshift"
    default: z04
    options:
      z04: { label: "z=0.4" }
      z06: { label: "z=0.6" }
analyses:
  stage_a:
    path: ./analyses/stage_a
  stage_b:
    path: ./analyses/stage_b
outputs:
  - id: final_result
    type: data
    from: stage_b.result
""")

    # Stage A sub-analysis
    stage_a = tmp_path / "analyses" / "stage_a"
    stage_a.mkdir(parents=True)
    (stage_a / "astra.yaml").write_text("""
version: "1.0"
name: "Stage A"
inputs:
  - id: raw_data
    type: data
    from: ../raw_data
decisions:
  z_min:
    from: ../z_min
  method:
    label: "Method"
    default: fast
    options:
      fast: { label: "Fast" }
      slow: { label: "Slow" }
outputs:
  - id: intermediate
    type: data
    recipe:
      command: python scripts/process.py
  - id: validation_plot
    type: figure
""")
    (stage_a / "universes").mkdir()
    (stage_a / "universes" / "baseline.yaml").write_text(
        "id: baseline\ndecisions:\n  method: fast\n"
    )

    # Stage B sub-analysis
    stage_b = tmp_path / "analyses" / "stage_b"
    stage_b.mkdir(parents=True)
    (stage_b / "astra.yaml").write_text("""
version: "1.0"
name: "Stage B"
inputs:
  - id: input_data
    type: data
    from: ../stage_a.intermediate
decisions:
  z_min:
    from: ../z_min
  threshold:
    label: "Threshold"
    default: "0.5"
    options:
      "0.5": { label: "0.5" }
      "0.9": { label: "0.9" }
outputs:
  - id: result
    type: data
    recipe:
      command: python scripts/analyze.py
      inputs: [input_data]
""")
    (stage_b / "universes").mkdir()
    (stage_b / "universes" / "baseline.yaml").write_text(
        "id: baseline\ndecisions:\n  threshold: '0.5'\n"
    )

    # Root universes
    (tmp_path / "universes").mkdir()
    (tmp_path / "universes" / "baseline.yaml").write_text("""
id: baseline
decisions:
  z_min: z04
analyses:
  stage_a:
    universe: baseline
  stage_b:
    universe: baseline
""")

    return tmp_path


@pytest.fixture
def resolved_spec(sub_analysis_project):
    """Load and resolve the sub-analysis tree."""
    spec = load_yaml(sub_analysis_project / "astra.yaml")
    return resolve_analysis_tree(spec, sub_analysis_project)


# ---------------------------------------------------------------------------
# collect_tree_outputs
# ---------------------------------------------------------------------------


class TestCollectTreeOutputs:
    def test_collects_root_and_sub_outputs(self, resolved_spec):
        outputs = collect_tree_outputs(resolved_spec)
        ids = [(o.analysis_id, o.output_id) for o in outputs]
        # Root output
        assert (None, "final_result") in ids
        # Stage A outputs
        assert ("stage_a", "intermediate") in ids
        assert ("stage_a", "validation_plot") in ids
        # Stage B output
        assert ("stage_b", "result") in ids

    def test_output_has_analysis_path(self, resolved_spec):
        outputs = collect_tree_outputs(resolved_spec)
        stage_a_out = next(o for o in outputs if o.analysis_id == "stage_a")
        assert stage_a_out.analysis_path == "./analyses/stage_a"

    def test_root_output_has_no_analysis_id(self, resolved_spec):
        outputs = collect_tree_outputs(resolved_spec)
        root_out = next(o for o in outputs if o.output_id == "final_result")
        assert root_out.analysis_id is None
        assert root_out.analysis_path is None


# ---------------------------------------------------------------------------
# resolve_universe_decisions
# ---------------------------------------------------------------------------


class TestResolveUniverseDecisions:
    def test_merges_root_and_sub_decisions(
        self, sub_analysis_project, resolved_spec,
    ):
        merged = resolve_universe_decisions(
            sub_analysis_project, resolved_spec, "baseline",
        )
        # Root decision
        assert merged["z_min"] == "z04"
        # Sub-analysis local decisions
        assert merged["stage_a.method"] == "fast"
        assert merged["stage_b.threshold"] == "0.5"

    def test_from_decisions_resolve_to_parent(
        self, sub_analysis_project, resolved_spec,
    ):
        merged = resolve_universe_decisions(
            sub_analysis_project, resolved_spec, "baseline",
        )
        # from: ../z_min should resolve to root z_min value
        assert merged["stage_a.z_min"] == "z04"
        assert merged["stage_b.z_min"] == "z04"


class TestGetDecisionsForAnalysis:
    def test_root_decisions(self, sub_analysis_project, resolved_spec):
        merged = resolve_universe_decisions(
            sub_analysis_project, resolved_spec, "baseline",
        )
        root = get_decisions_for_analysis(merged, None)
        assert root == {"z_min": "z04"}

    def test_sub_analysis_decisions(
        self, sub_analysis_project, resolved_spec,
    ):
        merged = resolve_universe_decisions(
            sub_analysis_project, resolved_spec, "baseline",
        )
        stage_a = get_decisions_for_analysis(merged, "stage_a")
        assert "method" in stage_a
        assert "z_min" in stage_a
        assert stage_a["method"] == "fast"
        assert stage_a["z_min"] == "z04"


# ---------------------------------------------------------------------------
# resolve_output_path
# ---------------------------------------------------------------------------


class TestResolveOutputPath:
    def test_root_output_path(self, sub_analysis_project, resolved_spec):
        outputs = collect_tree_outputs(resolved_spec)
        root_out = next(o for o in outputs if o.output_id == "final_result")
        path = resolve_output_path(sub_analysis_project, root_out, "baseline")
        assert path == sub_analysis_project / "results" / "baseline"

    def test_sub_output_path(self, sub_analysis_project, resolved_spec):
        outputs = collect_tree_outputs(resolved_spec)
        stage_a_out = next(
            o for o in outputs
            if o.analysis_id == "stage_a" and o.output_id == "intermediate"
        )
        path = resolve_output_path(sub_analysis_project, stage_a_out, "baseline")
        expected = (
            sub_analysis_project / "analyses" / "stage_a" / "results" / "baseline"
        )
        assert path == expected


# ---------------------------------------------------------------------------
# resolve_input_path
# ---------------------------------------------------------------------------


class TestResolveInputPath:
    def test_parent_input_reference(self, resolved_spec, sub_analysis_project):
        result = resolve_input_path(
            sub_analysis_project, resolved_spec, "../raw_data", "baseline",
        )
        assert result == "/data/raw"

    def test_sibling_output_reference(self, resolved_spec, sub_analysis_project):
        result = resolve_input_path(
            sub_analysis_project, resolved_spec,
            "../stage_a.intermediate", "baseline",
        )
        expected = str(
            (sub_analysis_project / "analyses" / "stage_a").resolve()
            / "results" / "baseline" / "intermediate"
        )
        assert result == expected


# ---------------------------------------------------------------------------
# build_asset_definitions with sub-analyses
# ---------------------------------------------------------------------------


class TestSubAnalysisAssets:
    def test_creates_assets_for_sub_analysis_outputs(
        self, sub_analysis_project, resolved_spec,
    ):
        runner = MagicMock()
        assets = build_asset_definitions(
            resolved_spec, runner=runner,
            project_path=sub_analysis_project,
        )
        keys = set()
        for a in assets:
            if isinstance(a, dg.AssetSpec):
                keys.add(tuple(a.key.path))
            else:
                for spec in a.specs:
                    keys.add(tuple(spec.key.path))

        # Sub-analysis assets should have 3-part keys
        assert ("baseline", "stage_a", "intermediate") in keys
        assert ("baseline", "stage_b", "result") in keys

    def test_root_alias_output_created(
        self, sub_analysis_project, resolved_spec,
    ):
        runner = MagicMock()
        assets = build_asset_definitions(
            resolved_spec, runner=runner,
            project_path=sub_analysis_project,
        )
        # Find the alias AssetSpec for final_result
        alias_specs = [
            a for a in assets
            if isinstance(a, dg.AssetSpec) and a.key.path[-1] == "final_result"
        ]
        assert len(alias_specs) == 1
        alias = alias_specs[0]
        assert alias.metadata.get("alias_for") == "stage_b.result"

    def test_cross_sub_analysis_dependency(
        self, sub_analysis_project, resolved_spec,
    ):
        """Stage B's result should depend on stage_a.intermediate."""
        runner = MagicMock()
        assets = build_asset_definitions(
            resolved_spec, runner=runner,
            project_path=sub_analysis_project,
        )
        # Find stage_b/result asset
        result_asset = None
        for a in assets:
            if isinstance(a, dg.AssetsDefinition):
                for spec in a.specs:
                    if spec.key.path == ["baseline", "stage_b", "result"]:
                        result_asset = a
                        break
        assert result_asset is not None
        dep_keys = set()
        for spec in result_asset.specs:
            for dep in spec.deps:
                dep_keys.add(tuple(dep.asset_key.path))
        assert ("baseline", "stage_a", "intermediate") in dep_keys

    def test_build_definitions_with_sub_analyses(self, sub_analysis_project):
        defs = build_definitions(sub_analysis_project, no_build=True)
        assert isinstance(defs, dg.Definitions)


# ---------------------------------------------------------------------------
# Status with sub-analyses
# ---------------------------------------------------------------------------


class TestSubAnalysisStatus:
    def test_status_includes_sub_analysis_outputs(
        self, sub_analysis_project,
    ):
        status = get_output_status(sub_analysis_project, "baseline")
        # Sub-analysis outputs should be qualified
        assert "stage_a/intermediate" in status
        assert "stage_b/result" in status

    def test_status_pending_for_unmaterialized(
        self, sub_analysis_project,
    ):
        status = get_output_status(sub_analysis_project, "baseline")
        assert status["stage_a/intermediate"] == "pending"
        assert status["stage_b/result"] == "pending"

    def test_status_no_recipe_for_validation_plot(
        self, sub_analysis_project,
    ):
        status = get_output_status(sub_analysis_project, "baseline")
        assert status["stage_a/validation_plot"] == "no_recipe"

    def test_alias_output_status(self, sub_analysis_project):
        status = get_output_status(sub_analysis_project, "baseline")
        assert status["final_result"] == "alias"

    def test_materialized_sub_output(self, sub_analysis_project):
        instance = dg.DagsterInstance.ephemeral()

        # Materialize with the 3-part key
        @dg.asset(name="intermediate", key_prefix=["baseline", "stage_a"])
        def _trivial():
            return dg.MaterializeResult()

        dg.materialize([_trivial], instance=instance)

        status = get_output_status(
            sub_analysis_project, "baseline", instance=instance,
        )
        assert status["stage_a/intermediate"] == "materialized"


# ---------------------------------------------------------------------------
# Backward compatibility — flat spec (no sub-analyses)
# ---------------------------------------------------------------------------


class TestFlatSpecCompatibility:
    def test_flat_spec_unchanged(self, tmp_path):
        """A spec without analyses should work exactly as before."""
        (tmp_path / "astra.yaml").write_text("""
version: "1.0"
name: "Flat Test"
inputs: []
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
""")
        runner = MagicMock()
        spec = load_yaml(tmp_path / "astra.yaml")
        spec = resolve_analysis_tree(spec, tmp_path)
        assets = build_asset_definitions(spec, runner=runner)
        keys = set()
        for a in assets:
            if isinstance(a, dg.AssetsDefinition):
                for s in a.specs:
                    keys.add(tuple(s.key.path))
        assert ("baseline", "result") in keys

    def test_flat_status_unchanged(self, tmp_path):
        (tmp_path / "astra.yaml").write_text("""
version: "1.0"
name: "Flat Test"
inputs: []
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
""")
        status = get_output_status(tmp_path, "baseline")
        assert status["result"] == "pending"
