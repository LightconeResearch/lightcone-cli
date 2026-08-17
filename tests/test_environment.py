"""Tests for the environment model: mode, env_version, lock scan."""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_project

from lightcone.engine.environment import (
    InstallSettings,
    LockScan,
    Mode,
    ProjectEnvironmentError,
    compute_env_version,
    load_environment,
    scan_lock,
)
from lightcone.engine.image.declaration import load_image_declaration

# ---- mode detection --------------------------------------------------------


class TestModeDetection:
    def test_direct_by_default(self, direct_project: Path) -> None:
        env = load_environment(direct_project)
        assert env.mode is Mode.DIRECT
        assert env.image is None

    def test_image_table_escalates(self, containerized_project: Path) -> None:
        env = load_environment(containerized_project)
        assert env.mode is Mode.CONTAINERIZED
        assert env.image is not None
        assert env.image.system_packages == ("libhdf5-dev", "r-base-core")

    def test_empty_image_table_escalates(self, tmp_path: Path) -> None:
        """Presence IS the escalation — even an empty table."""
        project = make_project(
            tmp_path / "p", extra_pyproject="\n[tool.lightcone.image]\n"
        )
        assert load_environment(project).mode is Mode.CONTAINERIZED

    def test_extra_file_alone_escalates(self, direct_project: Path) -> None:
        (direct_project / "Containerfile.extra").write_text("RUN echo hi\n")
        env = load_environment(direct_project)
        assert env.mode is Mode.CONTAINERIZED
        assert env.image is not None and env.image.extra is not None

    def test_python_version_read(self, direct_project: Path) -> None:
        assert load_environment(direct_project).python_version == "3.12.12"


class TestRefusals:
    def test_authored_containerfile_refused(self, direct_project: Path) -> None:
        (direct_project / "Containerfile").write_text("FROM debian\n")
        with pytest.raises(ProjectEnvironmentError, match="generates images"):
            load_environment(direct_project)

    def test_packaged_containerized_refused(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            containerized=True,
            extra_pyproject=(
                '\n[build-system]\nrequires = ["hatchling"]\n'
                'build-backend = "hatchling.build"\n'
            ),
        )
        with pytest.raises(ProjectEnvironmentError, match="virtual project"):
            load_environment(project)

    def test_packaged_direct_is_fine(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject=(
                '\n[build-system]\nrequires = ["hatchling"]\n'
                'build-backend = "hatchling.build"\n'
            ),
        )
        assert load_environment(project).packaged is True

    def test_missing_pyproject(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "p")
        (project / "pyproject.toml").unlink()
        with pytest.raises(ProjectEnvironmentError, match="pyproject.toml"):
            load_environment(project)

    def test_missing_lock(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "p")
        (project / "uv.lock").unlink()
        with pytest.raises(ProjectEnvironmentError, match="uv lock"):
            load_environment(project)

    def test_missing_python_version(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "p")
        (project / ".python-version").unlink()
        with pytest.raises(ProjectEnvironmentError, match=".python-version"):
            load_environment(project)

    def test_unknown_lightcone_key_refused(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p", extra_pyproject="\n[tool.lightcone.bogus]\nx = 1\n"
        )
        with pytest.raises(ProjectEnvironmentError, match="bogus"):
            load_environment(project)


class TestWritableProject:
    def test_parsed(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject=(
                '\n[tool.lightcone.sandbox]\nwritable-project = ["result"]\n'
            ),
        )
        env = load_environment(project)
        assert env.writable_project_outputs == frozenset({"result"})

    def test_default_empty(self, direct_project: Path) -> None:
        assert load_environment(direct_project).writable_project_outputs == frozenset()

    def test_bad_type_refused(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject="\n[tool.lightcone.sandbox]\nwritable-project = 1\n",
        )
        with pytest.raises(ProjectEnvironmentError, match="writable-project"):
            load_environment(project)

    def test_unknown_sandbox_key_refused(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject="\n[tool.lightcone.sandbox]\nbogus = 1\n",
        )
        with pytest.raises(ProjectEnvironmentError, match="bogus"):
            load_environment(project)


# ---- env_version -----------------------------------------------------------


def _env_version_of(project: Path) -> str:
    return load_environment(project).env_version


class TestEnvVersion:
    def test_format(self, direct_project: Path) -> None:
        ev = _env_version_of(direct_project)
        assert ev.startswith("sha256:") and len(ev) == 7 + 64

    def test_deterministic(self, tmp_path: Path) -> None:
        a = _env_version_of(make_project(tmp_path / "a"))
        b = _env_version_of(make_project(tmp_path / "b"))
        assert a == b

    def test_golden_pinned(self, direct_project: Path) -> None:
        """Golden fingerprint: moves ⇔ the identity formula changed.

        A failure here means every existing manifest in the wild goes
        stale — deliberate formula changes must bump this string
        consciously, in the same commit.
        """
        assert _env_version_of(direct_project) == (
            "sha256:fe986a87d3d2e4f38e31e38a8930f5b6c425adc4199c7ba17fe875c6c2b37a81"
        )

    def test_golden_pinned_containerized(
        self, containerized_project: Path
    ) -> None:
        assert _env_version_of(containerized_project) == (
            "sha256:12f886a8ada4cbddaaa18595056eeefc26fdbad5d7f4d358620574457d8d006e"
        )

    def test_moves_with_lock(self, direct_project: Path) -> None:
        before = _env_version_of(direct_project)
        (direct_project / "uv.lock").write_text(
            (direct_project / "uv.lock").read_text() + "\n# drift\n"
        )
        assert _env_version_of(direct_project) != before

    def test_moves_with_python_pin(self, direct_project: Path) -> None:
        before = _env_version_of(direct_project)
        (direct_project / ".python-version").write_text("3.12.11\n")
        assert _env_version_of(direct_project) != before

    def test_moves_with_install_settings(self, tmp_path: Path) -> None:
        plain = _env_version_of(make_project(tmp_path / "a"))
        tweaked = _env_version_of(
            make_project(
                tmp_path / "b", extra_pyproject="\n[tool.uv]\nno-binary = true\n"
            )
        )
        assert plain != tweaked

    def test_moves_with_image_declaration(
        self, tmp_path: Path
    ) -> None:
        direct = _env_version_of(make_project(tmp_path / "a"))
        containerized = _env_version_of(
            make_project(tmp_path / "b", containerized=True)
        )
        assert direct != containerized

    def test_moves_with_extra_stage(self, tmp_path: Path) -> None:
        a = make_project(tmp_path / "a", containerized=True)
        before = _env_version_of(a)
        (a / "Containerfile.extra").write_text("RUN echo hi\n")
        assert _env_version_of(a) != before

    def test_does_not_move_with_project_code(self, direct_project: Path) -> None:
        """G5: code edits never move the environment identity."""
        before = _env_version_of(direct_project)
        (direct_project / "analysis.py").write_text("x = 1\n")
        (direct_project / "astra.yaml").write_text("outputs: []\n")
        assert _env_version_of(direct_project) == before

    def test_one_formula_direct_hashes_empty_image(self) -> None:
        """Direct mode hashes the empty image shape — same formula."""
        ev = compute_env_version(
            uv_lock_bytes=b"lock",
            python_version_bytes=b"3.12.12\n",
            install_settings=InstallSettings.from_tool_uv({}),
            image=None,
        )
        assert ev.startswith("sha256:")


# ---- lock scan -------------------------------------------------------------


_LOCK_WITH_HAZARDS = """\
version = 1
requires-python = ">=3.12"

[[package]]
name = "fixture-proj"
version = "0.0.0"
source = { virtual = "." }

[[package]]
name = "numpy"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://example/numpy.whl", hash = "sha256:aa" }]

[[package]]
name = "legacy-sdist"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example/legacy.tar.gz", hash = "sha256:bb" }

[[package]]
name = "local-thing"
version = "0.1"
source = { directory = "../local-thing" }
"""


class TestScanLock:
    def test_clean_lock(self, direct_project: Path) -> None:
        scan = scan_lock(direct_project)
        assert scan == LockScan(refusals=(), sdist_built=(), non_default_groups=())

    def test_hazards(self, direct_project: Path) -> None:
        (direct_project / "uv.lock").write_text(_LOCK_WITH_HAZARDS)
        scan = scan_lock(direct_project)
        assert scan.sdist_built == ("legacy-sdist",)
        assert len(scan.refusals) == 1 and "local-thing" in scan.refusals[0]

    def test_own_package_exempt(self, direct_project: Path) -> None:
        """The project's own (virtual/editable) package never refuses."""
        (direct_project / "uv.lock").write_text(_LOCK_WITH_HAZARDS)
        scan = scan_lock(direct_project)
        assert not any("fixture-proj" in r for r in scan.refusals)

    def test_group_advisory(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject='\n[dependency-groups]\ndocs = ["sphinx"]\n',
        )
        assert scan_lock(project).non_default_groups == ("docs",)

    def test_default_groups_not_advisory(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject='\n[dependency-groups]\ndev = ["pytest"]\n',
        )
        assert scan_lock(project).non_default_groups == ()


# ---- declaration convenience ----------------------------------------------


def test_load_image_declaration_none_for_direct(direct_project: Path) -> None:
    assert load_image_declaration(direct_project) is None
