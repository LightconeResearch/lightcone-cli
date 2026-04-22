"""Tests for target and user configuration."""
from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine.targets import (
    TargetKind,
    detect_target_shape,
    get_config_path,
    list_targets,
    load_target,
    load_user_config,
    normalize_target,
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


class TestTargetKind:
    def test_values(self):
        assert TargetKind.DOCKER.value == "docker"
        assert TargetKind.LOCAL.value == "local"
        assert TargetKind.SLURM.value == "slurm"
        assert TargetKind.SLURM_SESSION.value == "slurm-session"

    def test_str_compatible(self):
        # TargetKind is a str subclass so it round-trips through YAML naturally.
        assert TargetKind.SLURM == "slurm"


class TestDetectTargetShape:
    def test_new_shape(self):
        assert detect_target_shape({"mode": "slurm"}) == "new"

    def test_legacy_shape(self):
        assert detect_target_shape({"backend": "slurm"}) == "legacy"

    def test_new_takes_precedence(self):
        # If both are present (unlikely mixed config), the new shape wins.
        assert detect_target_shape({"mode": "slurm", "backend": "slurm"}) == "new"

    def test_missing_raises(self):
        with pytest.raises(ValueError, match="neither 'mode' nor 'backend'"):
            detect_target_shape({"site": "perlmutter"})


class TestNormalizeTarget:
    def test_new_shape_is_identity(self):
        cfg = {
            "name": "perlmutter",
            "mode": "slurm",
            "ssh": {"host": "perlmutter.nersc.gov"},
            "queue": {"account": "m1234"},
        }
        assert normalize_target(cfg) == cfg

    def test_legacy_emits_deprecation_warning(self, sample_target):
        with pytest.warns(DeprecationWarning, match="pre-ADR-0001"):
            normalize_target(sample_target)

    def test_legacy_flat_scheduler_translation(self, sample_target):
        # sample_target has scheduler-ish fields at the top level (wizard shape).
        with pytest.warns(DeprecationWarning):
            result = normalize_target(sample_target)

        assert result["mode"] == "slurm"
        assert result["name"] == "perlmutter"
        assert result["site"] == "perlmutter"
        assert result["ssh"] == {"host": "perlmutter.nersc.gov", "user": "testuser"} or (
            result["ssh"]["host"] == "perlmutter.nersc.gov"
        )
        assert result["queue"]["account"] == "m1234"
        assert result["queue"]["qos"] == "debug"
        assert result["container"]["runtime"] == "podman-hpc"
        assert "--constraint=gpu" in result["extra_sbatch_directives"]

    def test_legacy_nested_scheduler_translation(self):
        cfg = {
            "site": "frontier",
            "backend": "slurm",
            "connection": {"hostname": "frontier.olcf.ornl.gov"},
            "scheduler": {
                "account": "CSC999",
                "partition": "batch",
                "qos": "normal",
                "constraint": "gpu",
                "container_runtime": "apptainer",
                "container_flags": ["--nv"],
                "extra_slurm_args": ["--reservation=maint"],
            },
            "poll": {"interval_seconds": 15, "timeout_seconds": 7200},
        }
        with pytest.warns(DeprecationWarning):
            result = normalize_target(cfg)

        assert result["mode"] == "slurm"
        assert result["ssh"]["host"] == "frontier.olcf.ornl.gov"
        assert result["queue"]["account"] == "CSC999"
        assert result["queue"]["partition"] == "batch"
        assert result["container"]["runtime"] == "apptainer"
        assert result["container"]["flags"] == ["--nv"]
        assert result["extra_sbatch_directives"] == [
            "--constraint=gpu",
            "--reservation=maint",
        ]
        # interval_seconds is dropped; only timeout_seconds carries over.
        assert result["poll"] == {"timeout_seconds": 7200}

    def test_legacy_docker_minimal(self):
        cfg = {"backend": "docker"}
        with pytest.warns(DeprecationWarning):
            result = normalize_target(cfg)
        assert result["mode"] == "docker"
        assert "queue" not in result
        assert "ssh" not in result
        assert "extra_sbatch_directives" not in result
