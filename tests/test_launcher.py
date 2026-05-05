"""Tests for the lc launch backend."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from lightcone.engine.container import ContainerBuildError, RuntimeChoice
from lightcone.engine.launcher import (
    BUILTIN_TARGETS,
    LaunchTarget,
    _build_dev_wheel,
    _is_dev_version,
    _render_containerfile,
    _try_pull_and_cache,
    resolve_launch_target,
)
from lightcone.engine.manifest import lc_version as _lc_version


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "astra.yaml").write_text("name: test\n")
    (tmp_path / ".lightcone").mkdir()
    return tmp_path


@pytest.fixture
def fake_target(tmp_path: Path) -> LaunchTarget:
    cf = tmp_path / "fake.Containerfile"
    cf.write_text(
        "FROM python:3.12-slim\n"
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
        assert t.entrypoint == ["--dangerously-skip-permissions"]
        assert "ANTHROPIC_API_KEY" in t.env_passthrough
        assert "CLAUDE_CODE_OAUTH_TOKEN" in t.env_passthrough
        assert "/dev/fuse" in t.devices
        assert ".claude.json" in t.home_mounts
        assert ".claude" in t.home_mounts
        assert t.run_as_host_user is True


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
            "FROM python:3.12-slim\nARG LIGHTCONE_VERSION=0.0.0\nRUN echo ${LIGHTCONE_VERSION}\n"
        )
        target = LaunchTarget(name="with_default", containerfile=cf, entrypoint=["bash"])
        rendered = _render_containerfile(target, project)
        content = rendered.read_text()
        version = _lc_version()
        assert f"ARG LIGHTCONE_VERSION={version}" in content
        assert "ARG LIGHTCONE_VERSION=0.0.0" not in content


_BSP = "--break-system-packages"
_INSTALL_BLOCK = (
    "# Dev/local builds are not published to PyPI ...\n"
    "RUN case \"${LIGHTCONE_VERSION}\" in \\\n"
    f"    *dev*|*+*|dev) uv pip install --system {_BSP} lightcone-cli ;; \\\n"
    f"    *) uv pip install --system {_BSP} \"lightcone-cli==${{LIGHTCONE_VERSION}}\" ;; \\\n"
    "    esac\n"
)


@pytest.fixture
def install_target(tmp_path: Path) -> LaunchTarget:
    """Target whose Containerfile includes the lightcone install block."""
    cf = tmp_path / "install.Containerfile"
    cf.write_text(
        "FROM python:3.12-slim\n"
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
        assert f"uv pip install --system --break-system-packages /tmp/{wheel.name}" in content
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


class TestBuildDevWheelReuse:
    """_build_dev_wheel reuses an existing wheel for the same version."""

    def test_reuses_existing_wheel_same_version(self, tmp_path: Path) -> None:
        version = "0.1.0.dev0+gabc123"
        wheel = tmp_path / f"lightcone_cli-{version}-py3-none-any.whl"
        wheel.write_bytes(b"original wheel bytes")

        with patch("lightcone.engine.launcher._find_source_root", return_value=tmp_path):
            with patch("lightcone.engine.launcher._lc_version", return_value=version):
                with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
                    result = _build_dev_wheel(tmp_path)

        # subprocess.run must NOT be called — wheel was reused
        mock_run.assert_not_called()
        assert result == wheel

    def test_rebuilds_when_version_changed(self, tmp_path: Path) -> None:
        old_wheel = tmp_path / "lightcone_cli-0.0.1.dev0+gold-py3-none-any.whl"
        old_wheel.write_bytes(b"stale")
        new_version = "0.1.0.dev0+gnew"
        new_wheel = tmp_path / f"lightcone_cli-{new_version}-py3-none-any.whl"

        def fake_build(*args: object, **kwargs: object) -> MagicMock:
            new_wheel.write_bytes(b"fresh wheel")
            return MagicMock(returncode=0)

        with patch("lightcone.engine.launcher._find_source_root", return_value=tmp_path):
            with patch("lightcone.engine.launcher._lc_version", return_value=new_version):
                with patch("lightcone.engine.launcher.subprocess.run", side_effect=fake_build):
                    result = _build_dev_wheel(tmp_path)

        assert result == new_wheel
        # Stale wheel should have been removed
        assert not old_wheel.exists()


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
    def test_podman_hpc_no_extra_flags(
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
        """podman-hpc takes the same plain ``run`` options as podman.

        Earlier versions of the launcher added ``--no-setns`` here, but
        modern podman-hpc (5.x+) rejects that flag. We rely on the
        wrapper's defaults instead.
        """
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        choice = RuntimeChoice(runtime="podman-hpc", explicit=True)
        launch_target(fake_target.name, choice=choice, project_root=project)

        cmd = mock_exec.call_args[0][1]
        assert "--no-setns" not in cmd
        assert cmd[0] == "podman-hpc"
        assert "run" in cmd

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_run_as_host_user_adds_user_flag(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        project: Path,
        tmp_path: Path,
    ) -> None:
        import os

        from lightcone.engine.launcher import launch_target

        target = LaunchTarget(
            name="fake",
            containerfile=tmp_path / "fake.Containerfile",
            entrypoint=["--dangerously-skip-permissions"],
            run_as_host_user=True,
        )
        (tmp_path / "fake.Containerfile").write_text(
            "FROM python:3.12-slim\nARG LIGHTCONE_VERSION\n"
        )
        mock_resolve.return_value = target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        choice = RuntimeChoice(runtime="docker", explicit=True)
        launch_target(target.name, choice=choice, project_root=project)

        cmd = mock_exec.call_args[0][1]
        assert "--user" in cmd
        idx = cmd.index("--user")
        assert cmd[idx + 1] == f"{os.getuid()}:{os.getgid()}"

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
        # Only the variable name is passed (no embedded value) to avoid
        # secrets appearing in /proc/<pid>/cmdline.
        assert cmd[idx + 1] == "HOME"

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

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_env_passthrough_name_only(
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
        """env vars are passed as -e VAR (no value) to avoid cmdline leaks."""
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball
        monkeypatch.setenv("HOME", "/home/secureuser")

        choice = RuntimeChoice(runtime="docker", explicit=True)
        launch_target(fake_target.name, choice=choice, project_root=project)

        cmd = mock_exec.call_args[0][1]
        # Must not embed the value in argv.
        assert "HOME=/home/secureuser" not in " ".join(cmd)
        assert "-e" in cmd
        idx = cmd.index("-e")
        assert cmd[idx + 1] == "HOME"

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_home_mounts_added_when_dir_exists(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        project: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from lightcone.engine.launcher import launch_target

        # Target with a home_mount subdir
        home_dir = tmp_path / "home"
        claude_dir = home_dir / ".claude"
        claude_dir.mkdir(parents=True)
        cf = tmp_path / "fake.Containerfile"
        cf.write_text("FROM python:3.12-slim\nARG LIGHTCONE_VERSION\n")
        target = LaunchTarget(
            name="fake",
            containerfile=cf,
            entrypoint=["bash"],
            home_mounts=[".claude"],
        )
        mock_resolve.return_value = target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball
        monkeypatch.setenv("HOME", str(home_dir))

        choice = RuntimeChoice(runtime="docker", explicit=True)
        launch_target(target.name, choice=choice, project_root=project)

        cmd = mock_exec.call_args[0][1]
        expected = str(claude_dir)
        assert f"{expected}:{expected}" in " ".join(cmd)


class TestTryPullAndCache:
    """Tests for the GHCR pull-first behaviour in _try_pull_and_cache."""

    def test_daemonless_returns_false_immediately(self, tmp_path: Path) -> None:
        tarball = tmp_path / "lc-fake-abc123.tar"
        with patch("lightcone.engine.launcher.pull_image") as mock_pull:
            result = _try_pull_and_cache(
                "lc-fake-abc123",
                "ghcr.io/lightconeresearch/claude:1.0.0",
                tarball,
                runtime="apptainer",
            )
        assert result is False
        mock_pull.assert_not_called()

    def test_daemonless_singularity_returns_false(self, tmp_path: Path) -> None:
        tarball = tmp_path / "lc-fake-abc123.tar"
        with patch("lightcone.engine.launcher.pull_image") as mock_pull:
            result = _try_pull_and_cache(
                "lc-fake-abc123",
                "ghcr.io/lightconeresearch/claude:1.0.0",
                tarball,
                runtime="singularity",
            )
        assert result is False
        mock_pull.assert_not_called()

    @patch("lightcone.engine.launcher.save_image_as_tarball")
    @patch("lightcone.engine.launcher.subprocess.run")
    @patch("lightcone.engine.launcher.pull_image")
    def test_successful_pull_saves_tarball(
        self,
        mock_pull: MagicMock,
        mock_subprocess: MagicMock,
        mock_save: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_subprocess.return_value = MagicMock(returncode=0)
        tarball = tmp_path / "lc-fake-abc123.tar"
        registry_ref = "ghcr.io/lightconeresearch/claude:1.0.0"

        result = _try_pull_and_cache(
            "lc-fake-abc123",
            registry_ref,
            tarball,
            runtime="docker",
        )

        assert result is True
        mock_pull.assert_called_once_with(registry_ref, runtime="docker")
        mock_subprocess.assert_called_once_with(
            ["docker", "tag", registry_ref, "lc-fake-abc123"],
            check=True,
            capture_output=True,
        )
        mock_save.assert_called_once_with("lc-fake-abc123", tarball, runtime="docker")

    @patch("lightcone.engine.launcher.pull_image")
    def test_pull_failure_returns_false(
        self,
        mock_pull: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_pull.side_effect = ContainerBuildError("registry unreachable")
        tarball = tmp_path / "lc-fake-abc123.tar"

        result = _try_pull_and_cache(
            "lc-fake-abc123",
            "ghcr.io/lightconeresearch/claude:1.0.0",
            tarball,
            runtime="docker",
        )

        assert result is False

    @patch("lightcone.engine.launcher.subprocess.run")
    @patch("lightcone.engine.launcher.pull_image")
    def test_tag_failure_returns_false(
        self,
        mock_pull: MagicMock,
        mock_subprocess: MagicMock,
        tmp_path: Path,
    ) -> None:
        import subprocess

        mock_subprocess.side_effect = subprocess.CalledProcessError(1, "docker tag")
        tarball = tmp_path / "lc-fake-abc123.tar"

        result = _try_pull_and_cache(
            "lc-fake-abc123",
            "ghcr.io/lightconeresearch/claude:1.0.0",
            tarball,
            runtime="docker",
        )

        assert result is False


class TestLaunchTargetGhcrPull:
    """Tests for the GHCR pull-first path in launch_target."""

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.save_image_as_tarball")
    @patch("lightcone.engine.launcher.build_image")
    @patch("lightcone.engine.launcher._try_pull_and_cache", return_value=True)
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_release_version_tries_pull_before_build(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_pull: MagicMock,
        mock_build: MagicMock,
        mock_save: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
    ) -> None:
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        # No tarball on disk — should attempt pull, succeed, skip build.
        with patch("lightcone.engine.launcher._lc_version", return_value="1.2.3"):
            choice = RuntimeChoice(runtime="docker", explicit=True)
            launch_target(fake_target.name, choice=choice, project_root=project)

        mock_pull.assert_called_once()
        mock_build.assert_not_called()

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.save_image_as_tarball")
    @patch("lightcone.engine.launcher.build_image")
    @patch("lightcone.engine.launcher._try_pull_and_cache", return_value=False)
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_pull_failure_falls_back_to_local_build(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_pull: MagicMock,
        mock_build: MagicMock,
        mock_save: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
    ) -> None:
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        with patch("lightcone.engine.launcher._lc_version", return_value="1.2.3"):
            choice = RuntimeChoice(runtime="docker", explicit=True)
            launch_target(fake_target.name, choice=choice, project_root=project)

        mock_pull.assert_called_once()
        mock_build.assert_called_once()

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.save_image_as_tarball")
    @patch("lightcone.engine.launcher.build_image")
    @patch("lightcone.engine.launcher._try_pull_and_cache")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_dev_version_skips_pull(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_pull: MagicMock,
        mock_build: MagicMock,
        mock_save: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
    ) -> None:
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        with patch("lightcone.engine.launcher._lc_version", return_value="0.3.5.dev0+gabc"):
            choice = RuntimeChoice(runtime="docker", explicit=True)
            launch_target(fake_target.name, choice=choice, project_root=project)

        mock_pull.assert_not_called()
        mock_build.assert_called_once()
