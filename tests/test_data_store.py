"""Tests for DataStore input resolution."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from prism.dagster.data_store import DataStore


@pytest.fixture
def project(tmp_path):
    """Create a minimal project structure."""
    (tmp_path / "results" / "baseline").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def store(project):
    """DataStore with a cache dir in tmp_path."""
    cache = project / ".cache"
    return DataStore(project_root=project, cache_dir=cache)


class TestResolveInputLocal:
    def test_absolute_path(self, store, tmp_path):
        """Absolute paths resolve directly (backward compat)."""
        data_file = tmp_path / "external" / "catalog.hdf5"
        data_file.parent.mkdir(parents=True)
        data_file.write_bytes(b"catalog data")

        result = store.resolve_input(
            {"id": "sim_data", "source": str(data_file)},
            universe_id="baseline",
        )
        assert result.input_id == "sim_data"
        assert result.local_path == data_file
        assert result.staged is False
        assert result.source_uri == str(data_file)

    def test_relative_path(self, store, project):
        """Relative paths resolve to project root."""
        data_file = project / "data" / "input.fits"
        data_file.parent.mkdir(parents=True)
        data_file.write_bytes(b"fits data")

        result = store.resolve_input(
            {"id": "obs", "source": "./data/input.fits"},
            universe_id="baseline",
        )
        assert result.local_path == data_file.resolve()
        assert result.staged is False

    def test_missing_local_source_warns(self, store):
        """Missing local file should warn but still resolve."""
        result = store.resolve_input(
            {"id": "missing", "source": "/nonexistent/path"},
            universe_id="baseline",
        )
        assert result.local_path == Path("/nonexistent/path")
        assert result.staged is False
        assert result.verified is False

    def test_internal_input(self, store, project):
        """Inputs without a source are internal (sibling outputs)."""
        result = store.resolve_input(
            {"id": "cleaned", "type": "data"},
            universe_id="baseline",
        )
        assert result.local_path == project / "results" / "baseline" / "cleaned"
        assert result.staged is False
        assert result.source_uri == "results://baseline/cleaned"


class TestResolveInputChecksum:
    def test_checksum_verified_on_match(self, store, tmp_path):
        data = b"verified data"
        data_file = tmp_path / "ext" / "verified.dat"
        data_file.parent.mkdir(parents=True)
        data_file.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()

        result = store.resolve_input(
            {
                "id": "verified",
                "source": str(data_file),
                "checksum": {"algorithm": "sha256", "value": sha},
            },
            universe_id="baseline",
        )
        assert result.verified is True

    def test_checksum_mismatch_warns(self, store, tmp_path):
        data_file = tmp_path / "ext" / "bad.dat"
        data_file.parent.mkdir(parents=True)
        data_file.write_bytes(b"data")

        result = store.resolve_input(
            {
                "id": "bad",
                "source": str(data_file),
                "checksum": {"algorithm": "sha256", "value": "00000bad"},
            },
            universe_id="baseline",
        )
        assert result.verified is False

    def test_no_checksum_not_verified(self, store, tmp_path):
        data_file = tmp_path / "ext" / "data.dat"
        data_file.parent.mkdir(parents=True)
        data_file.write_bytes(b"data")

        result = store.resolve_input(
            {"id": "data", "source": str(data_file)},
            universe_id="baseline",
        )
        assert result.verified is False


class TestResolveInputRemote:
    def test_remote_uri_stages(self, store, tmp_path):
        """Remote URIs are staged to cache via fsspec."""
        source = tmp_path / "remote" / "data.fits"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"remote data")

        result = store.resolve_input(
            {"id": "remote", "source": f"file://{source}"},
            universe_id="baseline",
        )
        assert result.staged is True
        assert result.local_path.exists()
        assert result.local_path.read_bytes() == b"remote data"


class TestResolveExternalInputs:
    def test_returns_same_shape_as_old_function(self, store, tmp_path):
        """resolve_external_inputs returns {id: path_str} like get_external_inputs."""
        data_file = tmp_path / "ext" / "catalog.hdf5"
        data_file.parent.mkdir(parents=True)
        data_file.write_bytes(b"data")

        spec = {
            "inputs": [
                {"id": "sim_data", "type": "data", "source": str(data_file)},
                {"id": "raw", "type": "data"},  # internal — no source
            ],
            "outputs": [],
        }
        result = store.resolve_external_inputs(spec, "baseline")
        assert isinstance(result, dict)
        assert "sim_data" in result
        assert result["sim_data"] == str(data_file)
        assert "raw" not in result  # internal inputs excluded

    def test_relative_sources_resolved(self, store, project):
        """Relative sources are resolved and included."""
        data_file = project / "data" / "input.fits"
        data_file.parent.mkdir(parents=True)
        data_file.write_bytes(b"fits")

        spec = {
            "inputs": [
                {"id": "obs", "type": "data", "source": "./data/input.fits"},
            ],
            "outputs": [],
        }
        result = store.resolve_external_inputs(spec, "baseline")
        assert "obs" in result
        assert result["obs"] == str(data_file.resolve())

    def test_empty_inputs(self, store):
        assert store.resolve_external_inputs({"inputs": [], "outputs": []}, "baseline") == {}
        assert store.resolve_external_inputs({"outputs": []}, "baseline") == {}


class TestDataStoreInit:
    def test_default_cache_dir(self, project):
        ds = DataStore(project_root=project)
        assert ds.cache_dir == Path.home() / ".prism" / "cache"

    def test_custom_cache_dir(self, project, tmp_path):
        custom = tmp_path / "my_cache"
        ds = DataStore(project_root=project, cache_dir=custom)
        assert ds.cache_dir == custom
