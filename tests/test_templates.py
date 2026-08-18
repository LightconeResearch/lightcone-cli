"""Tests for `lightcone.engine.templates` — the scaffold's file templates."""

from __future__ import annotations

import sys

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


# ---- the uv project -------------------------------------------------------


def test_pyproject_renders_every_placeholder() -> None:
    rendered = templates.pyproject(name="my-analysis")
    assert "$" not in rendered
    assert 'name = "my-analysis"' in rendered
    assert f'requires-python = "{templates.requires_python()}"' in rendered
    assert templates.lightcone_requirement() in rendered
    # Virtual by design: containerized mode builds `--no-install-project`,
    # so a packaged project's own import would fail inside its image.
    assert "[build-system]" not in rendered


def test_python_version_pins_the_running_interpreter() -> None:
    """No engine constant — a new project pins the python the researcher
    actually has, rather than one lc would have to download."""
    v = sys.version_info
    assert templates.python_version() == f"{v.major}.{v.minor}.{v.micro}\n"


def test_requires_python_comes_from_our_own_metadata() -> None:
    """Declaring the engine's own bound rather than inventing a second one."""
    from importlib.metadata import metadata

    assert templates.requires_python() == metadata("lightcone-cli")["Requires-Python"]


def test_the_ambient_pin_always_satisfies_the_declared_floor() -> None:
    """Not a coincidence: lc can only run on an interpreter meeting its own
    Requires-Python, so the two can never conflict."""
    from packaging.specifiers import SpecifierSet

    assert templates.python_version().strip() in SpecifierSet(templates.requires_python())


def test_requires_python_falls_back_to_the_running_minor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A metadata-less install still gets a bound the ambient pin meets."""
    import importlib.metadata

    def _missing(_name: str) -> object:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "metadata", _missing)
    v = sys.version_info
    assert templates.requires_python() == f">={v.major}.{v.minor}"


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


# ---- .gitignore -----------------------------------------------------------


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


def test_gitignore_header_is_the_templates_own_first_line() -> None:
    """Derived, not duplicated: rewording the template's comment can't leave
    the repair appending a second header."""
    assert templates.gitignore().startswith(templates.gitignore_header() + "\n")


def test_repair_is_none_when_nothing_is_missing() -> None:
    assert templates.missing_gitignore_entries(templates.gitignore()) == []
    assert templates.gitignore_repair(templates.gitignore()) is None


def test_repair_of_an_empty_file_is_just_the_template() -> None:
    assert templates.gitignore_repair("") == templates.gitignore()


def test_repair_appends_only_what_is_missing_behind_the_header() -> None:
    repaired = templates.gitignore_repair("mine.txt\n.venv/\n")
    assert repaired is not None
    assert repaired.startswith("mine.txt\n.venv/\n\n")
    assert repaired.count(".venv/") == 1
    assert templates.gitignore_header() in repaired
    assert templates.missing_gitignore_entries(repaired) == []


def test_repair_does_not_add_a_second_header() -> None:
    """The case a marker check would have skipped: header present, entries
    missing."""
    header = templates.gitignore_header()
    repaired = templates.gitignore_repair(f"{header}\n.venv/\n")
    assert repaired is not None
    assert repaired.count(header) == 1
    assert templates.missing_gitignore_entries(repaired) == []


def test_a_pattern_inside_a_comment_does_not_count_as_present() -> None:
    assert "results/*" in templates.missing_gitignore_entries("# results/*\n")


# ---- the rest -------------------------------------------------------------


def test_index_md_renders_the_title_and_keeps_myst_roles() -> None:
    """The report body legitimately contains braces (`{astra}` roles) —
    which is why substitution is `string.Template`, not `str.format`."""
    rendered = templates.index_md(title="My Analysis")
    assert rendered.startswith("# My Analysis\n")
    assert "{astra}`decisions.example_method`" in rendered
    assert "{astra:value}`outputs.main_result`" in rendered
    assert "$" not in rendered


def test_results_readme_explains_the_output_layout() -> None:
    """`results/` is git-ignored and starts empty, so the README is the
    only thing a clone shows for it."""
    assert "results/<universe>/<output_id>/" in templates.results_readme()
