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

from lightcone.engine.identity import LockScan, definition_version, env_version, scan_lock
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


def test_fields_cannot_shift_into_one_another(root: Path) -> None:
    """Length framing, exercised where it is load-bearing. `env_version`
    concatenates two files' *raw* bytes, so without framing a byte moved
    from the end of one to the start of the other feeds the hash the same
    input and claims two different environments are one.
    """
    (root / "uv.lock").write_bytes(b"lock\n3.13")
    (root / ".python-version").write_bytes(b".1\n")
    shifted_left = env_version(root)

    (root / "uv.lock").write_bytes(b"lock\n")
    (root / ".python-version").write_bytes(b"3.13.1\n")
    assert env_version(root) != shifted_left


# ---- definition_version ----------------------------------------------------


def test_definition_version_follows_both_its_terms() -> None:
    recipe, decisions = "python fit.py {output}", {"method": "mcmc"}
    original = definition_version(recipe=recipe, decisions=decisions)

    assert definition_version(recipe=recipe + " -v", decisions=decisions) != original
    assert definition_version(recipe=recipe, decisions={"method": "nested"}) != original


def test_decision_order_does_not_matter() -> None:
    """Decisions are a mapping, not a sequence — two spellings of the same
    choices define the same output."""
    a = definition_version(recipe="r", decisions={"x": "1", "y": "2"})
    b = definition_version(recipe="r", decisions={"y": "2", "x": "1"})
    assert a == b


def test_the_environment_is_not_part_of_what_an_output_is(root: Path) -> None:
    """The load-bearing separation. `env_version` is recorded beside an
    output and compared to say it is *behind*; folding it in here would
    make one added dependency remake a project's every result."""
    before = definition_version(recipe="r", decisions={})
    (root / ".python-version").write_text("3.12.9\n")
    assert env_version(root)  # the environment did move
    assert definition_version(recipe="r", decisions={}) == before


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
