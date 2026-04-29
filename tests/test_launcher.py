"""Tests for the lc launch backend."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lightcone.engine.container import ContainerBuildError, RuntimeChoice
from lightcone.engine.launcher import (
    BUILTIN_TARGETS,
    LaunchTarget,
    _is_dev_version,
    _lc_version,
    _render_containerfile,
    resolve_launch_target,
)


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

    def test_substitutes_existing_default(
        self, tmp_path: Path, project: Path
    ) -> None:
        cf = tmp_path / "with_default.Containerfile"
        cf.write_text(
            "FROM ubuntu:24.04\nARG LIGHTCONE_VERSION=0.0.0\nRUN echo ${LIGHTCONE_VERSION}\n"
        )
        target = LaunchTarget(name="with_default", containerfile=cf, entrypoint=["bash"])
        rendered = _render_containerfile(target, project)
        content = rendered.read_text()
        version = _lc_version()
        assert f"ARG LIGHTCONE_VERSION={version}" in content
        assert "ARG LIGHTCONE_VERSION=0.0.0" not in content


_INSTALL_BLOCK = (
    "# Dev/local builds are not published to PyPI ...\n"
    "RUN case \"${LIGHTCONE_VERSION}\" in \\\n"
    "    *dev*|*+*|dev) uv pip install --system lightcone-cli ;; \\\n"
    "    *) uv pip install --system \"lightcone-cli==${LIGHTCONE_VERSION}\" ;; \\\n"
    "    esac\n"
)


@pytest.fixture
def install_target(tmp_path: Path) -> LaunchTarget:
    """Target whose Containerfile includes the lightcone install block."""
    cf = tmp_path / "install.Containerfile"
    cf.write_text(
        "FROM ubuntu:24.04\n"
        "ARG LIGHTCONE_VERSION\n"
        + _INSTALL_BLOCK
    )
    return LaunchTarget(name="install", containerfile=cf, entrypoint=["bash"])


class TestIsDevVersion:
    def test_clean_release_is_not_dev(self) -> None:
        assert _is_dev_version("1.2.3") is False

    def test_dev_string_is_dev(self) -> None:
        assert _is_dev_version("dev") is True

    def test_dev_suffix_is_dev(self) -> None:
        assert _is_dev_version("0.1.0.dev0+gabc123") is True

    def test_local_identifier_is_dev(self) -> None:
        assert _is_dev_version("1.0.0+local") is True

    def test_rc_version_is_not_dev(self) -> None:
        assert _is_dev_version("1.0.0rc1") is False


class TestRenderContainerfileDevWheel:
    def test_injects_copy_and_wheel_install_when_dev(
        self, install_target: LaunchTarget, project: Path, tmp_path: Path
    ) -> None:
        wheel = tmp_path / "lightcone_cli-0.1.0.dev0-py3-none-any.whl"
        wheel.write_bytes(b"fake wheel")

        with patch("lightcone.engine.launcher._lc_version", return_value="0.1.0.dev0+gabc"):
            with patch("lightcone.engine.launcher._build_dev_wheel", return_value=wheel):
                rendered = _render_containerfile(install_target, project)

        content = rendered.read_text()
        assert f"COPY {wheel.name} /tmp/{wheel.name}" in content
        assert f"uv pip install --system /tmp/{wheel.name}" in content
        assert "--no-deps" not in content
        assert "case" not in content

    def test_fallback_to_case_when_wheel_build_fails(
        self, install_target: LaunchTarget, project: Path
    ) -> None:
        with patch("lightcone.engine.launcher._lc_version", return_value="0.1.0.dev0+gabc"):
            with patch("lightcone.engine.launcher._build_dev_wheel", return_value=None):
                rendered = _render_containerfile(install_target, project)

        content = rendered.read_text()
        assert "case" in content
        assert "COPY" not in content

    def test_no_wheel_logic_for_release_version(
        self, install_target: LaunchTarget, project: Path
    ) -> None:
        with patch("lightcone.engine.launcher._lc_version", return_value="1.2.3"):
            with patch("lightcone.engine.launcher._build_dev_wheel") as mock_build:
                rendered = _render_containerfile(install_target, project)

        mock_build.assert_not_called()
        content = rendered.read_text()
        assert "case" in content

    def test_no_wheel_logic_without_install_block(
        self, fake_target: LaunchTarget, project: Path
    ) -> None:
        """Containerfiles without the install block are not touched."""
        with patch("lightcone.engine.launcher._lc_version", return_value="0.1.0.dev0+gabc"):
            with patch("lightcone.engine.launcher._build_dev_wheel") as mock_build:
                _render_containerfile(fake_target, project)

        mock_build.assert_not_called()


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
