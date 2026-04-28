"""Tests for the lc launch backend."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lightcone.engine.launcher import (
    BUILTIN_TARGETS,
    LaunchTarget,
    _render_containerfile,
    _lc_version,
    resolve_launch_target,
)
from lightcone.engine.container import ContainerBuildError, RuntimeChoice


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "astra.yaml").write_text("name: test\n")
    (tmp_path / ".lightcone").mkdir()
    return tmp_path


@pytest.fixture
def fake_target(tmp_path: Path) -> LaunchTarget:
    cf = tmp_path / "fake.Containerfile"
    cf.write_text(
        "FROM ubuntu:24.04\n"
        "ARG LIGHTCONE_VERSION\n"
        "RUN echo ${LIGHTCONE_VERSION}\n"
    )
    return LaunchTarget(
        name="fake",
        containerfile=cf,
        entrypoint=["bash"],
        env_passthrough=["HOME"],
        devices=[],
    )


class TestBuiltinTargets:
    def test_claude_is_registered(self) -> None:
        assert "claude" in BUILTIN_TARGETS

    def test_claude_target_fields(self) -> None:
        t = BUILTIN_TARGETS["claude"]
        assert t.name == "claude"
        assert t.entrypoint == ["claude"]
        assert "ANTHROPIC_API_KEY" in t.env_passthrough
        assert "/dev/fuse" in t.devices


class TestResolveTarget:
    def test_resolves_claude(self, project: Path) -> None:
        t = resolve_launch_target("claude", project)
        assert t.name == "claude"

    def test_unknown_raises(self, project: Path) -> None:
        with pytest.raises(ContainerBuildError, match="Unknown launch target"):
            resolve_launch_target("nonexistent", project)


class TestRenderContainerfile:
    def test_substitutes_lightcone_version(
        self, fake_target: LaunchTarget, project: Path
    ) -> None:
        rendered = _render_containerfile(fake_target, project)
        content = rendered.read_text()
        version = _lc_version()
        assert f"ARG LIGHTCONE_VERSION={version}" in content
        assert "ARG LIGHTCONE_VERSION\n" not in content

    def test_renders_to_lightcone_containers(
        self, fake_target: LaunchTarget, project: Path
    ) -> None:
        rendered = _render_containerfile(fake_target, project)
        assert rendered.parent == project / ".lightcone" / "containers"
        assert rendered.name == "fake.Containerfile"

    def test_idempotent(self, fake_target: LaunchTarget, project: Path) -> None:
        r1 = _render_containerfile(fake_target, project)
        r2 = _render_containerfile(fake_target, project)
        assert r1.read_text() == r2.read_text()


class TestLaunchTarget:
    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_exec_called_with_runtime(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
        tmp_path: Path,
    ) -> None:
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        # Tarball already exists — skip build
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        choice = RuntimeChoice(runtime="docker", explicit=True)
        launch_target(fake_target.name, choice=choice, project_root=project)

        # os.execvp was called
        assert mock_exec.called
        exec_args = mock_exec.call_args[0]
        cmd = exec_args[1]
        assert cmd[0] == "docker"
        assert "run" in cmd
        assert "-it" in cmd

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_podman_hpc_adds_no_setns(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
        tmp_path: Path,
    ) -> None:
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        choice = RuntimeChoice(runtime="podman-hpc", explicit=True)
        launch_target(fake_target.name, choice=choice, project_root=project)

        cmd = mock_exec.call_args[0][1]
        assert "--no-setns" in cmd

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_env_passthrough(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball
        monkeypatch.setenv("HOME", "/home/testuser")

        choice = RuntimeChoice(runtime="docker", explicit=True)
        launch_target(fake_target.name, choice=choice, project_root=project)

        cmd = mock_exec.call_args[0][1]
        assert "-e" in cmd
        idx = cmd.index("-e")
        assert "HOME=/home/testuser" in cmd[idx + 1]

    @patch("lightcone.engine.launcher.build_image")
    @patch("lightcone.engine.launcher.save_image_as_tarball")
    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_builds_when_tarball_absent(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        mock_save: MagicMock,
        mock_build: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
    ) -> None:
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        # No tarball on disk — build should be called
        choice = RuntimeChoice(runtime="docker", explicit=True)
        launch_target(fake_target.name, choice=choice, project_root=project)

        mock_build.assert_called_once()
        mock_save.assert_called_once()
