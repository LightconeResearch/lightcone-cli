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
    # Virtual by design: containerized mode builds `--no-install-project`,
    # so a packaged project's own import would fail inside its image.
    assert "[build-system]" not in rendered


def test_pyproject_does_not_depend_on_the_engine() -> None:
    """The engine is the host's uv tool, not a project dependency — a
    scaffolded lock carries only what the analysis itself imports."""
    assert "lightcone-cli" not in templates.pyproject(name="my-analysis")


def test_python_version_pins_the_running_interpreter() -> None:
    """No engine constant — a new project pins the python the researcher
    actually has, rather than one lc would have to download."""
    v = sys.version_info
    assert templates.python_version() == f"{v.major}.{v.minor}.{v.micro}\n"


def test_requires_python_is_the_running_minor() -> None:
    """The bound and the exact pin come from one interpreter, so the
    scaffolded `.python-version` always satisfies `requires-python`."""
    v = sys.version_info
    assert templates.requires_python() == f">={v.major}.{v.minor}"


# ---- .gitignore -----------------------------------------------------------


def test_gitignore_entries_are_the_patterns_only() -> None:
    """Convergence compares patterns, so comments and blanks must not leak
    into the set — a comment treated as an entry would be re-appended
    forever."""
    entries = templates.entries("gitignore.tmpl")
    assert not any(e.startswith("#") or not e.strip() for e in entries)
    assert ".venv/" in entries


def test_the_template_does_not_ignore_what_the_repository_versions() -> None:
    """`results/` and `data/` are committed, so an ignore rule covering
    either would make every materialized output silently uncommittable —
    `git add` skips ignored paths without a word."""
    entries = templates.entries("gitignore.tmpl")
    assert not any(e.lstrip("!").startswith(("results", "data")) for e in entries)


def test_gitignore_header_is_the_templates_own_first_line() -> None:
    """Derived, not duplicated: rewording the template's comment can't leave
    the repair appending a second header."""
    assert templates.read("gitignore.tmpl").startswith(templates.header("gitignore.tmpl") + "\n")


def test_repair_is_none_when_nothing_is_missing() -> None:
    assert templates.missing("gitignore.tmpl", templates.read("gitignore.tmpl")) == []
    assert templates.gitignore_repair(templates.read("gitignore.tmpl")) is None


def test_repair_of_an_empty_file_is_just_the_template() -> None:
    assert templates.gitignore_repair("") == templates.read("gitignore.tmpl")


def test_repair_appends_only_what_is_missing_behind_the_header() -> None:
    repaired = templates.gitignore_repair("mine.txt\n.venv/\n")
    assert repaired is not None
    assert repaired.startswith("mine.txt\n.venv/\n\n")
    assert repaired.count(".venv/") == 1
    assert templates.header("gitignore.tmpl") in repaired
    assert templates.missing("gitignore.tmpl", repaired) == []


def test_repair_does_not_add_a_second_header() -> None:
    """The case a marker check would have skipped: header present, entries
    missing."""
    header = templates.header("gitignore.tmpl")
    repaired = templates.gitignore_repair(f"{header}\n.venv/\n")
    assert repaired is not None
    assert repaired.count(header) == 1
    assert templates.missing("gitignore.tmpl", repaired) == []


def test_a_pattern_inside_a_comment_does_not_count_as_present() -> None:
    assert ".venv/" in templates.missing("gitignore.tmpl", "# .venv/\n")


# ---- .gitattributes -------------------------------------------------------


def test_gitattributes_routes_content_to_the_annex_and_everything_else_to_git() -> None:
    """The whole storage policy. The default line is the load-bearing one:
    `git annex add` annexes whatever it is handed, so without it the
    documented save turns analysis code into read-only symlinks."""
    entries = templates.entries("gitattributes.tmpl")
    assert entries[0] == "* annex.largefiles=nothing"
    assert "results/** annex.largefiles=anything" in entries
    assert "data/** annex.largefiles=anything" in entries
    assert "results/**/.*.manifest.json annex.largefiles=nothing" in entries


def test_gitattributes_exceptions_come_after_the_default() -> None:
    """Last matching line wins, so a default written below the exceptions
    would silently take the annex back out of the picture."""
    entries = templates.entries("gitattributes.tmpl")
    assert entries.index("* annex.largefiles=nothing") < entries.index(
        "results/** annex.largefiles=anything"
    )


def test_gitattributes_repair_appends_what_a_users_own_file_lacks() -> None:
    """More is at stake here than in `.gitignore`: a `.gitattributes` the
    user wrote first would leave result bytes routed into git."""
    repaired = templates.gitattributes_repair("*.fits filter=lfs\n")
    assert repaired is not None
    assert repaired.startswith("*.fits filter=lfs\n\n")
    assert templates.missing("gitattributes.tmpl", repaired) == []
    assert templates.gitattributes_repair(templates.read("gitattributes.tmpl")) is None


def test_a_file_the_repair_can_fix_reports_no_disorder() -> None:
    """The ordinary case: whatever the user already had, the managed lines
    are appended in template order and the result means what it should."""
    assert templates.gitattributes_disorder("") == ""
    assert templates.gitattributes_disorder("*.fits filter=lfs\n") == ""
    assert templates.gitattributes_disorder(templates.read("gitattributes.tmpl")) == ""


def test_an_opt_out_the_defaults_would_land_below_is_named() -> None:
    """The trap append-only cannot escape. A file that already opts
    `results/` into the annex gets `* annex.largefiles=nothing` appended
    *after* it, and last-match-wins then routes every result into git as a
    plain blob — while convergence reports the file repaired."""
    misplaced = templates.gitattributes_disorder("results/** annex.largefiles=anything\n")
    assert misplaced == "* annex.largefiles=nothing"


def test_a_gitattributes_written_by_an_earlier_template_still_converges() -> None:
    """Repair only appends, at end of file, carrying each line's template
    rank — so a managed line added anywhere but last strands every file
    written before it, permanently, since append-only repair cannot
    reorder. This is such a file, and it must converge."""
    earlier = (
        "* annex.largefiles=nothing\n"
        "* filter=annex\n"
        "results/** annex.largefiles=anything\n"
        "data/** annex.largefiles=anything\n"
        ".datalad/environments/*/image annex.largefiles=anything\n"
    )
    assert templates.gitattributes_disorder(earlier) == ""


def test_a_hand_written_file_already_in_the_right_order_needs_nothing() -> None:
    """Judged on meaning, not on who wrote it — and only lines setting the
    *same* attribute can be out of order with each other, so the
    `* filter=annex` the repair appends below these two is not disorder."""
    ordered = "* annex.largefiles=nothing\nresults/** annex.largefiles=anything\n"
    assert templates.gitattributes_disorder(ordered) == ""


# ---- .datalad/config ------------------------------------------------------


def test_datalad_config_carries_the_dataset_id() -> None:
    """The one thing a git + git-annex repository lacks to *be* a DataLad
    dataset, in the git-config syntax datalad reads it from."""
    text = templates.datalad_config(dataset_id="4b7b5c1e-0000-4000-8000-000000000000")
    assert '[datalad "dataset"]' in text
    assert "id = 4b7b5c1e-0000-4000-8000-000000000000" in text


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
    """`results/` starts empty and git carries no empty directories, so the
    README is the only thing a clone shows for it."""
    assert "results/<universe>/<output_id>.<format>" in templates.read("results-README.md.tmpl")


def test_data_readme_explains_where_declared_inputs_go() -> None:
    """It says what the directory is for and nothing about how to fill it:
    manipulating git-annex by hand is not something lc asks of anyone."""
    text = templates.read("data-README.md.tmpl")
    assert "data/catalog.fits" in text
    assert "git annex" not in text
