"""Tests for target and user configuration."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lightcone.engine.targets import (
    OPTION_AXES,
    get_cache_key_overrides,
    get_config_path,
    get_option_choices,
    get_option_default,
    get_options,
    is_cache_stale,
    list_targets,
    load_cluster_cache,
    load_target,
    load_user_config,
    resolve_cache_key,
    resolve_run_config,
    save_cluster_cache,
    save_target,
    save_user_config,
)


@pytest.fixture
def targets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    targets = tmp_path / "targets"
    targets.mkdir()
    monkeypatch.setattr("lightcone.engine.targets.get_targets_dir", lambda: targets)
    return targets


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr("lightcone.engine.targets.get_cache_dir", lambda: cache)
    return cache


@pytest.fixture
def perlmutter_target() -> dict:
    return {
        "site": "perlmutter",
        "backend": "slurm",
        "connection": {
            "hostname": "perlmutter.nersc.gov",
            "username": "testuser",
        },
        "container_runtime": "podman-hpc",
        "options": {
            "qos": {
                "default": "debug",
                "choices": {
                    "debug":   "quick iteration, testing",
                    "regular": "production runs, large jobs",
                    "preempt": "cheap batch, restartable",
                    "shared":  "fractional node (1–2 GPUs)",
                },
            },
            "constraint": {
                "default": "gpu",
                "choices": {
                    "gpu": "A100 40 GB",
                    "cpu": "CPU only",
                },
            },
            "time_limit": {"default": "30m"},
            "account": {"default": "m1234"},
        },
        "strategy": "fit",
        "cache_key_overrides": {"regular/cpu": "regular_1"},
        "resource_limits": {
            "max_nodes": 4,
            "max_walltime_minutes": 360,
            "max_concurrent_jobs": 8,
        },
    }


class TestTargetCRUD:
    def test_save_then_load(self, targets_dir, perlmutter_target):
        save_target("perlmutter", perlmutter_target)
        loaded = load_target("perlmutter")
        assert loaded is not None
        assert loaded["backend"] == "slurm"
        assert get_option_default(loaded, "qos") == "debug"

    def test_load_nonexistent(self, targets_dir):
        assert load_target("nonexistent") is None

    def test_list_empty(self, targets_dir):
        assert list_targets() == []

    def test_list_with_targets(self, targets_dir, perlmutter_target):
        save_target("perlmutter", perlmutter_target)
        save_target("frontier", {
            "site": "frontier", "backend": "slurm",
            "options": {"qos": {"default": "batch",
                                 "choices": {"batch": ""}}},
        })
        assert list_targets() == ["frontier", "perlmutter"]


class TestUserConfig:
    def test_load_missing_returns_empty(self, targets_dir, monkeypatch):
        config_path = targets_dir.parent / "config.yaml"
        monkeypatch.setattr(
            "lightcone.engine.targets.get_config_path", lambda: config_path,
        )
        assert load_user_config() == {}

    def test_save_and_load_default_target(self, targets_dir, monkeypatch):
        config_path = targets_dir.parent / "config.yaml"
        monkeypatch.setattr(
            "lightcone.engine.targets.get_config_path", lambda: config_path,
        )
        save_user_config({"default_target": "perlmutter"})
        assert load_user_config()["default_target"] == "perlmutter"

    def test_config_path_is_in_lightcone_dir(self):
        path = get_config_path()
        assert path.name == "config.yaml"
        assert ".lightcone" in str(path)


class TestOptionHelpers:
    def test_axes(self):
        assert "qos" in OPTION_AXES
        assert "constraint" in OPTION_AXES
        assert "time_limit" in OPTION_AXES

    def test_get_option_default(self, perlmutter_target):
        assert get_option_default(perlmutter_target, "qos") == "debug"
        assert get_option_default(perlmutter_target, "constraint") == "gpu"
        assert get_option_default(perlmutter_target, "nonexistent") is None

    def test_get_option_choices(self, perlmutter_target):
        choices = get_option_choices(perlmutter_target, "qos")
        assert "debug" in choices
        assert "regular" in choices
        assert choices["regular"].startswith("production")

    def test_get_option_choices_list_form(self):
        config = {"options": {"partition": {"choices": ["a", "b"]}}}
        assert get_option_choices(config, "partition") == {"a": "", "b": ""}

    def test_get_options(self, perlmutter_target):
        assert "qos" in get_options(perlmutter_target)
        assert get_options({}) == {}


class TestResolveCacheKey:
    def test_override_with_constraint(self):
        overrides = {"regular/cpu": "regular_1"}
        assert resolve_cache_key("regular", "cpu", {}, overrides) == "regular_1"

    def test_override_without_constraint(self):
        overrides = {"custom": "custom_42"}
        assert resolve_cache_key("custom", None, {}, overrides) == "custom_42"

    def test_constraint_prefix_convention(self):
        cache_keys = {"gpu_debug", "gpu_regular"}
        assert resolve_cache_key("debug", "gpu", cache_keys) == "gpu_debug"

    def test_bare_fallback(self):
        cache_keys = {"debug", "regular"}
        # No prefixed variant exists — fall back to bare name.
        assert resolve_cache_key("debug", "cpu", cache_keys) == "debug"

    def test_no_constraint_uses_qos_verbatim(self):
        cache_keys = {"debug"}
        assert resolve_cache_key("debug", None, cache_keys) == "debug"

    def test_override_wins_over_convention(self):
        cache_keys = {"gpu_debug"}
        overrides = {"debug/gpu": "custom"}
        assert resolve_cache_key("debug", "gpu", cache_keys, overrides) == "custom"


class TestGetCacheKeyOverrides:
    def test_reads_dict(self, perlmutter_target):
        assert get_cache_key_overrides(perlmutter_target) == {
            "regular/cpu": "regular_1",
        }

    def test_missing_returns_empty(self):
        assert get_cache_key_overrides({}) == {}

    def test_non_dict_returns_empty(self):
        assert get_cache_key_overrides({"cache_key_overrides": "oops"}) == {}


class TestResolveRunConfig:
    def test_cli_overrides_win(self, perlmutter_target):
        resolved = resolve_run_config(perlmutter_target, {"qos": "regular"})
        assert resolved["qos"] == "regular"

    def test_defaults_used(self, perlmutter_target):
        resolved = resolve_run_config(perlmutter_target, {})
        assert resolved["qos"] == "debug"
        assert resolved["constraint"] == "gpu"
        assert resolved["time_limit"] == "30m"

    def test_invalid_qos_raises(self, perlmutter_target):
        with pytest.raises(ValueError, match="nonexistent"):
            resolve_run_config(perlmutter_target, {"qos": "nonexistent"})

    def test_invalid_constraint_raises(self, perlmutter_target):
        with pytest.raises(ValueError, match="gpu&hbm80g"):
            resolve_run_config(perlmutter_target, {"constraint": "gpu&hbm80g"})

    def test_local_backend_returns_empty(self):
        resolved = resolve_run_config({"backend": "local"}, {})
        assert resolved == {}

    def test_local_backend_warns_on_qos(self):
        config = {"backend": "local"}
        with patch("lightcone.engine.targets.logger") as mock_logger:
            resolve_run_config(config, {"qos": "debug"})
            mock_logger.warning.assert_called()

    def test_time_limit_override(self, perlmutter_target):
        resolved = resolve_run_config(perlmutter_target, {"time_limit": "2h"})
        assert resolved["time_limit"] == "2h"

    def test_orthogonal_qos_and_constraint(self, perlmutter_target):
        """qos and constraint can be set independently."""
        resolved = resolve_run_config(
            perlmutter_target, {"qos": "regular", "constraint": "cpu"},
        )
        assert resolved["qos"] == "regular"
        assert resolved["constraint"] == "cpu"


class TestClusterCache:
    def test_save_and_load_roundtrip(self, cache_dir):
        from lightcone.engine.slurm_info import ClusterInfo, QoSInfo

        info = ClusterInfo(
            qos={"gpu_debug": QoSInfo("gpu_debug", max_wall_minutes=30,
                                       max_nodes=8, priority=69119)},
            user_qos=["gpu_debug"],
            user_accounts=["m4031"],
            partitions={},
            timestamp="2026-03-28T00:00:00",
        )
        save_cluster_cache("t", info)
        loaded = load_cluster_cache("t")
        assert loaded is not None
        assert loaded.qos["gpu_debug"].max_nodes == 8

    def test_load_missing(self, cache_dir):
        assert load_cluster_cache("nonexistent") is None

    def test_is_stale_no_file(self, cache_dir):
        assert is_cache_stale("nonexistent") is True

    def test_is_stale_fresh(self, cache_dir):
        from lightcone.engine.slurm_info import ClusterInfo

        save_cluster_cache("fresh", ClusterInfo(timestamp="2099-01-01T00:00:00+00:00"))
        assert is_cache_stale("fresh") is False

    def test_is_stale_old(self, cache_dir):
        from lightcone.engine.slurm_info import ClusterInfo

        save_cluster_cache("old", ClusterInfo(timestamp="2020-01-01T00:00:00+00:00"))
        assert is_cache_stale("old") is True
