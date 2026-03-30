"""Tests for target and user configuration."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from prism.dagster.targets import (
    filter_qos_by_hardware,
    get_allowed_qos_names,
    get_config_path,
    get_qos_entry,
    is_cache_stale,
    list_targets,
    load_cluster_cache,
    load_target,
    load_user_config,
    migrate_target,
    resolve_run_config,
    save_cluster_cache,
    save_target,
    save_user_config,
)


@pytest.fixture
def targets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    targets = tmp_path / "targets"
    targets.mkdir()
    monkeypatch.setattr("prism.dagster.targets.get_targets_dir", lambda: targets)
    return targets


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr("prism.dagster.targets.get_cache_dir", lambda: cache)
    return cache


@pytest.fixture
def sample_target() -> dict:
    return {
        "site": "perlmutter",
        "backend": "slurm",
        "connection": {
            "hostname": "perlmutter.nersc.gov",
            "username": "testuser",
        },
        "account": "m1234",
        "container_runtime": "podman-hpc",
        "constraint": "gpu",
        "qos": "debug",
        "max_nodes": 4,
        "max_walltime_minutes": 360,
        "max_concurrent_jobs": 8,
    }


@pytest.fixture
def new_format_target() -> dict:
    return {
        "site": "perlmutter",
        "backend": "slurm",
        "connection": {
            "hostname": "perlmutter.nersc.gov",
            "username": "testuser",
        },
        "account": "m1234",
        "container_runtime": "podman-hpc",
        "defaults": {
            "qos": "gpu_debug",
            "constraint": "gpu",
            "time_limit": "30m",
        },
        "qos": [
            {"name": "gpu_debug", "constraint": "gpu",
             "use_for": "quick iteration, testing"},
            {"name": "gpu_regular", "constraint": "gpu",
             "use_for": "production runs, large jobs"},
            {"name": "gpu_preempt", "constraint": "gpu",
             "use_for": "cheap batch jobs, restartable"},
            {"name": "debug", "constraint": "cpu",
             "use_for": "quick CPU tests"},
            {"name": "regular_1", "constraint": "cpu",
             "use_for": "large CPU jobs"},
        ],
        "resource_limits": {
            "max_nodes": 4,
            "max_walltime_minutes": 360,
            "max_concurrent_jobs": 8,
        },
    }


class TestTargetConfig:
    def test_save_then_load(self, targets_dir, new_format_target):
        save_target("perlmutter", new_format_target)
        loaded = load_target("perlmutter")
        assert loaded is not None
        assert loaded["backend"] == "slurm"
        assert loaded["defaults"]["qos"] == "gpu_debug"

    def test_load_nonexistent(self, targets_dir):
        assert load_target("nonexistent") is None

    def test_list_empty(self, targets_dir):
        assert list_targets() == []

    def test_list_with_targets(self, targets_dir, new_format_target):
        save_target("perlmutter", new_format_target)
        save_target("frontier", {"site": "frontier", "backend": "slurm",
                                  "defaults": {"qos": "batch"}})
        assert list_targets() == ["frontier", "perlmutter"]


class TestUserConfig:
    def test_load_missing_returns_empty(self, targets_dir, monkeypatch):
        config_path = targets_dir.parent / "config.yaml"
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)
        assert load_user_config() == {}

    def test_save_and_load_default_target(self, targets_dir, monkeypatch):
        config_path = targets_dir.parent / "config.yaml"
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)
        save_user_config({"default_target": "perlmutter"})
        config = load_user_config()
        assert config["default_target"] == "perlmutter"

    def test_config_path_is_in_prism_dir(self):
        path = get_config_path()
        assert path.name == "config.yaml"
        assert ".prism" in str(path)


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


class TestMigrateTarget:
    def test_old_format_creates_defaults(self, sample_target):
        new = migrate_target(sample_target)
        assert new["defaults"]["qos"] == "debug"
        assert new["defaults"]["constraint"] == "gpu"
        assert "qos" not in new or isinstance(new.get("qos"), list)

    def test_old_format_creates_resource_limits(self, sample_target):
        new = migrate_target(sample_target)
        assert new["resource_limits"]["max_nodes"] == 4
        assert new["resource_limits"]["max_walltime_minutes"] == 360
        assert new["resource_limits"]["max_concurrent_jobs"] == 8
        # Old flat keys should be gone
        assert "max_nodes" not in new
        assert "max_walltime_minutes" not in new

    def test_drops_container_flags(self):
        old = {"backend": "slurm", "qos": "debug",
               "container_flags": ["--gpu"]}
        new = migrate_target(old)
        assert "container_flags" not in new

    def test_drops_node_type(self):
        old = {"backend": "slurm", "qos": "debug", "node_type": "gpu"}
        new = migrate_target(old)
        assert "node_type" not in new

    def test_creates_qos_list(self, sample_target):
        new = migrate_target(sample_target)
        qos_list = new.get("qos", [])
        assert isinstance(qos_list, list)
        assert len(qos_list) == 1
        assert qos_list[0]["name"] == "debug"
        assert qos_list[0]["constraint"] == "gpu"

    def test_preserves_non_migrated_fields(self, sample_target):
        new = migrate_target(sample_target)
        assert new["site"] == "perlmutter"
        assert new["backend"] == "slurm"
        assert new["connection"]["hostname"] == "perlmutter.nersc.gov"
        assert new["account"] == "m1234"

    def test_auto_migrates_on_load(self, targets_dir, sample_target):
        """Loading old-format target should auto-migrate and rewrite."""
        # Write old format directly (bypass load_target)
        path = targets_dir / "old.yaml"
        with open(path, "w") as f:
            yaml.dump(sample_target, f)

        loaded = load_target("old")
        assert loaded is not None
        assert "defaults" in loaded
        assert loaded["defaults"]["qos"] == "debug"

        # File should have been rewritten
        with open(path) as f:
            rewritten = yaml.safe_load(f)
        assert "defaults" in rewritten

    def test_new_format_not_migrated(self, new_format_target):
        """New-format targets should not be detected as old."""
        from prism.dagster.targets import _is_old_format
        assert not _is_old_format(new_format_target)


# ---------------------------------------------------------------------------
# resolve_run_config tests
# ---------------------------------------------------------------------------


class TestResolveRunConfig:
    def test_cli_overrides_win(self, new_format_target):
        resolved = resolve_run_config(new_format_target,
                                       {"qos": "gpu_regular"})
        assert resolved["qos"] == "gpu_regular"

    def test_defaults_used(self, new_format_target):
        resolved = resolve_run_config(new_format_target, {})
        assert resolved["qos"] == "gpu_debug"
        assert resolved["constraint"] == "gpu"

    def test_invalid_qos_raises(self, new_format_target):
        with pytest.raises(ValueError, match="not_a_real_qos"):
            resolve_run_config(new_format_target,
                                {"qos": "not_a_real_qos"})

    def test_local_backend_returns_empty(self):
        config = {"backend": "local"}
        resolved = resolve_run_config(config, {})
        assert resolved == {}

    def test_local_backend_warns_on_qos(self):
        config = {"backend": "local"}
        with patch("prism.dagster.targets.logger") as mock_logger:
            resolve_run_config(config, {"qos": "debug"})
            mock_logger.warning.assert_called()

    def test_auto_constraint_from_qos_entry(self, new_format_target):
        """Switching QoS should auto-set constraint from qos entry."""
        resolved = resolve_run_config(new_format_target,
                                       {"qos": "debug"})
        assert resolved["constraint"] == "cpu"

    def test_explicit_constraint_not_overridden(self, new_format_target):
        """Explicit --constraint should not be overridden by qos entry."""
        resolved = resolve_run_config(new_format_target,
                                       {"qos": "debug",
                                        "constraint": "gpu"})
        assert resolved["constraint"] == "gpu"

    def test_time_limit_override(self, new_format_target):
        resolved = resolve_run_config(new_format_target,
                                       {"time_limit": "2h"})
        assert resolved["time_limit"] == "2h"


# ---------------------------------------------------------------------------
# QoS query helpers
# ---------------------------------------------------------------------------


class TestQosHelpers:
    def test_get_allowed_qos_names(self, new_format_target):
        names = get_allowed_qos_names(new_format_target)
        assert "gpu_debug" in names
        assert "gpu_regular" in names
        assert "debug" in names

    def test_get_qos_entry(self, new_format_target):
        entry = get_qos_entry(new_format_target, "gpu_debug")
        assert entry is not None
        assert entry["constraint"] == "gpu"
        assert entry["use_for"] == "quick iteration, testing"

    def test_get_qos_entry_missing(self, new_format_target):
        assert get_qos_entry(new_format_target, "nonexistent") is None

    def test_filter_gpu(self, new_format_target):
        gpu = filter_qos_by_hardware(new_format_target, needs_gpu=True)
        names = [e["name"] for e in gpu]
        assert "gpu_debug" in names
        assert "gpu_regular" in names
        assert "debug" not in names
        assert "regular_1" not in names

    def test_filter_cpu(self, new_format_target):
        cpu = filter_qos_by_hardware(new_format_target, needs_gpu=False)
        names = [e["name"] for e in cpu]
        assert "debug" in names
        assert "regular_1" in names
        assert "gpu_debug" not in names

    def test_empty_qos_list(self):
        config = {"backend": "local"}
        assert get_allowed_qos_names(config) == []
        assert filter_qos_by_hardware(config, True) == []


# ---------------------------------------------------------------------------
# Cluster cache tests
# ---------------------------------------------------------------------------


class TestClusterCache:
    def test_save_and_load_roundtrip(self, cache_dir):
        from prism.dagster.slurm_info import ClusterInfo, QoSInfo

        info = ClusterInfo(
            qos={"gpu_debug": QoSInfo("gpu_debug", max_wall_minutes=30,
                                       max_nodes=8, priority=69119)},
            user_qos=["gpu_debug"],
            user_accounts=["m4031"],
            partitions={},
            timestamp="2026-03-28T00:00:00",
        )
        save_cluster_cache("test_target", info)
        loaded = load_cluster_cache("test_target")
        assert loaded is not None
        assert loaded.qos["gpu_debug"].max_nodes == 8
        assert loaded.timestamp == "2026-03-28T00:00:00"

    def test_load_missing(self, cache_dir):
        assert load_cluster_cache("nonexistent") is None

    def test_is_stale_no_file(self, cache_dir):
        assert is_cache_stale("nonexistent") is True

    def test_is_stale_fresh(self, cache_dir):
        from prism.dagster.slurm_info import ClusterInfo

        info = ClusterInfo(timestamp="2099-01-01T00:00:00+00:00")
        save_cluster_cache("fresh", info)
        assert is_cache_stale("fresh") is False

    def test_is_stale_old(self, cache_dir):
        from prism.dagster.slurm_info import ClusterInfo

        info = ClusterInfo(timestamp="2020-01-01T00:00:00+00:00")
        save_cluster_cache("old", info)
        assert is_cache_stale("old") is True


# ---------------------------------------------------------------------------
# QoS entry management
# ---------------------------------------------------------------------------


class TestQoSEntryManagement:
    def test_add_qos_entry(self, new_format_target):
        from prism.dagster.targets import add_qos_entry

        added = add_qos_entry(new_format_target, "gpu_shared", "gpu", "small jobs")
        assert added is True
        names = get_allowed_qos_names(new_format_target)
        assert "gpu_shared" in names

    def test_add_qos_entry_duplicate(self, new_format_target):
        from prism.dagster.targets import add_qos_entry

        added = add_qos_entry(new_format_target, "gpu_debug", "gpu", "testing")
        assert added is False

    def test_remove_qos_entry(self, new_format_target):
        from prism.dagster.targets import remove_qos_entry

        removed = remove_qos_entry(new_format_target, "gpu_regular")
        assert removed is True
        names = get_allowed_qos_names(new_format_target)
        assert "gpu_regular" not in names
        assert "gpu_debug" in names

    def test_remove_qos_entry_missing(self, new_format_target):
        from prism.dagster.targets import remove_qos_entry

        removed = remove_qos_entry(new_format_target, "nonexistent")
        assert removed is False

    def test_add_to_empty_qos_list(self):
        from prism.dagster.targets import add_qos_entry

        config: dict = {"backend": "slurm", "defaults": {}}
        added = add_qos_entry(config, "gpu_debug", "gpu", "testing")
        assert added is True
        assert len(config["qos"]) == 1
