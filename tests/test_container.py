"""Tests for container image building from Containerfiles."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prism.container import (
    ContainerBuildError,
    build_image,
    build_image_podman_hpc,
    compute_image_tag,
    detect_container_runtime,
    find_dependency_files,
    get_container_status,
    image_exists_locally,
    image_exists_podman_hpc,
    resolve_container_for_slurm,
    resolve_container_spec,
)


@pytest.fixture
def project(tmp_path):
    """Create a minimal project with a Containerfile."""
    containerfile = tmp_path / "Containerfile"
    containerfile.write_text("FROM python:3.12-slim\n")
    return tmp_path


@pytest.fixture
def project_with_deps(project):
    """Project with Containerfile and dependency files."""
    (project / "requirements.txt").write_text("numpy\npandas\n")
    (project / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    return project


class TestFindDependencyFiles:
    def test_finds_requirements_txt(self, project):
        (project / "requirements.txt").write_text("numpy\n")
        found = find_dependency_files(project)
        assert len(found) == 1
        assert found[0].name == "requirements.txt"

    def test_finds_pyproject_toml(self, project):
        (project / "pyproject.toml").write_text("[project]\n")
        found = find_dependency_files(project)
        assert len(found) == 1
        assert found[0].name == "pyproject.toml"

    def test_skips_missing_files(self, project):
        # No dependency files in project fixture
        found = find_dependency_files(project)
        assert found == []

    def test_finds_multiple(self, project_with_deps):
        found = find_dependency_files(project_with_deps)
        names = [f.name for f in found]
        assert "requirements.txt" in names
        assert "pyproject.toml" in names


class TestComputeImageTag:
    def test_deterministic(self, project):
        containerfile = project / "Containerfile"
        tag1 = compute_image_tag("test", containerfile, project)
        tag2 = compute_image_tag("test", containerfile, project)
        assert tag1 == tag2

    def test_tag_format(self, project):
        containerfile = project / "Containerfile"
        tag = compute_image_tag("my-project", containerfile, project)
        assert tag.startswith("prism-my-project-")
        # 12 hex chars after the prefix
        hash_part = tag.removeprefix("prism-my-project-")
        assert len(hash_part) == 12

    def test_changes_with_containerfile(self, project):
        containerfile = project / "Containerfile"
        tag1 = compute_image_tag("test", containerfile, project)
        containerfile.write_text("FROM ubuntu:22.04\n")
        tag2 = compute_image_tag("test", containerfile, project)
        assert tag1 != tag2

    def test_changes_with_requirements(self, project):
        containerfile = project / "Containerfile"
        tag1 = compute_image_tag("test", containerfile, project)
        (project / "requirements.txt").write_text("numpy\n")
        tag2 = compute_image_tag("test", containerfile, project)
        assert tag1 != tag2

    def test_sanitises_project_name(self, project):
        containerfile = project / "Containerfile"
        tag = compute_image_tag("My Project", containerfile, project)
        assert tag.startswith("prism-my-project-")


class TestImageExistsLocally:
    @patch("prism.container.subprocess.run")
    def test_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert image_exists_locally("prism-test-abc123") is True

    @patch("prism.container.subprocess.run")
    def test_not_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert image_exists_locally("prism-test-abc123") is False

    @patch("prism.container.subprocess.run", side_effect=FileNotFoundError)
    def test_docker_not_installed(self, mock_run):
        assert image_exists_locally("prism-test-abc123") is False


class TestBuildImage:
    @patch("prism.container.subprocess.run")
    def test_success(self, mock_run, project):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="built", stderr=""
        )
        result = build_image(
            "prism-test-abc123",
            project / "Containerfile",
            project,
        )
        assert result.tag == "prism-test-abc123"
        assert result.already_existed is False
        assert result.exit_code == 0

    @patch("prism.container.subprocess.run")
    def test_failure(self, mock_run, project):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="some error"
        )
        with pytest.raises(ContainerBuildError, match="docker build failed"):
            build_image("prism-test-abc123", project / "Containerfile", project, runtime="docker")

    @patch("prism.container.subprocess.run", side_effect=FileNotFoundError)
    def test_docker_not_installed(self, mock_run, project):
        with pytest.raises(ContainerBuildError, match="docker is not installed"):
            build_image("prism-test-abc123", project / "Containerfile", project)

    @patch("prism.container.subprocess.run")
    def test_build_args(self, mock_run, project):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )
        build_image(
            "prism-test-abc123",
            project / "Containerfile",
            project,
            build_args={"PY_VERSION": "3.12"},
        )
        cmd = mock_run.call_args[0][0]
        assert "--build-arg" in cmd
        assert "PY_VERSION=3.12" in cmd


class TestResolveContainerSpec:
    def test_none_returns_none(self, project):
        assert resolve_container_spec(None, project, "test") is None

    def test_image_name_returns_as_is(self, project):
        assert resolve_container_spec("python:3.12", project, "test") == "python:3.12"

    def test_containerfile_path_dry_run(self, project):
        tag = resolve_container_spec("Containerfile", project, "test", dry_run=True)
        assert tag is not None
        assert tag.startswith("prism-test-")

    def test_nonexistent_path_treated_as_image(self, tmp_path):
        # A string that doesn't point to an existing file is a pre-built image
        assert resolve_container_spec("NoSuchFile", tmp_path, "test") == "NoSuchFile"

    @patch("prism.container.image_exists_locally", return_value=True)
    def test_exists_skip_build(self, mock_exists, project):
        tag = resolve_container_spec("Containerfile", project, "test")
        assert tag is not None
        assert tag.startswith("prism-test-")

    @patch("prism.container.build_image")
    @patch("prism.container.image_exists_locally", return_value=False)
    def test_not_exists_builds(self, mock_exists, mock_build, project):
        mock_build.return_value = MagicMock(tag="prism-test-abc123")
        tag = resolve_container_spec("Containerfile", project, "test")
        assert tag is not None
        mock_build.assert_called_once()

    @patch("prism.container.build_image")
    @patch("prism.container.image_exists_locally", return_value=True)
    def test_force_rebuilds(self, mock_exists, mock_build, project):
        mock_build.return_value = MagicMock(tag="prism-test-abc123")
        tag = resolve_container_spec("Containerfile", project, "test", force=True)
        assert tag is not None
        mock_build.assert_called_once()


class TestGetContainerStatus:
    def test_none(self, project):
        s = get_container_status(None, project, "test")
        assert s.type == "none"

    def test_prebuilt(self, project):
        s = get_container_status("python:3.12", project, "test")
        assert s.type == "prebuilt"
        assert s.image == "python:3.12"

    def test_nonexistent_path_treated_as_prebuilt(self, tmp_path):
        # A string that doesn't point to an existing file is a pre-built image
        s = get_container_status("NoSuchFile", tmp_path, "test")
        assert s.type == "prebuilt"
        assert s.image == "NoSuchFile"

    @patch("prism.container.image_exists_locally", return_value=False)
    def test_containerfile_not_built(self, mock_exists, project):
        s = get_container_status("Containerfile", project, "test")
        assert s.type == "build"
        assert s.exists is False
        assert s.image is not None

    @patch("prism.container.image_exists_locally", return_value=True)
    def test_containerfile_built(self, mock_exists, project):
        s = get_container_status("Containerfile", project, "test")
        assert s.type == "build"
        assert s.exists is True
        assert s.image is not None


# ---------------------------------------------------------------------------
# HPC container runtimes
# ---------------------------------------------------------------------------


class TestImageExistsPodmanHpc:
    @patch("prism.container.subprocess.run")
    def test_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert image_exists_podman_hpc("my-image:v1") is True

    @patch("prism.container.subprocess.run")
    def test_not_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert image_exists_podman_hpc("my-image:v1") is False

    @patch("prism.container.subprocess.run", side_effect=FileNotFoundError)
    def test_not_installed(self, mock_run):
        assert image_exists_podman_hpc("my-image:v1") is False


class TestBuildImagePodmanHpc:
    @patch("prism.container._podman_hpc_migrate")
    @patch("prism.container.subprocess.run")
    def test_success(self, mock_run, mock_migrate, project):
        mock_run.return_value = MagicMock(returncode=0, stdout="built", stderr="")
        result = build_image_podman_hpc(
            "prism-test-abc123", project / "Containerfile", project,
        )
        assert result.tag == "prism-test-abc123"
        assert result.already_existed is False
        # Should also have migrated
        mock_migrate.assert_called_once_with("prism-test-abc123")

    @patch("prism.container.subprocess.run")
    def test_failure(self, mock_run, project):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="build error")
        with pytest.raises(ContainerBuildError, match="podman-hpc build failed"):
            build_image_podman_hpc(
                "prism-test-abc123", project / "Containerfile", project,
            )

    @patch("prism.container.subprocess.run", side_effect=FileNotFoundError)
    def test_not_installed(self, mock_run, project):
        with pytest.raises(ContainerBuildError, match="podman-hpc is not installed"):
            build_image_podman_hpc(
                "prism-test-abc123", project / "Containerfile", project,
            )


class TestResolveContainerForSlurm:
    def test_none_returns_none(self, project):
        assert resolve_container_for_slurm(None, project, "test", "podman-hpc") is None

    @patch("prism.container.image_exists_podman_hpc", return_value=True)
    def test_prebuilt_podman_already_exists(self, mock_exists, project):
        result = resolve_container_for_slurm(
            "my-image:v1", project, "test", "podman-hpc",
        )
        assert result == "my-image:v1"

    @patch("prism.container._podman_hpc_migrate")
    @patch("prism.container.image_exists_podman_hpc", return_value=False)
    def test_prebuilt_podman_migrates(self, mock_exists, mock_migrate, project):
        result = resolve_container_for_slurm(
            "my-image:v1", project, "test", "podman-hpc",
        )
        assert result == "my-image:v1"
        mock_migrate.assert_called_once_with("my-image:v1")

    @patch("prism.container.build_image_podman_hpc")
    @patch("prism.container.image_exists_podman_hpc", return_value=False)
    def test_containerfile_podman_builds(self, mock_exists, mock_build, project):
        mock_build.return_value = MagicMock(tag="prism-test-abc123")
        tag = resolve_container_for_slurm("Containerfile", project, "test", "podman-hpc")
        assert tag is not None
        assert tag.startswith("prism-test-")
        mock_build.assert_called_once()

    @patch("prism.container.image_exists_podman_hpc", return_value=True)
    def test_containerfile_podman_cached(self, mock_exists, project):
        tag = resolve_container_for_slurm("Containerfile", project, "test", "podman-hpc")
        assert tag is not None
        assert tag.startswith("prism-test-")

    @patch("prism.container._podman_hpc_migrate")
    @patch("prism.container.image_exists_podman_hpc", return_value=False)
    def test_nonexistent_path_treated_as_image(self, mock_exists, mock_migrate, tmp_path):
        # A string that doesn't point to an existing file is a pre-built image
        tag = resolve_container_for_slurm("NoSuchFile", tmp_path, "test", "podman-hpc")
        assert tag == "NoSuchFile"
        mock_migrate.assert_called_once_with("NoSuchFile")


class TestDetectContainerRuntime:
    @patch("prism.container.shutil.which")
    def test_docker_found(self, mock_which):
        mock_which.side_effect = lambda name: "/usr/bin/docker" if name == "docker" else None
        assert detect_container_runtime() == "docker"

    @patch("prism.container.shutil.which")
    def test_podman_only(self, mock_which):
        mock_which.side_effect = lambda name: "/usr/bin/podman" if name == "podman" else None
        assert detect_container_runtime() == "podman"

    @patch("prism.container.shutil.which")
    def test_docker_preferred_over_podman(self, mock_which):
        mock_which.side_effect = lambda name: f"/usr/bin/{name}"
        assert detect_container_runtime() == "docker"

    @patch("prism.container.shutil.which", return_value=None)
    def test_neither_found(self, mock_which):
        assert detect_container_runtime() is None


class TestPodmanSupport:
    @pytest.fixture()
    def project(self, tmp_path):
        (tmp_path / "Containerfile").write_text("FROM python:3.12-slim\n")
        (tmp_path / "requirements.txt").write_text("numpy\n")
        return tmp_path

    @patch("prism.container.subprocess.run")
    def test_image_exists_with_podman(self, mock_run, project):
        mock_run.return_value = MagicMock(returncode=0)
        assert image_exists_locally("some-tag", runtime="podman") is True
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0][0] == "podman"

    @patch("prism.container.subprocess.run")
    def test_build_image_with_podman(self, mock_run, project):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = build_image(
            "test-tag", project / "Containerfile", project, runtime="podman",
        )
        assert result.tag == "test-tag"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert cmd[1] == "build"

    @patch("prism.container.subprocess.run", side_effect=FileNotFoundError)
    def test_build_image_podman_not_installed(self, mock_run, project):
        with pytest.raises(ContainerBuildError, match="podman is not installed"):
            build_image("test-tag", project / "Containerfile", project, runtime="podman")

    @patch("prism.container.subprocess.run")
    def test_resolve_container_spec_with_podman(self, mock_run, project):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        tag = resolve_container_spec("Containerfile", project, "test", runtime="podman")
        assert tag is not None
        # First call should be image inspect, second should be build
        calls = mock_run.call_args_list
        assert calls[0][0][0][0] == "podman"
