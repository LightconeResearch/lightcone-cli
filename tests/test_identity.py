"""Tests for `lightcone.engine.identity` — what an output is identified by.

These are sensitivity tests. A content hash is only worth having if it
moves when the thing it identifies moves and stays put when it does not,
so almost every case here is "change one thing, assert the hash did or did
not follow" — including the ones that assert it *did not*, which are the
ones a careless formula breaks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine.identity import LockScan, code_version, env_version, scan_lock
from lightcone.engine.project import ProjectError

_LOCK = """version = 1
requires-python = ">=3.11"

[[package]]
name = "demo"
version = "0.1.0"
source = { virtual = "." }
"""

_PYPROJECT = """[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []
"""


_REGISTRY = 'source = { registry = "https://pypi.org/simple" }'


def _with_package(root: Path, *lines: str) -> None:
    """Append one `[[package]]` entry to the project's lock."""
    (root / "uv.lock").write_text(_LOCK + "\n[[package]]\n" + "\n".join(lines) + "\n")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A project with just the three files identity is made of."""
    (tmp_path / "uv.lock").write_text(_LOCK)
    (tmp_path / ".python-version").write_text("3.13.14\n")
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT)
    return tmp_path


# ---- env_version -----------------------------------------------------------


def test_env_version_is_stable_and_shaped_like_a_digest(root: Path) -> None:
    assert env_version(root) == env_version(root)
    assert env_version(root).startswith("sha256:")


def test_the_lock_moves_it(root: Path) -> None:
    before = env_version(root)
    (root / "uv.lock").write_text(_LOCK + '\n[[package]]\nname = "numpy"\n')
    assert env_version(root) != before


def test_even_a_comment_in_the_lock_moves_it(root: Path) -> None:
    """Raw bytes, deliberately. Over-invalidating beats a parse that
    silently disagrees with uv about what the lock means."""
    before = env_version(root)
    (root / "uv.lock").write_text("# regenerated\n" + _LOCK)
    assert env_version(root) != before


def test_the_interpreter_pin_moves_it(root: Path) -> None:
    before = env_version(root)
    (root / ".python-version").write_text("3.12.9\n")
    assert env_version(root) != before


def test_an_install_setting_moves_it(root: Path) -> None:
    """The lock says what *could* be installed; these say which of it is."""
    before = env_version(root)
    (root / "pyproject.toml").write_text(_PYPROJECT + "\n[tool.uv]\nno-binary = true\n")
    assert env_version(root) != before


def test_a_setting_outside_the_audited_list_does_not_move_it(root: Path) -> None:
    """The list is closed. A setting that changes nothing about which
    artifacts a sync materializes must not stale every output in the
    project."""
    before = env_version(root)
    (root / "pyproject.toml").write_text(_PYPROJECT + "\n[tool.uv]\npackage = false\n")
    assert env_version(root) == before


def test_project_code_does_not_move_it(root: Path) -> None:
    """The environment is not the analysis. Editing a recipe's source has
    to stale that output, through the git record and through declared
    inputs — never every output in the repository."""
    before = env_version(root)
    (root / "src").mkdir()
    (root / "src" / "fit.py").write_text("print('hi')\n")
    assert env_version(root) == before


def test_a_missing_lock_names_what_to_do(root: Path) -> None:
    (root / "uv.lock").unlink()
    with pytest.raises(ProjectError, match="no uv.lock"):
        env_version(root)


def test_a_missing_interpreter_pin_is_a_refusal(root: Path) -> None:
    (root / ".python-version").unlink()
    with pytest.raises(ProjectError, match=r"no \.python-version"):
        env_version(root)


# ---- code_version ----------------------------------------------------------


def test_code_version_follows_each_of_its_three_terms() -> None:
    recipe, decisions, env = "python fit.py {output}", {"method": "mcmc"}, "sha256:aa"
    original = code_version(recipe=recipe, decisions=decisions, env=env)

    assert code_version(recipe=recipe + " -v", decisions=decisions, env=env) != original
    assert code_version(recipe=recipe, decisions={"method": "nested"}, env=env) != original
    assert code_version(recipe=recipe, decisions=decisions, env="sha256:bb") != original


def test_decision_order_does_not_matter() -> None:
    """Decisions are a mapping, not a sequence — two spellings of the same
    choices are the same code."""
    a = code_version(recipe="r", decisions={"x": "1", "y": "2"}, env="e")
    b = code_version(recipe="r", decisions={"y": "2", "x": "1"}, env="e")
    assert a == b


def test_fields_cannot_shift_into_one_another() -> None:
    """Length framing, exercised. Concatenated raw, these two would feed
    the hash identical bytes and claim two different outputs were one."""
    a = code_version(recipe="ab", decisions={}, env="c")
    b = code_version(recipe="a", decisions={}, env="bc")
    assert a != b


def test_the_environment_reaches_every_output(root: Path) -> None:
    """The property that makes `env_version` worth computing: it sits
    inside `code_version`, so an environment edit stales everything."""
    before = code_version(recipe="r", decisions={}, env=env_version(root))
    (root / ".python-version").write_text("3.12.9\n")
    assert code_version(recipe="r", decisions={}, env=env_version(root)) != before


# ---- the lock scan ---------------------------------------------------------


def test_a_clean_lock_scans_clean(root: Path) -> None:
    assert scan_lock(root) == LockScan(refusals=(), sdist_built=(), non_default_groups=())


def test_a_path_dependency_is_refused(root: Path) -> None:
    """The lock records where it was, not what was in it: two syncs of one
    lock can install different code, and every hash here would agree they
    were identical."""
    _with_package(root, 'name = "sibling"', 'source = { path = "../sibling" }')
    scan = scan_lock(root)
    assert len(scan.refusals) == 1
    assert "sibling" in scan.refusals[0] and "path" in scan.refusals[0]


def test_an_editable_dependency_is_refused(root: Path) -> None:
    _with_package(root, 'name = "tool"', 'source = { editable = "../tool" }')
    assert "tool" in scan_lock(root).refusals[0]


def test_the_projects_own_package_is_not_refused(root: Path) -> None:
    """It *is* the project, and the repository already records its bytes."""
    _with_package(root, 'name = "demo"', 'source = { editable = "." }')
    assert scan_lock(root).refusals == ()


def test_the_own_package_exemption_survives_name_normalization(root: Path) -> None:
    """uv writes the PEP 503 name into the lock; `pyproject.toml` carries
    whatever the author wrote. Comparing them raw makes a packaged project
    fail to recognise itself, and the scan then refuses its own code."""
    (root / "pyproject.toml").write_text(_PYPROJECT.replace('name = "demo"', 'name = "My_Demo"'))
    _with_package(root, 'name = "my-demo"', 'source = { editable = "." }')

    assert scan_lock(root).refusals == ()


def test_a_registry_package_with_no_wheel_is_reported(root: Path) -> None:
    """Identity covers the sdist, not the build of it — reported, not
    refused, because building from source is legitimate."""
    _with_package(
        root,
        'name = "oldlib"',
        _REGISTRY,
        "",
        "[package.sdist]",
        'url = "https://example/oldlib.tar.gz"',
    )
    assert scan_lock(root).sdist_built == ("oldlib",)


def test_a_registry_package_with_wheels_is_not_reported(root: Path) -> None:
    _with_package(
        root,
        'name = "numpy"',
        _REGISTRY,
        "",
        "[package.sdist]",
        'url = "https://example/numpy.tar.gz"',
        "",
        "[[package.wheels]]",
        'url = "https://example/numpy.whl"',
    )
    assert scan_lock(root).sdist_built == ()


def test_groups_outside_the_default_set_are_advisory(root: Path) -> None:
    """They are installable states `env_version` does not distinguish."""
    (root / "pyproject.toml").write_text(
        _PYPROJECT + "\n[dependency-groups]\ndev = []\nplots = []\n"
    )
    assert scan_lock(root).non_default_groups == ("plots",)


def test_a_group_uv_installs_by_default_is_not_advisory(root: Path) -> None:
    (root / "pyproject.toml").write_text(_PYPROJECT + "\n[dependency-groups]\ndev = []\n")
    assert scan_lock(root).non_default_groups == ()
