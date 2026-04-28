"""Tests for the container runtime layer.

Covers tag computation, build invocation, runtime detection/config, and
the recipe wrap that the Snakefile generator embeds into ``shell()``.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from lightcone.engine.container import (
    RUNTIMES,
    ContainerBuildError,
    build_image,
    compute_image_tag,
    detect_runtime,
    find_dependency_files,
    get_container_status,
    image_exists_locally,
    image_exists_podman_hpc,
    is_containerfile,
    load_runtime,
    pull_image,
    resolve_image_for_run,
    wrap_recipe,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal project with a Containerfile."""
    (tmp_path / "Containerfile").write_text("FROM python:3.12-slim\n")
    return tmp_path


@pytest.fixture
def project_with_deps(project: Path) -> Path:
    (project / "requirements.txt").write_text("numpy\npandas\n")
    (project / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    return project


# ---- find_dependency_files / compute_image_tag ----------------------------


class TestFindDependencyFiles:
    def test_finds_requirements_txt(self, project: Path) -> None:
        (project / "requirements.txt").write_text("numpy\n")
        found = find_dependency_files(project)
        assert [f.name for f in found] == ["requirements.txt"]

    def test_finds_pyproject_toml(self, project: Path) -> None:
        (project / "pyproject.toml").write_text("[project]\n")
        found = find_dependency_files(project)
        assert [f.name for f in found] == ["pyproject.toml"]

    def test_skips_missing_files(self, project: Path) -> None:
        assert find_dependency_files(project) == []

    def test_finds_multiple(self, project_with_deps: Path) -> None:
        names = {f.name for f in find_dependency_files(project_with_deps)}
        assert {"requirements.txt", "pyproject.toml"} <= names


class TestComputeImageTag:
    def test_deterministic(self, project: Path) -> None:
        cf = project / "Containerfile"
        assert compute_image_tag("test", cf, project) == compute_image_tag("test", cf, project)

    def test_tag_format(self, project: Path) -> None:
        tag = compute_image_tag("my-project", project / "Containerfile", project)
        assert tag.startswith("lc-my-project-")
        assert len(tag.removeprefix("lc-my-project-")) == 12

    def test_changes_with_containerfile(self, project: Path) -> None:
        cf = project / "Containerfile"
        tag1 = compute_image_tag("test", cf, project)
        cf.write_text("FROM ubuntu:22.04\n")
        tag2 = compute_image_tag("test", cf, project)
        assert tag1 != tag2

    def test_changes_with_requirements(self, project: Path) -> None:
        cf = project / "Containerfile"
        tag1 = compute_image_tag("test", cf, project)
        (project / "requirements.txt").write_text("numpy\n")
        tag2 = compute_image_tag("test", cf, project)
        assert tag1 != tag2

    def test_sanitises_project_name(self, project: Path) -> None:
        tag = compute_image_tag("My Project", project / "Containerfile", project)
        assert tag.startswith("lc-my-project-")

    def test_changes_with_uv_lock(self, project: Path) -> None:
        cf = project / "Containerfile"
        tag1 = compute_image_tag("test", cf, project)
        (project / "uv.lock").write_text("# v1\n")
        tag2 = compute_image_tag("test", cf, project)
        assert tag1 != tag2

    def test_changes_with_copied_file(self, project: Path) -> None:
        cf = project / "Containerfile"
        cf.write_text("FROM python:3.12-slim\nCOPY app.py /app/app.py\n")
        (project / "app.py").write_text("print(1)\n")
        tag1 = compute_image_tag("test", cf, project)
        (project / "app.py").write_text("print(2)\n")
        tag2 = compute_image_tag("test", cf, project)
        assert tag1 != tag2

    def test_changes_with_copied_directory(self, project: Path) -> None:
        cf = project / "Containerfile"
        cf.write_text("FROM python:3.12-slim\nCOPY src/ /app/src/\n")
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("a = 1\n")
        tag1 = compute_image_tag("test", cf, project)
        (project / "src" / "b.py").write_text("b = 2\n")
        tag2 = compute_image_tag("test", cf, project)
        assert tag1 != tag2

    def test_copy_dir_ignores_results(self, project: Path) -> None:
        cf = project / "Containerfile"
        cf.write_text("FROM python:3.12-slim\nCOPY . /app/\n")
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("a = 1\n")
        tag1 = compute_image_tag("test", cf, project)
        # Touching results/ or .lightcone/ must not invalidate the tag —
        # they aren't in the build context for any sane Containerfile.
        (project / "results").mkdir()
        (project / "results" / "out.txt").write_text("data\n")
        (project / ".lightcone").mkdir()
        (project / ".lightcone" / "Snakefile").write_text("rule x:\n")
        tag2 = compute_image_tag("test", cf, project)
        assert tag1 == tag2

    def test_skips_from_stage_copy(self, project: Path) -> None:
        cf = project / "Containerfile"
        cf.write_text(
            "FROM python:3.12-slim AS builder\n"
            "FROM python:3.12-slim\n"
            "COPY --from=builder /tmp/x /app/x\n"
        )
        # No real source on host, but parsing must not raise or expand.
        tag = compute_image_tag("test", cf, project)
        assert tag.startswith("lc-test-")

    def test_skips_url_add(self, project: Path) -> None:
        cf = project / "Containerfile"
        cf.write_text(
            "FROM python:3.12-slim\nADD https://example.com/x.tgz /app/x.tgz\n"
        )
        tag = compute_image_tag("test", cf, project)
        assert tag.startswith("lc-test-")

    def test_glob_copy_invalidates_on_match_change(self, project: Path) -> None:
        cf = project / "Containerfile"
        cf.write_text("FROM python:3.12-slim\nCOPY *.py /app/\n")
        (project / "main.py").write_text("x = 1\n")
        tag1 = compute_image_tag("test", cf, project)
        (project / "main.py").write_text("x = 2\n")
        tag2 = compute_image_tag("test", cf, project)
        assert tag1 != tag2

    def test_swap_dep_file_names_not_collision(self, project: Path) -> None:
        # Same total bytes, swapped between two dep files: must not collide
        # (the old concat-without-delimiter scheme would have).
        (project / "requirements.txt").write_text("numpy\n")
        (project / "requirements-dev.txt").write_text("pandas\n")
        tag1 = compute_image_tag("test", project / "Containerfile", project)
        (project / "requirements.txt").write_text("pandas\n")
        (project / "requirements-dev.txt").write_text("numpy\n")
        tag2 = compute_image_tag("test", project / "Containerfile", project)
        assert tag1 != tag2


# ---- image_exists_locally / image_exists_podman_hpc -----------------------


class TestImageExistsLocally:
    @patch("lightcone.engine.container.subprocess.run")
    def test_docker_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        assert image_exists_locally("lc-foo", runtime="docker") is True
        assert mock_run.call_args[0][0][0] == "docker"

    @patch("lightcone.engine.container.subprocess.run")
    def test_podman_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        assert image_exists_locally("lc-foo", runtime="podman") is True
        assert mock_run.call_args[0][0][0] == "podman"

    @patch("lightcone.engine.container.subprocess.run")
    def test_not_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        assert image_exists_locally("lc-foo", runtime="docker") is False

    @patch("lightcone.engine.container.subprocess.run", side_effect=FileNotFoundError)
    def test_runtime_not_installed(self, mock_run: MagicMock) -> None:
        assert image_exists_locally("lc-foo", runtime="docker") is False

    @patch("lightcone.engine.container.image_exists_podman_hpc", return_value=True)
    def test_podman_hpc_delegates(self, mock_phpc: MagicMock) -> None:
        assert image_exists_locally("lc-foo", runtime="podman-hpc") is True
        mock_phpc.assert_called_once_with("lc-foo")


class TestImageExistsPodmanHpc:
    @patch("lightcone.engine.container.subprocess.run")
    def test_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        assert image_exists_podman_hpc("img:v1") is True

    @patch("lightcone.engine.container.subprocess.run", side_effect=FileNotFoundError)
    def test_not_installed(self, mock_run: MagicMock) -> None:
        assert image_exists_podman_hpc("img:v1") is False


# ---- build_image ----------------------------------------------------------


class TestBuildImage:
    @patch("lightcone.engine.container.subprocess.run")
    def test_docker_success(self, mock_run: MagicMock, project: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = build_image("lc-test", project / "Containerfile", project, runtime="docker")
        assert result.tag == "lc-test"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert cmd[1] == "build"

    @patch("lightcone.engine.container.subprocess.run")
    def test_podman_success(self, mock_run: MagicMock, project: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = build_image("lc-test", project / "Containerfile", project, runtime="podman")
        assert result.tag == "lc-test"
        assert mock_run.call_args[0][0][0] == "podman"

    @patch("lightcone.engine.container._podman_hpc_migrate")
    @patch("lightcone.engine.container.subprocess.run")
    def test_podman_hpc_migrates_after_build(
        self, mock_run: MagicMock, mock_migrate: MagicMock, project: Path
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        build_image("lc-test", project / "Containerfile", project, runtime="podman-hpc")
        mock_migrate.assert_called_once_with("lc-test")

    @patch("lightcone.engine.container.subprocess.run")
    def test_failure_raises(self, mock_run: MagicMock, project: Path) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(ContainerBuildError, match="docker build failed"):
            build_image("lc-test", project / "Containerfile", project, runtime="docker")

    @patch("lightcone.engine.container.subprocess.run", side_effect=FileNotFoundError)
    def test_runtime_missing_raises(self, mock_run: MagicMock, project: Path) -> None:
        with pytest.raises(ContainerBuildError, match="podman is not installed"):
            build_image("lc-test", project / "Containerfile", project, runtime="podman")

    def test_unsupported_runtime_raises(self, project: Path) -> None:
        with pytest.raises(ContainerBuildError, match="Unsupported build runtime"):
            build_image(
                "lc-test", project / "Containerfile", project, runtime="noop"
            )

    @patch("lightcone.engine.container.subprocess.run")
    def test_build_args(self, mock_run: MagicMock, project: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        build_image(
            "lc-test",
            project / "Containerfile",
            project,
            runtime="docker",
            build_args={"PY_VERSION": "3.12"},
        )
        cmd = mock_run.call_args[0][0]
        assert "--build-arg" in cmd
        assert "PY_VERSION=3.12" in cmd

    def test_build_stages_context_off_source_tree(self, project: Path) -> None:
        """Build context must be a tempdir, not the source project.

        On NERSC, projects living on DVS-mounted home/CFS hit
        ``llistxattr EPROTO`` when buildah's copier walks COPY sources.
        Staging into ``$TMPDIR`` (tmpfs) is what lets builds succeed there.
        """
        cf = project / "Containerfile"
        cf.write_text("FROM python:3.12-slim\nCOPY app.py /app/app.py\n")
        (project / "app.py").write_text("print('hi')\n")

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            ctx = Path(cmd[-1])
            captured["ctx"] = ctx
            captured["files"] = sorted(
                p.relative_to(ctx).as_posix()
                for p in ctx.rglob("*")
                if p.is_file()
            )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "lightcone.engine.container.subprocess.run", side_effect=fake_run
        ):
            build_image("lc-test", cf, project, runtime="podman")

        assert captured["ctx"].resolve() != project.resolve()
        assert not captured["ctx"].exists()
        assert "Containerfile" in captured["files"]
        assert "app.py" in captured["files"]

    def test_build_stages_copy_dot_with_excludes(self, project: Path) -> None:
        """``COPY .`` mirrors the project but skips excluded subtrees."""
        cf = project / "Containerfile"
        cf.write_text("FROM python:3.12-slim\nCOPY . /app/\n")
        (project / "src").mkdir()
        (project / "src" / "main.py").write_text("x = 1\n")
        (project / ".git").mkdir()
        (project / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (project / "results").mkdir()
        (project / "results" / "out.txt").write_text("data\n")

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            ctx = Path(cmd[-1])
            captured["files"] = sorted(
                p.relative_to(ctx).as_posix()
                for p in ctx.rglob("*")
                if p.is_file()
            )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "lightcone.engine.container.subprocess.run", side_effect=fake_run
        ):
            build_image("lc-test", cf, project, runtime="podman")

        assert "src/main.py" in captured["files"]
        assert "Containerfile" in captured["files"]
        assert not any(f.startswith(".git/") for f in captured["files"])
        assert not any(f.startswith("results/") for f in captured["files"])

    @patch("lightcone.engine.container.subprocess.run")
    def test_build_cleans_stage_on_failure(
        self, mock_run: MagicMock, project: Path
    ) -> None:
        """Staged tempdir is removed even when the build fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(ContainerBuildError):
            build_image("lc-test", project / "Containerfile", project, runtime="docker")
        ctx = Path(mock_run.call_args[0][0][-1])
        assert not ctx.exists()


# ---- pull_image -----------------------------------------------------------


class TestPullImage:
    @patch("lightcone.engine.container.subprocess.run")
    def test_pull_success_docker(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        pull_image("python:3.12-slim", runtime="docker")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "pull", "python:3.12-slim"]

    @patch("lightcone.engine.container.subprocess.run")
    def test_pull_success_podman(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        pull_image("python:3.12-slim", runtime="podman")
        assert mock_run.call_args[0][0][0] == "podman"

    @patch("lightcone.engine.container._podman_hpc_migrate")
    @patch("lightcone.engine.container.subprocess.run")
    def test_pull_podman_hpc_migrates(
        self, mock_run: MagicMock, mock_migrate: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        pull_image("python:3.12-slim", runtime="podman-hpc")
        mock_migrate.assert_called_once_with("python:3.12-slim")

    @patch("lightcone.engine.container.subprocess.run")
    def test_pull_failure_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        with pytest.raises(ContainerBuildError, match="docker pull"):
            pull_image("python:3.12-slim", runtime="docker")

    def test_unsupported_runtime_raises(self) -> None:
        with pytest.raises(ContainerBuildError, match="not supported for the apptainer runtime"):
            pull_image("img", runtime="apptainer")


# ---- detect_runtime / load_runtime ---------------------------------------


class TestDetectRuntime:
    @patch("lightcone.engine.container.shutil.which")
    def test_podman_preferred(self, mock_which: MagicMock) -> None:
        # Both installed — podman wins (rootless, no daemon to wedge).
        mock_which.side_effect = lambda name: f"/usr/bin/{name}"
        assert detect_runtime() == "podman"

    @patch("lightcone.engine.container.shutil.which")
    def test_docker_only(self, mock_which: MagicMock) -> None:
        mock_which.side_effect = lambda name: "/usr/bin/docker" if name == "docker" else None
        with patch(
            "lightcone.engine.container._docker_daemon_up", return_value=True
        ):
            assert detect_runtime() == "docker"

    @patch("lightcone.engine.container.shutil.which")
    def test_docker_skipped_when_daemon_down(self, mock_which: MagicMock) -> None:
        # docker on PATH but its daemon is unreachable → fall through.
        # Without this probe, a laptop with docker installed but stopped
        # would silently pick docker and every recipe would fail with a
        # socket error. With nothing else available, returns None.
        mock_which.side_effect = lambda name: "/usr/bin/docker" if name == "docker" else None
        with patch(
            "lightcone.engine.container._docker_daemon_up", return_value=False
        ):
            assert detect_runtime() is None

    @patch("lightcone.engine.container.shutil.which")
    def test_docker_daemon_down_falls_through_to_podman(
        self, mock_which: MagicMock
    ) -> None:
        # Both binaries present, docker daemon down → podman is picked
        # regardless of order in RUNTIMES.
        mock_which.side_effect = lambda name: f"/usr/bin/{name}"
        with patch(
            "lightcone.engine.container._docker_daemon_up", return_value=False
        ):
            assert detect_runtime() == "podman"

    @patch("lightcone.engine.container.shutil.which", return_value=None)
    def test_none_available(self, mock_which: MagicMock) -> None:
        assert detect_runtime() is None

    def test_apptainer_in_runtimes(self) -> None:
        # apptainer is supported as a nested execution runtime (inside lc launch containers)
        assert "apptainer" in RUNTIMES


class TestLoadRuntime:
    def _write_config(self, tmp_path: Path, content: dict) -> None:
        cfg_dir = tmp_path / ".lightcone"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text(yaml.safe_dump(content))

    def test_no_config_uses_auto(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            "lightcone.engine.container.detect_runtime", lambda: "docker"
        )
        choice = load_runtime()
        assert choice.runtime == "docker"
        assert choice.explicit is False

    def test_auto_with_no_runtime_returns_none_implicitly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """auto + nothing on PATH → none, but explicit=False so the
        caller can warn that this is a silent fallback."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            "lightcone.engine.container.detect_runtime", lambda: None
        )
        choice = load_runtime()
        assert choice.runtime == "none"
        assert choice.explicit is False

    def test_explicit_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """User opted out of containers — explicit=True, no warnings owed."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_config(tmp_path, {"container": {"runtime": "none"}})
        choice = load_runtime()
        assert choice.runtime == "none"
        assert choice.explicit is True

    def test_explicit_runtime_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            "lightcone.engine.container.shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "podman" else None,
        )
        self._write_config(tmp_path, {"container": {"runtime": "podman"}})
        choice = load_runtime()
        assert choice.runtime == "podman"
        assert choice.explicit is True

    def test_explicit_runtime_missing_on_path_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            "lightcone.engine.container.shutil.which", lambda _: None
        )
        self._write_config(tmp_path, {"container": {"runtime": "podman"}})
        with pytest.raises(ContainerBuildError, match="not on PATH"):
            load_runtime()

    def test_unknown_runtime_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_config(tmp_path, {"container": {"runtime": "noop"}})
        with pytest.raises(ContainerBuildError, match="Unknown container.runtime"):
            load_runtime()


# ---- resolve_image_for_run -----------------------------------------------


class TestResolveImageForRun:
    def test_none_returns_none(self, project: Path) -> None:
        assert resolve_image_for_run(
            None, project_path=project, project_name="test"
        ) is None

    def test_registry_image_passes_through(self, project: Path) -> None:
        assert resolve_image_for_run(
            "python:3.12-slim", project_path=project, project_name="test"
        ) == "python:3.12-slim"

    def test_namespaced_registry_image_passes_through(self, project: Path) -> None:
        assert resolve_image_for_run(
            "ghcr.io/foo/bar:tag", project_path=project, project_name="test"
        ) == "ghcr.io/foo/bar:tag"

    def test_containerfile_resolves_to_tag(self, project: Path) -> None:
        result = resolve_image_for_run(
            "Containerfile", project_path=project, project_name="test"
        )
        assert result is not None
        assert result.startswith("lc-test-")


# ---- wrap_recipe ----------------------------------------------------------


class TestWrapRecipe:
    def test_no_image_passthrough(self) -> None:
        assert wrap_recipe("echo hi", image=None, runtime="podman") == "echo hi"

    def test_runtime_none_passthrough(self) -> None:
        assert wrap_recipe(
            "echo hi", image="python:3.12-slim", runtime="none"
        ) == "echo hi"

    def test_podman_wrap_basic(self) -> None:
        wrapped = wrap_recipe(
            "echo hi", image="python:3.12-slim", runtime="podman"
        )
        assert wrapped.startswith("podman run --rm --pull=never ")
        assert "python:3.12-slim" in wrapped
        # The recipe is shell-quoted to survive nested shells.
        assert shlex.quote("echo hi") in wrapped

    def test_docker_wrap(self) -> None:
        wrapped = wrap_recipe("echo hi", image="img:v1", runtime="docker")
        assert wrapped.startswith("docker run --rm --pull=never ")

    def test_podman_hpc_wrap(self) -> None:
        wrapped = wrap_recipe("echo hi", image="img:v1", runtime="podman-hpc")
        assert wrapped.startswith("podman-hpc run --rm --pull=never ")

    def test_pull_never_short_name_safe(self) -> None:
        """``--pull=never`` is what makes locally-built short-name images
        like ``lc-foo-abc123`` work under podman, which would otherwise
        try to resolve the name against unqualified-search-registries."""
        wrapped = wrap_recipe(
            "echo", image="lc-foo-abc123", runtime="podman"
        )
        assert "--pull=never" in wrapped

    def test_preserves_snakemake_placeholders(self) -> None:
        """Snakemake's ``{output[0]}`` must survive the wrap so it can
        substitute at exec time."""
        wrapped = wrap_recipe(
            "echo > {output[0]}/x", image="img:v1", runtime="podman"
        )
        assert "{output[0]}" in wrapped

    def test_preserves_recipe_with_single_quotes(self) -> None:
        """Recipes may contain single quotes (e.g. ``python -c 'print(1)'``).
        The shlex.quote escape must survive nested shell parsing."""
        recipe = """python -c 'print("hi")'"""
        wrapped = wrap_recipe(recipe, image="img:v1", runtime="podman")
        # Round-trip through shlex.split should yield the original recipe
        # as the last argument (the bash -c argument).
        tokens = shlex.split(wrapped)
        assert tokens[-1] == recipe

    def test_unsupported_runtime_raises(self) -> None:
        with pytest.raises(ContainerBuildError, match="Unsupported run runtime"):
            wrap_recipe("echo", image="img:v1", runtime="noop")

    def test_bind_mounts_pwd(self) -> None:
        """Recipes that write to relative paths need $PWD bind-mounted."""
        wrapped = wrap_recipe("echo", image="img:v1", runtime="podman")
        assert '-v "$PWD":"$PWD"' in wrapped
        assert '-w "$PWD"' in wrapped


# ---- get_container_status -------------------------------------------------


class TestGetContainerStatus:
    def test_none(self, project: Path) -> None:
        s = get_container_status(None, project, "test", runtime="docker")
        assert s.type == "none"

    def test_prebuilt(self, project: Path) -> None:
        s = get_container_status("python:3.12", project, "test", runtime="docker")
        assert s.type == "prebuilt"
        assert s.image == "python:3.12"

    @patch("lightcone.engine.container.image_exists_locally", return_value=False)
    def test_containerfile_not_built(
        self, mock_exists: MagicMock, project: Path
    ) -> None:
        s = get_container_status("Containerfile", project, "test", runtime="docker")
        assert s.type == "build"
        assert s.exists is False
        assert s.image is not None

    @patch("lightcone.engine.container.image_exists_locally", return_value=True)
    def test_containerfile_built(
        self, mock_exists: MagicMock, project: Path
    ) -> None:
        s = get_container_status("Containerfile", project, "test", runtime="docker")
        assert s.type == "build"
        assert s.exists is True

    def test_runtime_none_skips_existence_check(self, project: Path) -> None:
        s = get_container_status("Containerfile", project, "test", runtime="none")
        assert s.type == "build"
        assert s.exists is None


# ---- tarball helpers -------------------------------------------------------


class TestTarballPathForTag:
    def test_canonical_path(self, tmp_path: Path) -> None:
        from lightcone.engine.container import tarball_path_for_tag

        p = tarball_path_for_tag("lc-foo-abc123", tmp_path)
        assert p == tmp_path / ".lightcone" / "images" / "lc-foo-abc123.tar"

    def test_different_tags_differ(self, tmp_path: Path) -> None:
        from lightcone.engine.container import tarball_path_for_tag

        p1 = tarball_path_for_tag("lc-a-111111", tmp_path)
        p2 = tarball_path_for_tag("lc-b-222222", tmp_path)
        assert p1 != p2


class TestSaveImageAsTarball:
    @patch("lightcone.engine.container.subprocess.run")
    def test_calls_runtime_save(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from lightcone.engine.container import save_image_as_tarball

        mock_run.return_value = MagicMock(returncode=0)
        tarball = tmp_path / ".lightcone" / "images" / "lc-foo.tar"
        save_image_as_tarball("lc-foo", tarball, runtime="docker")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert cmd[1] == "save"
        assert "lc-foo" in cmd

    @patch("lightcone.engine.container.subprocess.run")
    def test_creates_parent_dir(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from lightcone.engine.container import save_image_as_tarball

        mock_run.return_value = MagicMock(returncode=0)
        tarball = tmp_path / "deep" / "nested" / "img.tar"
        save_image_as_tarball("lc-foo", tarball, runtime="podman")
        assert tarball.parent.exists()

    @patch("lightcone.engine.container.subprocess.run")
    def test_failure_raises(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from lightcone.engine.container import save_image_as_tarball, ContainerBuildError

        mock_run.return_value = MagicMock(returncode=1)
        tarball = tmp_path / "img.tar"
        with pytest.raises(ContainerBuildError, match="save"):
            save_image_as_tarball("lc-foo", tarball, runtime="docker")


class TestLoadImageFromTarball:
    @patch("lightcone.engine.container.subprocess.run")
    def test_calls_runtime_load(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from lightcone.engine.container import load_image_from_tarball

        mock_run.return_value = MagicMock(returncode=0, stderr="")
        tarball = tmp_path / "img.tar"
        tarball.write_bytes(b"fake")
        load_image_from_tarball(tarball, runtime="podman")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert "load" in cmd

    @patch("lightcone.engine.container.subprocess.run")
    def test_failure_raises(self, mock_run: MagicMock, tmp_path: Path) -> None:
        from lightcone.engine.container import load_image_from_tarball, ContainerBuildError

        mock_run.return_value = MagicMock(returncode=1, stderr=b"bad tarball")
        tarball = tmp_path / "img.tar"
        tarball.write_bytes(b"fake")
        with pytest.raises(ContainerBuildError, match="load"):
            load_image_from_tarball(tarball, runtime="docker")


# ---- is_containerfile -----------------------------------------------------


class TestIsContainerfile:
    def test_existing_file(self, project: Path) -> None:
        assert is_containerfile("Containerfile", project) is True

    def test_missing_file(self, project: Path) -> None:
        assert is_containerfile("python:3.12-slim", project) is False


# ---- apptainer runtime support ---------------------------------------------


class TestApptainerRuntime:
    def test_image_exists_locally_checks_tarball(self, tmp_path: Path) -> None:
        from lightcone.engine.container import image_exists_locally, tarball_path_for_tag

        assert image_exists_locally("lc-foo-abc", runtime="apptainer", project_path=tmp_path) is False
        tarball = tarball_path_for_tag("lc-foo-abc", tmp_path)
        tarball.parent.mkdir(parents=True)
        tarball.write_bytes(b"fake")
        assert image_exists_locally("lc-foo-abc", runtime="apptainer", project_path=tmp_path) is True

    def test_image_exists_locally_no_project_path_returns_false(self) -> None:
        from lightcone.engine.container import image_exists_locally

        assert image_exists_locally("lc-foo", runtime="apptainer") is False

    @patch("lightcone.engine.container.subprocess.run")
    def test_build_image_apptainer_uses_buildah(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        from lightcone.engine.container import build_image

        (tmp_path / "Containerfile").write_text("FROM python:3.12-slim\n")
        tarball = tmp_path / ".lightcone" / "images" / "lc-test.tar"
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = build_image(
            "lc-test",
            tmp_path / "Containerfile",
            tmp_path,
            runtime="apptainer",
            tarball_path=tarball,
        )
        assert result.tag == "lc-test"
        # First call: buildah build
        first_cmd = mock_run.call_args_list[0][0][0]
        assert first_cmd[0] == "buildah"
        assert "build" in first_cmd
        # Second call: buildah push oci-archive:
        second_cmd = mock_run.call_args_list[1][0][0]
        assert second_cmd[0] == "buildah"
        assert "push" in second_cmd
        assert f"oci-archive:{tarball}" in " ".join(second_cmd)

    def test_build_image_apptainer_requires_tarball_path(self, tmp_path: Path) -> None:
        from lightcone.engine.container import build_image, ContainerBuildError

        (tmp_path / "Containerfile").write_text("FROM python:3.12-slim\n")
        with pytest.raises(ContainerBuildError, match="tarball_path is required"):
            build_image(
                "lc-test",
                tmp_path / "Containerfile",
                tmp_path,
                runtime="apptainer",
            )

    def test_wrap_recipe_apptainer(self) -> None:
        from lightcone.engine.container import wrap_recipe

        wrapped = wrap_recipe("echo hi", image="lc-foo-abc123", runtime="apptainer")
        assert wrapped.startswith("apptainer exec oci-archive:")
        assert "lc-foo-abc123.tar" in wrapped
        assert shlex.quote("echo hi") in wrapped

    def test_wrap_recipe_apptainer_preserves_placeholders(self) -> None:
        from lightcone.engine.container import wrap_recipe

        wrapped = wrap_recipe(
            "python run.py --out {output[0]}", image="lc-foo", runtime="apptainer"
        )
        assert "{output[0]}" in wrapped

    def test_pull_image_apptainer_raises(self) -> None:
        from lightcone.engine.container import pull_image, ContainerBuildError

        with pytest.raises(ContainerBuildError, match="not supported for the apptainer runtime"):
            pull_image("python:3.12-slim", runtime="apptainer")
