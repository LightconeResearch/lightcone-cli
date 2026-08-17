"""Shared fixtures for the lightcone-cli test suite."""
from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--regen-goldens",
        action="store_true",
        default=False,
        help="Rewrite golden fixture files instead of asserting against them.",
    )

#: Deterministic environment-file contents shared by fixture projects —
#: identity tests pin hashes over these exact bytes.
PYPROJECT_MIN = """\
[project]
name = "fixture-proj"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = []
"""

UV_LOCK_MIN = """\
version = 1
revision = 3
requires-python = ">=3.12"

[[package]]
name = "fixture-proj"
version = "0.0.0"
source = { virtual = "." }
"""

PYTHON_VERSION_MIN = "3.12.12\n"

ASTRA_YAML_MIN = """\
outputs:
  - id: result
    type: metric
    recipe:
      command: echo hi > {output}/data.txt
"""

IMAGE_TABLE = """
[tool.lightcone.image]
system-packages = ["r-base-core", "libhdf5-dev"]
"""


def make_project(
    root: Path,
    *,
    containerized: bool = False,
    extra_pyproject: str = "",
    astra_yaml: str = ASTRA_YAML_MIN,
) -> Path:
    """Write a minimal, deterministic uv project scaffold under *root*."""
    root.mkdir(parents=True, exist_ok=True)
    pyproject = PYPROJECT_MIN
    if containerized:
        pyproject += IMAGE_TABLE
    pyproject += extra_pyproject
    (root / "pyproject.toml").write_text(pyproject)
    (root / "uv.lock").write_text(UV_LOCK_MIN)
    (root / ".python-version").write_text(PYTHON_VERSION_MIN)
    (root / "astra.yaml").write_text(astra_yaml)
    return root


@pytest.fixture
def direct_project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "proj")


@pytest.fixture
def containerized_project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "proj", containerized=True)
