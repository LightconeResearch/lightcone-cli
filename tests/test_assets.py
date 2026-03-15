"""Tests for ASTRA asset factory."""
from __future__ import annotations

import unittest.mock
from unittest.mock import MagicMock

import dagster as dg
import pytest
from astra.helpers import load_yaml

from prism.dagster.assets import (
    build_asset_definitions,
    build_definitions,
    get_external_inputs,
)


@pytest.fixture
def mock_runner():
    """Create a mock ASTRAContainerRunner."""
    return MagicMock()


@pytest.fixture
def sample_astra_yaml(tmp_path):
    """Create a sample astra.yaml with inline recipes."""
    astra_yaml = tmp_path / "astra.yaml"
    astra_yaml.write_text("""
version: "1.0"
name: "Test Analysis"
inputs:
  - id: raw_data
    type: data
outputs:
  - id: cleaned
    type: data
    recipe:
      command: python clean.py
      container: test:latest
  - id: result
    type: metric
    recipe:
      command: python analyze.py
      inputs: [cleaned]
      container: test:latest
  - id: external
    type: data
decisions: {}
""")
    return tmp_path


class TestBuildAssetDefinitions:
    def test_generates_assets_for_outputs_with_recipes(
        self, sample_astra_yaml, mock_runner,
    ):
        spec = load_yaml(sample_astra_yaml / "astra.yaml")
        assets = build_asset_definitions(spec, runner=mock_runner)
        asset_keys = {a.key.path[-1] for a in assets}
        assert "cleaned" in asset_keys
        assert "result" in asset_keys

    def test_asset_keys_scoped_by_universe(
        self, sample_astra_yaml, mock_runner,
    ):
        spec = load_yaml(sample_astra_yaml / "astra.yaml")
        assets = build_asset_definitions(
            spec, runner=mock_runner, universe_id="baseline",
        )
        for a in assets:
            assert a.key.path[0] == "baseline"

    def test_skips_outputs_without_recipes(
        self, sample_astra_yaml, mock_runner,
    ):
        spec = load_yaml(sample_astra_yaml / "astra.yaml")
        assets = build_asset_definitions(spec, runner=mock_runner)
        asset_keys = {a.key.path[-1] for a in assets}
        assert "external" not in asset_keys

    def test_asset_dependencies(self, sample_astra_yaml, mock_runner):
        spec = load_yaml(sample_astra_yaml / "astra.yaml")
        assets = build_asset_definitions(spec, runner=mock_runner)
        result_asset = next(a for a in assets if a.key.path[-1] == "result")
        # Check dependencies via specs
        dep_keys = set()
        for spec_item in result_asset.specs:
            for dep in spec_item.deps:
                dep_keys.add(dep.asset_key.path[-1])
        assert "cleaned" in dep_keys

    def test_build_definitions_returns_definitions(self, sample_astra_yaml):
        defs = build_definitions(sample_astra_yaml)
        assert isinstance(defs, dg.Definitions)

    def test_container_flags_forwarded_to_runner(self, sample_astra_yaml):
        """container_flags from target config should reach the runner's scheduler config."""
        target_config = {
            "backend": "slurm",
            "account": "m1234",
            "container_runtime": "podman-hpc",
            "container_flags": ["--scratch", "--cfs"],
        }
        with unittest.mock.patch(
            "prism.dagster.assets.ASTRAContainerRunner",
        ) as MockRunner:
            build_definitions(
                sample_astra_yaml, target_config=target_config, no_build=True,
            )
            call_kwargs = MockRunner.call_args[1]
            scheduler = call_kwargs["target_config"]["scheduler"]
            assert scheduler["container_flags"] == ["--scratch", "--cfs"]

    def test_build_spec_resolved_to_string(self, tmp_path, mock_runner):
        """Container build specs should be resolved to tag strings."""
        containerfile = tmp_path / "Containerfile"
        containerfile.write_text("FROM python:3.12-slim\n")

        spec = {
            "name": "Test",
            "container": {"build": "Containerfile"},
            "outputs": [
                {
                    "id": "result",
                    "type": "metric",
                    "recipe": {"command": "python run.py"},
                },
            ],
        }

        with unittest.mock.patch(
            "prism.container.image_exists_locally", return_value=True,
        ):
            assets = build_asset_definitions(
                spec,
                runner=mock_runner,
                project_path=tmp_path,
                project_name="Test",
            )

        assert len(assets) == 1
        # The container metadata should be a resolved tag string, not a dict
        for asset_spec in assets[0].specs:
            meta = asset_spec.metadata or {}
            container_val = meta.get("container", "")
            assert isinstance(container_val, str)
            assert "build" not in container_val  # not the raw dict


class TestGetExternalInputs:
    def test_extracts_filesystem_sources(self):
        spec = {
            "inputs": [
                {"id": "sim_data", "type": "data", "source": "/pscratch/sd/f/francois/sim_42"},
                {"id": "obs_data", "type": "data", "source": "/global/cfs/cdirs/m1234/obs"},
            ],
            "outputs": [],
        }
        result = get_external_inputs(spec)
        assert result == {
            "sim_data": "/pscratch/sd/f/francois/sim_42",
            "obs_data": "/global/cfs/cdirs/m1234/obs",
        }

    def test_ignores_non_filesystem_sources(self):
        spec = {
            "inputs": [
                {"id": "raw", "type": "data"},
                {"id": "web", "type": "data", "source": "https://example.com/data.tar"},
                {"id": "local", "type": "data", "source": "relative/path"},
            ],
            "outputs": [],
        }
        result = get_external_inputs(spec)
        assert result == {}

    def test_empty_inputs(self):
        assert get_external_inputs({"inputs": [], "outputs": []}) == {}
        assert get_external_inputs({"outputs": []}) == {}


class TestExternalInputsAssetSpecs:
    def test_external_inputs_become_asset_specs(self, mock_runner):
        spec = {
            "inputs": [
                {"id": "sim_data", "type": "data", "source": "/pscratch/sim"},
            ],
            "outputs": [
                {
                    "id": "result",
                    "type": "metric",
                    "recipe": {"command": "python run.py", "inputs": ["sim_data"]},
                },
            ],
        }
        assets = build_asset_definitions(spec, runner=mock_runner)
        # Should have 1 AssetSpec (sim_data) + 1 AssetsDefinition (result)
        specs = [a for a in assets if isinstance(a, dg.AssetSpec)]
        defs = [a for a in assets if isinstance(a, dg.AssetsDefinition)]
        assert len(specs) == 1
        assert specs[0].key.path[-1] == "sim_data"
        assert specs[0].metadata["external"] is True
        assert specs[0].metadata["source"] == "/pscratch/sim"
        assert len(defs) == 1

    def test_external_inputs_in_definitions(self, tmp_path):
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: "Test"
inputs:
  - id: sim_data
    type: data
    source: /pscratch/sim
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
      inputs: [sim_data]
""")
        defs = build_definitions(tmp_path, no_build=True)
        assert isinstance(defs, dg.Definitions)

    def test_external_inputs_passed_to_runner(self, mock_runner):
        spec = {
            "inputs": [
                {"id": "sim_data", "type": "data", "source": "/pscratch/sim"},
                {"id": "other", "type": "data"},
            ],
            "outputs": [
                {
                    "id": "result",
                    "type": "metric",
                    "recipe": {"command": "python run.py", "inputs": ["sim_data"]},
                },
            ],
        }
        mock_runner.execute.return_value = MagicMock(
            exit_code=0, output_path=None, metadata={"backend": "docker"},
        )
        assets = build_asset_definitions(spec, runner=mock_runner)
        # Materialize the recipe asset to trigger runner.execute
        recipe_asset = [a for a in assets if isinstance(a, dg.AssetsDefinition)][0]
        # Execute the inner function directly
        context = MagicMock()
        recipe_asset.op.compute_fn.decorated_fn(context)
        # Check that runner.execute was called with the right external_inputs
        call_kwargs = mock_runner.execute.call_args[1]
        assert call_kwargs["external_inputs"] == {"sim_data": "/pscratch/sim"}

    def test_no_external_inputs_passes_none(self, mock_runner):
        spec = {
            "inputs": [{"id": "raw", "type": "data"}],
            "outputs": [
                {
                    "id": "result",
                    "type": "metric",
                    "recipe": {"command": "python run.py", "inputs": ["raw"]},
                },
            ],
        }
        mock_runner.execute.return_value = MagicMock(
            exit_code=0, output_path=None, metadata={"backend": "docker"},
        )
        assets = build_asset_definitions(spec, runner=mock_runner)
        recipe_asset = [a for a in assets if isinstance(a, dg.AssetsDefinition)][0]
        context = MagicMock()
        recipe_asset.op.compute_fn.decorated_fn(context)
        call_kwargs = mock_runner.execute.call_args[1]
        assert call_kwargs["external_inputs"] is None
