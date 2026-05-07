"""Tests for the lc launch backend."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lightcone.engine.container import ContainerBuildError, RuntimeChoice
from lightcone.engine.launcher import (
    BUILTIN_TARGETS,
    LaunchTarget,
    _apply_tracking_tag,
    _build_dev_wheel,
    _is_dev_version,
    _render_containerfile,
    _tracking_image_ref,
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
        assert t.entrypoint == ["claude", "--dangerously-skip-permissions"]
        assert "ANTHROPIC_API_KEY" in t.env_passthrough
        assert "CLAUDE_CODE_OAUTH_TOKEN" in t.env_passthrough
        assert "/dev/fuse" in t.devices
        assert ".claude.json" in t.home_mounts
        assert t.run_as_host_user is True
        assert t.registry_name == "lightcone-sandbox"
        assert t.install_cmds == ["npm install -g @anthropic-ai/claude-code"]
        assert t.committed_tag_prefix == "lightcone-claude"

    def test_mistral_vibe_is_registered(self) -> None:
        assert "mistral-vibe" in BUILTIN_TARGETS

    def test_mistral_vibe_target_fields(self) -> None:
        t = BUILTIN_TARGETS["mistral-vibe"]
        assert t.name == "mistral-vibe"
        assert t.install_cmds == ["uv tool install mistral-vibe"]
        assert t.committed_tag_prefix == "lightcone-mistral-vibe"
        assert t.entrypoint == ["vibe"]
        assert t.env_passthrough == ["MISTRAL_API_KEY"]
        assert ".vibe/config.toml" in t.home_mounts
        assert ".vibe/agents/" in t.home_mounts
        assert ".vibe/prompts/" in t.home_mounts
        assert ".vibe/skills/" in t.home_mounts
        assert ".vibe/tools/" in t.home_mounts
        assert t.registry_name == "lightcone-sandbox"

    def test_opencode_is_registered(self) -> None:
        assert "opencode" in BUILTIN_TARGETS

    def test_opencode_target_fields(self) -> None:
        t = BUILTIN_TARGETS["opencode"]
        assert t.name == "opencode"
        assert t.install_cmds == ["npm install -g opencode-ai"]
        assert t.committed_tag_prefix == "lightcone-opencode"
        assert t.entrypoint == ["opencode"]
        assert "OPENAI_API_KEY" in t.env_passthrough
        assert "ANTHROPIC_API_KEY" in t.env_passthrough
        assert "MISTRAL_API_KEY" in t.env_passthrough
        assert "GEMINI_API_KEY" in t.env_passthrough
        assert "GROQ_API_KEY" in t.env_passthrough
        assert ".config/opencode/opencode.json" in t.home_mounts
        assert ".config/opencode/tui.json" in t.home_mounts
        assert ".config/opencode/agents/" in t.home_mounts
        assert ".config/opencode/commands/" in t.home_mounts
        assert ".config/opencode/modes/" in t.home_mounts
        assert ".config/opencode/plugins/" in t.home_mounts
        assert ".config/opencode/themes/" in t.home_mounts
        assert ".config/opencode/AGENTS.md" in t.home_mounts
        assert t.registry_name == "lightcone-sandbox"


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
        # --entrypoint must appear before the image tag
        if "--entrypoint" in cmd:
            assert cmd.index("--entrypoint") < cmd.index("lc-fake-abc123")

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
        """Docker (rootful) gets --user $UID:$GID directly."""
        import os

        from lightcone.engine.launcher import launch_target

        target = LaunchTarget(
            name="fake",
            containerfile=tmp_path / "fake.Containerfile",
            entrypoint=["claude", "--dangerously-skip-permissions"],
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
        assert "--userns=keep-id" not in cmd

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_run_as_host_user_uses_userns_keepid_for_podman(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        project: Path,
        tmp_path: Path,
    ) -> None:
        """Rootless podman: --user $UID:$GID fails with
        ``crun: setgroups: Invalid argument`` because the user namespace
        has no subuid/subgid mapping. --userns=keep-id is the rootless
        equivalent that maps the host UID into the container.
        """
        from lightcone.engine.launcher import launch_target

        target = LaunchTarget(
            name="fake",
            containerfile=tmp_path / "fake.Containerfile",
            entrypoint=["claude", "--dangerously-skip-permissions"],
            run_as_host_user=True,
        )
        (tmp_path / "fake.Containerfile").write_text(
            "FROM python:3.12-slim\nARG LIGHTCONE_VERSION\n"
        )
        mock_resolve.return_value = target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        choice = RuntimeChoice(runtime="podman", explicit=True)
        launch_target(target.name, choice=choice, project_root=project)
        cmd = mock_exec.call_args[0][1]
        assert "--userns=keep-id" in cmd
        assert "--user" not in cmd
        # Plain podman has no real-TTY/keep-id bug, so the dangerously-
        # skip-permissions arg stays.
        assert "--dangerously-skip-permissions" in cmd

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_podman_hpc_neither_userns_nor_skip_permissions(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        project: Path,
        tmp_path: Path,
    ) -> None:
        """podman-hpc rejects --userns=keep-id with a real TTY (the launcher
        always allocates one with -it). Drop the flag to avoid::

            crun: open .../merged: Permission denied

        Claude Code then runs as UID 0 inside the container, which means
        --dangerously-skip-permissions is rejected — drop that too. The
        user accepts the folder-trust prompt manually once.
        """
        from lightcone.engine.launcher import launch_target

        target = LaunchTarget(
            name="fake",
            containerfile=tmp_path / "fake.Containerfile",
            entrypoint=["claude", "--dangerously-skip-permissions"],
            run_as_host_user=True,
        )
        (tmp_path / "fake.Containerfile").write_text(
            "FROM python:3.12-slim\nARG LIGHTCONE_VERSION\n"
        )
        mock_resolve.return_value = target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        choice = RuntimeChoice(runtime="podman-hpc", explicit=True)
        launch_target(target.name, choice=choice, project_root=project)
        cmd = mock_exec.call_args[0][1]
        assert cmd[0] == "podman-hpc"
        assert "--userns=keep-id" not in cmd
        assert "--user" not in cmd
        assert "--entrypoint" in cmd
        assert "--dangerously-skip-permissions" not in cmd

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
            home_mounts=[".claude/"],  # trailing slash = directory
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


class TestLaunchTargetEnsureHarness:
    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher._ensure_harness_image", return_value="lightcone-claude:1.2.3")
    @patch("lightcone.engine.launcher._apply_tracking_tag")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-sandbox-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_ensure_harness_called_when_committed_tag_prefix_set(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_tracking: MagicMock,
        mock_ensure: MagicMock,
        mock_exec: MagicMock,
        project: Path,
        tmp_path: Path,
    ) -> None:
        """launch_target() calls _ensure_harness_image() and uses the returned tag."""
        from lightcone.engine.launcher import launch_target

        target = LaunchTarget(
            name="claude",
            containerfile=tmp_path / "lightcone-sandbox.Containerfile",
            entrypoint=["claude", "--dangerously-skip-permissions"],
            install_cmds=["npm install -g @anthropic-ai/claude-code"],
            committed_tag_prefix="lightcone-claude",
        )
        (tmp_path / "lightcone-sandbox.Containerfile").write_text(
            "FROM python:3.12-slim\nARG LIGHTCONE_VERSION\n"
        )
        mock_resolve.return_value = target
        tarball = tmp_path / "lc-sandbox-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        with patch("lightcone.engine.launcher._lc_version", return_value="1.2.3"):
            choice = RuntimeChoice(runtime="docker", explicit=True)
            launch_target(target.name, choice=choice, project_root=project)

        mock_ensure.assert_called_once_with(
            target,
            base_image="lc-sandbox-abc123",
            runtime="docker",
            lc_version="1.2.3",
            reinstall=False,
        )
        # _apply_tracking_tag must receive the committed harness tag, not the base image tag
        assert mock_tracking.call_args[0][0] == "lightcone-claude:1.2.3"
        # The harness image tag must be passed to _exec_interactive (via os.execvp)
        cmd = mock_exec.call_args[0][1]
        assert "lightcone-claude:1.2.3" in cmd

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher._ensure_harness_image", return_value="lightcone-claude:1.2.3")
    @patch("lightcone.engine.launcher._apply_tracking_tag")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-sandbox-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_reinstall_forwarded_to_ensure_harness_image(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_tracking: MagicMock,
        mock_ensure: MagicMock,
        mock_exec: MagicMock,
        project: Path,
        tmp_path: Path,
    ) -> None:
        """launch_target() forwards reinstall=True to _ensure_harness_image()."""
        from lightcone.engine.launcher import launch_target

        target = LaunchTarget(
            name="claude",
            containerfile=tmp_path / "lightcone-sandbox.Containerfile",
            entrypoint=["claude", "--dangerously-skip-permissions"],
            install_cmds=["npm install -g @anthropic-ai/claude-code"],
            committed_tag_prefix="lightcone-claude",
        )
        (tmp_path / "lightcone-sandbox.Containerfile").write_text(
            "FROM python:3.12-slim\nARG LIGHTCONE_VERSION\n"
        )
        mock_resolve.return_value = target
        tarball = tmp_path / "lc-sandbox-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        with patch("lightcone.engine.launcher._lc_version", return_value="1.2.3"):
            choice = RuntimeChoice(runtime="docker", explicit=True)
            launch_target(target.name, choice=choice, project_root=project, reinstall=True)

        mock_ensure.assert_called_once_with(
            target,
            base_image="lc-sandbox-abc123",
            runtime="docker",
            lc_version="1.2.3",
            reinstall=True,
        )

    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher._ensure_harness_image")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_ensure_harness_not_called_when_no_committed_tag_prefix(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_ensure: MagicMock,
        mock_exec: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
        tmp_path: Path,
    ) -> None:
        """launch_target() skips _ensure_harness_image() when committed_tag_prefix is empty."""
        from lightcone.engine.launcher import launch_target

        mock_resolve.return_value = fake_target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        choice = RuntimeChoice(runtime="docker", explicit=True)
        launch_target(fake_target.name, choice=choice, project_root=project)

        mock_ensure.assert_not_called()
        # The base tag must be used directly
        cmd = mock_exec.call_args[0][1]
        assert "lc-fake-abc123" in cmd


class TestEnsureHostPath:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        from lightcone.engine.launcher import _ensure_host_path

        target = tmp_path / "settings.json"
        assert not target.exists()
        _ensure_host_path(target, is_dir=False)
        assert target.is_file()

    def test_creates_directory_when_missing(self, tmp_path: Path) -> None:
        from lightcone.engine.launcher import _ensure_host_path

        target = tmp_path / "agents"
        assert not target.exists()
        _ensure_host_path(target, is_dir=True)
        assert target.is_dir()

    def test_does_not_clobber_existing_file(self, tmp_path: Path) -> None:
        from lightcone.engine.launcher import _ensure_host_path

        target = tmp_path / "config.toml"
        target.write_text("existing content")
        _ensure_host_path(target, is_dir=False)
        assert target.read_text() == "existing content"

    def test_does_not_clobber_existing_dir(self, tmp_path: Path) -> None:
        from lightcone.engine.launcher import _ensure_host_path

        target = tmp_path / "agents"
        target.mkdir()
        (target / "my_agent.toml").write_text("agent")
        _ensure_host_path(target, is_dir=True)
        assert (target / "my_agent.toml").exists()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        from lightcone.engine.launcher import _ensure_host_path

        target = tmp_path / "a" / "b" / "settings.json"
        _ensure_host_path(target, is_dir=False)
        assert target.is_file()


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
    @patch("lightcone.engine.launcher._ensure_harness_image", return_value="lightcone-claude:1.2.3")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.save_image_as_tarball")
    @patch("lightcone.engine.launcher.build_image")
    @patch("lightcone.engine.launcher._try_pull_and_cache", return_value=True)
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-claude-abc123")
    def test_registry_name_overrides_target_name(
        self,
        mock_tag: MagicMock,
        mock_pull: MagicMock,
        mock_build: MagicMock,
        mock_save: MagicMock,
        mock_exists: MagicMock,
        mock_ensure: MagicMock,
        mock_exec: MagicMock,
        project: Path,
    ) -> None:
        """claude target uses registry_name='lightcone-sandbox', not 'claude', for GHCR ref."""
        from lightcone.engine.launcher import launch_target

        # No tarball — forces pull path
        with patch("lightcone.engine.launcher._lc_version", return_value="1.2.3"):
            choice = RuntimeChoice(runtime="docker", explicit=True)
            launch_target("claude", choice=choice, project_root=project)

        # _try_pull_and_cache(tag, registry_ref, tarball, runtime=...)
        registry_ref = mock_pull.call_args[0][1]
        assert registry_ref == "ghcr.io/lightconeresearch/lightcone-sandbox:1.2.3"

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

    @patch("lightcone.engine.launcher._apply_tracking_tag")
    @patch("lightcone.engine.launcher.os.execvp")
    @patch("lightcone.engine.launcher.image_exists_locally", return_value=True)
    @patch("lightcone.engine.launcher.tarball_path_for_tag")
    @patch("lightcone.engine.launcher.compute_image_tag", return_value="lc-fake-abc123")
    @patch("lightcone.engine.launcher.resolve_launch_target")
    def test_tracking_tag_applied_before_exec(
        self,
        mock_resolve: MagicMock,
        mock_tag: MagicMock,
        mock_tarball_path: MagicMock,
        mock_exists: MagicMock,
        mock_exec: MagicMock,
        mock_tracking: MagicMock,
        fake_target: LaunchTarget,
        project: Path,
        tmp_path: Path,
    ) -> None:
        from lightcone.engine.launcher import launch_target
        from lightcone.engine.manifest import lc_version as _lc_version

        mock_resolve.return_value = fake_target
        tarball = tmp_path / "lc-fake-abc123.tar"
        tarball.write_bytes(b"fake")
        mock_tarball_path.return_value = tarball

        choice = RuntimeChoice(runtime="docker", explicit=True)
        launch_target(fake_target.name, choice=choice, project_root=project)

        version = _lc_version()
        mock_tracking.assert_called_once_with(
            "lc-fake-abc123",
            f"lightcone-{project.name}:{version}",
            "docker",
        )


class TestImageExists:
    def test_returns_true_when_image_found(self) -> None:
        from lightcone.engine.launcher import _image_exists

        with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _image_exists("lightcone-claude:0.5.0", "docker")

        mock_run.assert_called_once_with(
            ["docker", "image", "inspect", "lightcone-claude:0.5.0"],
            capture_output=True,
        )
        assert result is True

    def test_returns_false_when_image_not_found(self) -> None:
        from lightcone.engine.launcher import _image_exists

        with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = _image_exists("lightcone-claude:0.5.0", "docker")

        assert result is False

    def test_returns_false_on_oserror(self) -> None:
        from lightcone.engine.launcher import _image_exists

        with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("not found")
            result = _image_exists("lightcone-claude:0.5.0", "docker")

        assert result is False


class TestTrackingTag:
    def test_tracking_image_ref_uses_project_dir_name(self, tmp_path: Path) -> None:
        project = tmp_path / "my-analysis"
        project.mkdir()
        ref = _tracking_image_ref(project, "1.2.3")
        assert ref == "lightcone-my-analysis:1.2.3"

    def test_tracking_image_ref_dev_version(self, tmp_path: Path) -> None:
        project = tmp_path / "my-analysis"
        project.mkdir()
        ref = _tracking_image_ref(project, "dev")
        assert ref == "lightcone-my-analysis:dev"

    def test_apply_tracking_tag_calls_runtime_tag(self, tmp_path: Path) -> None:
        with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _apply_tracking_tag("lc-fake-abc123", "lightcone-proj:1.0.0", "docker")
        mock_run.assert_called_once_with(
            ["docker", "tag", "lc-fake-abc123", "lightcone-proj:1.0.0"],
            check=True,
            capture_output=True,
        )

    def test_apply_tracking_tag_swallows_errors(self, tmp_path: Path) -> None:
        import subprocess as _sp

        with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
            mock_run.side_effect = _sp.CalledProcessError(1, "docker tag")
            _apply_tracking_tag("lc-fake-abc123", "lightcone-proj:1.0.0", "docker")

    def test_apply_tracking_tag_swallows_oserror(self, tmp_path: Path) -> None:
        with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("no such file")
            _apply_tracking_tag("lc-fake-abc123", "lightcone-proj:1.0.0", "docker")


class TestEnsureHarnessImage:
    @pytest.fixture
    def harness_target(self, tmp_path: Path) -> LaunchTarget:
        cf = tmp_path / "lightcone-sandbox.Containerfile"
        cf.write_text("FROM python:3.12-slim\nARG LIGHTCONE_VERSION\n")
        return LaunchTarget(
            name="claude",
            containerfile=cf,
            entrypoint=["claude", "--dangerously-skip-permissions"],
            install_cmds=["npm install -g @anthropic-ai/claude-code"],
            committed_tag_prefix="lightcone-claude",
        )

    def test_returns_committed_tag_when_image_exists(
        self, harness_target: LaunchTarget
    ) -> None:
        from lightcone.engine.launcher import _ensure_harness_image

        with patch("lightcone.engine.launcher._image_exists", return_value=True):
            with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
                result = _ensure_harness_image(
                    harness_target, "lc-lightcone-sandbox-abc", "docker", "1.2.3"
                )

        assert result == "lightcone-claude:1.2.3"
        mock_run.assert_not_called()

    def test_installs_and_commits_when_image_absent(
        self, harness_target: LaunchTarget
    ) -> None:
        from lightcone.engine.launcher import _ensure_harness_image

        with patch("lightcone.engine.launcher._image_exists", return_value=False):
            with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = _ensure_harness_image(
                    harness_target, "lc-lightcone-sandbox-abc", "docker", "1.2.3"
                )

        assert result == "lightcone-claude:1.2.3"
        calls = [c[0][0] for c in mock_run.call_args_list]
        # First call: docker run (install)
        assert calls[0][0] == "docker"
        assert calls[0][1] == "run"
        assert "npm install -g @anthropic-ai/claude-code" in calls[0]
        # Second call: docker commit
        assert calls[1][0] == "docker"
        assert calls[1][1] == "commit"
        assert calls[1][-1] == "lightcone-claude:1.2.3"
        # Third call: docker rm (cleanup)
        assert calls[2][0] == "docker"
        assert calls[2][1] == "rm"

    def test_reinstall_skips_image_exists_check(
        self, harness_target: LaunchTarget
    ) -> None:
        from lightcone.engine.launcher import _ensure_harness_image

        with patch("lightcone.engine.launcher._image_exists") as mock_exists:
            with patch("lightcone.engine.launcher.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                _ensure_harness_image(
                    harness_target,
                    "lc-lightcone-sandbox-abc",
                    "docker",
                    "1.2.3",
                    reinstall=True,
                )

        mock_exists.assert_not_called()
        # rmi (remove old) + run (install) + commit + rm (cleanup temp) = 4 calls
        assert mock_run.call_count == 4
        rmi_cmd = mock_run.call_args_list[0][0][0]
        assert rmi_cmd[1] == "rmi"  # first call removes the old committed image

    def test_removes_tmp_container_on_install_failure(
        self, harness_target: LaunchTarget
    ) -> None:
        import subprocess as _sp

        from lightcone.engine.launcher import _ensure_harness_image

        def side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[1] == "run":  # docker run (install step) fails
                raise _sp.CalledProcessError(1, cmd)
            return MagicMock(returncode=0)

        with patch("lightcone.engine.launcher._image_exists", return_value=False):
            with patch(
                "lightcone.engine.launcher.subprocess.run", side_effect=side_effect
            ) as mock_run:
                with pytest.raises(ContainerBuildError):
                    _ensure_harness_image(
                        harness_target, "lc-lightcone-sandbox-abc", "docker", "1.2.3"
                    )

        # docker rm must still have been called despite the failure (finally block)
        assert mock_run.call_count == 2  # run (failed) + rm (cleanup)
        rm_cmd = mock_run.call_args_list[1][0][0]
        assert rm_cmd[1] == "rm"  # second call is cleanup, not commit
