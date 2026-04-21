"""Tests for target and user configuration."""
from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine.targets import (
    get_config_path,
    list_targets,
    load_target,
    load_user_config,
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


class TestTargetConfig:
    def test_save_then_load(self, targets_dir, sample_target):
        save_target("perlmutter-gpu", sample_target)
        loaded = load_target("perlmutter-gpu")
        assert loaded is not None
        assert loaded["backend"] == "slurm"
        assert loaded["connection"]["hostname"] == "perlmutter.nersc.gov"

    def test_load_nonexistent(self, targets_dir):
        assert load_target("nonexistent") is None

    def test_list_empty(self, targets_dir):
        assert list_targets() == []

    def test_list_with_targets(self, targets_dir, sample_target):
        save_target("perlmutter-gpu", sample_target)
        save_target("frontier-gpu", {"site": "frontier", "backend": "slurm"})
        assert list_targets() == ["frontier-gpu", "perlmutter-gpu"]


class TestUserConfig:
    def test_load_missing_returns_empty(self, targets_dir, monkeypatch):
        config_path = targets_dir.parent / "config.yaml"
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: config_path)
        assert load_user_config() == {}

    def test_save_and_load_default_target(self, targets_dir, monkeypatch):
        config_path = targets_dir.parent / "config.yaml"
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: config_path)
        save_user_config({"default_target": "perlmutter-gpu"})
        config = load_user_config()
        assert config["default_target"] == "perlmutter-gpu"

    def test_config_path_is_in_lightcone_dir(self):
        path = get_config_path()
        assert path.name == "config.yaml"
        assert ".lightcone" in str(path)
