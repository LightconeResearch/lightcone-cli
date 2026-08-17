"""Tests for the content-addressed image tag."""
from __future__ import annotations

import re
from pathlib import Path

from conftest import make_project

from lightcone.engine.environment import load_environment
from lightcone.engine.image.declaration import load_image_declaration
from lightcone.engine.image.definition import ImageDefinition
from lightcone.engine.image.identity import EnvInputs, compute_tag
from lightcone.engine.image.render import render


def _tag_of(project: Path) -> str:
    env = load_environment(project)
    decl = load_image_declaration(project)
    assert decl is not None
    defn = ImageDefinition.from_project(project, decl, env_version=env.env_version)
    return compute_tag(render(defn), EnvInputs.read(project))


class TestTag:
    def test_format(self, containerized_project: Path) -> None:
        assert re.fullmatch(r"lc-env-[0-9a-f]{16}", _tag_of(containerized_project))

    def test_deterministic(self, tmp_path: Path) -> None:
        a = _tag_of(make_project(tmp_path / "a", containerized=True))
        b = _tag_of(make_project(tmp_path / "b", containerized=True))
        assert a == b

    def test_moves_with_package_add(self, tmp_path: Path) -> None:
        a = _tag_of(make_project(tmp_path / "a", containerized=True))
        b_proj = make_project(tmp_path / "b")
        (b_proj / "pyproject.toml").write_text(
            (b_proj / "pyproject.toml").read_text()
            + '\n[tool.lightcone.image]\nsystem-packages = ["bc"]\n'
        )
        assert _tag_of(b_proj) != a

    def test_moves_with_lock_byte(self, containerized_project: Path) -> None:
        before = _tag_of(containerized_project)
        (containerized_project / "uv.lock").write_text(
            (containerized_project / "uv.lock").read_text() + "# x\n"
        )
        assert _tag_of(containerized_project) != before

    def test_moves_with_pyproject_byte(self, containerized_project: Path) -> None:
        before = _tag_of(containerized_project)
        p = containerized_project / "pyproject.toml"
        p.write_text(p.read_text() + "# comment\n")
        assert _tag_of(containerized_project) != before

    def test_moves_with_python_pin(self, containerized_project: Path) -> None:
        before = _tag_of(containerized_project)
        (containerized_project / ".python-version").write_text("3.12.11\n")
        assert _tag_of(containerized_project) != before

    def test_moves_with_extra_stage(self, containerized_project: Path) -> None:
        before = _tag_of(containerized_project)
        (containerized_project / "Containerfile.extra").write_text("RUN echo x\n")
        assert _tag_of(containerized_project) != before

    def test_does_not_move_with_project_code(
        self, containerized_project: Path
    ) -> None:
        """G5: code edits change no input to the tag."""
        before = _tag_of(containerized_project)
        (containerized_project / "analysis.py").write_text("x = 1\n")
        (containerized_project / "astra.yaml").write_text("outputs: []\n")
        assert _tag_of(containerized_project) == before
