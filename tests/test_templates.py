"""Tests for `lightcone.engine.templates` — the scaffold's file templates."""

from __future__ import annotations

import pytest

from lightcone.engine import templates


def test_every_declared_template_is_shipped() -> None:
    """The templates are package *data*, so a packaging slip would only
    show up at `lc init` time. Read them all here instead."""
    for name in templates.TEMPLATE_NAMES:
        assert templates.read(name).strip(), f"{name} is empty"


def test_unknown_template_fails_loudly() -> None:
    with pytest.raises(KeyError, match="unknown template"):
        templates.read("nope.tmpl")


def test_pyproject_renders_every_placeholder() -> None:
    from lightcone.engine.constants import DEFAULT_PYTHON_FLOOR, MIN_UV_VERSION

    rendered = templates.pyproject(name="my-analysis")
    assert "$" not in rendered
    assert 'name = "my-analysis"' in rendered
    assert f'requires-python = ">={DEFAULT_PYTHON_FLOOR}"' in rendered
    assert f'required-version = ">={MIN_UV_VERSION}"' in rendered
    assert templates.lightcone_requirement() in rendered


def test_pyproject_is_virtual() -> None:
    """Containerized mode builds `--no-install-project`, so a packaged
    project's own import would fail inside its image (spec §1)."""
    assert "[build-system]" not in templates.pyproject(name="x")


def test_lightcone_requirement_pins_the_running_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driver and project stay in lockstep — except for dev builds, whose
    versions aren't published, so pinning one would make the project's lock
    unsolvable."""
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.2.3")
    assert templates.lightcone_requirement() == "lightcone-cli==1.2.3"

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.2.3.dev4+gabc")
    assert templates.lightcone_requirement() == "lightcone-cli"

    def _missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    assert templates.lightcone_requirement() == "lightcone-cli"


def test_index_md_renders_the_title_and_keeps_myst_roles() -> None:
    """The report body legitimately contains braces (`{astra}` roles) —
    which is why substitution is `string.Template`, not `str.format`."""
    rendered = templates.index_md(title="My Analysis")
    assert rendered.startswith("# My Analysis\n")
    assert "{astra}`decisions.example_method`" in rendered
    assert "{astra:value}`outputs.main_result`" in rendered
    assert "$" not in rendered


def test_gitignore_entries_are_the_patterns_only() -> None:
    """Convergence compares patterns, so comments and blanks must not leak
    into the set — a comment treated as an entry would be re-appended
    forever."""
    entries = templates.gitignore_entries()
    assert not any(e.startswith("#") or not e.strip() for e in entries)
    assert ".venv/" in entries
    assert "results/*" in entries
    assert "!results/README.md" in entries


def test_gitignore_entries_keep_template_order() -> None:
    """`!results/README.md` only works after the `results/*` it negates."""
    entries = templates.gitignore_entries()
    assert entries.index("results/*") < entries.index("!results/README.md")


def test_missing_entries_ignores_comments_in_the_target() -> None:
    """A pattern that appears only inside a comment is not present."""
    assert "results/*" in templates.missing_gitignore_entries("# results/*\n")
    assert templates.missing_gitignore_entries(templates.gitignore()) == []


def test_results_readme_explains_the_output_layout() -> None:
    """`results/` is git-ignored and starts empty, so the README is the
    only thing a clone shows for it."""
    assert "results/<universe>/<output_id>/" in templates.results_readme()
