"""Tests for `lightcone.engine.plan` — the spec, read as a graph.

Two things are being pinned here. That the graph says what the spec meant
— which outputs exist, what each depends on, and what its recipe came out
as — and that everything ambiguous is an error rather than a guess: a
placeholder nothing declares, a target nothing matches, a nesting depth
the addressing scheme cannot express.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lightcone.engine import plan
from lightcone.engine.plan import Graph
from lightcone.engine.project import ProjectError

_SPEC = """
version: "0.0.13"
name: demo

inputs:
  - id: catalog
    type: data
    source: data/catalog.fits

outputs:
  - id: fit
    type: metric
    format: json
    inputs: [catalog]
    decisions: [method]
    recipe:
      command: python src/fit.py {inputs.catalog} --method {decisions.method} {output}

  - id: report
    type: report
    format: md
    inputs: [fit]
    recipe:
      command: python src/report.py {inputs.fit} {output}

  - id: reexport
    type: metric

decisions:
  method:
    label: Method
    default: mcmc
    options:
      mcmc: {label: MCMC}
      nested: {label: Nested}
"""


def _project(root: Path, spec: str = _SPEC, **universes: str) -> Path:
    (root / "astra.yaml").write_text(textwrap.dedent(spec))
    (root / "universes").mkdir(exist_ok=True)
    declared = universes or {"baseline": "id: baseline\ndecisions:\n  method: mcmc\n"}
    for name, text in declared.items():
        (root / "universes" / f"{name}.yaml").write_text(textwrap.dedent(text))
    return root


def _build(root: Path) -> Graph:
    return plan.build(root)


# ---- what the graph contains -----------------------------------------------


def test_one_task_per_universe_and_output_with_a_recipe(tmp_path: Path) -> None:
    """A re-export declares no recipe, so it produces no bytes and there is
    nothing to schedule for it."""
    graph = _build(_project(tmp_path))
    assert sorted(graph.tasks) == [("baseline", "fit"), ("baseline", "report")]


def test_every_universe_gets_its_own_task(tmp_path: Path) -> None:
    graph = _build(
        _project(
            tmp_path,
            baseline="id: baseline\ndecisions:\n  method: mcmc\n",
            alternative="id: alternative\ndecisions:\n  method: nested\n",
        )
    )
    assert {u for u, _ in graph.tasks} == {"baseline", "alternative"}


def test_a_decision_that_differs_gives_a_different_definition_version(tmp_path: Path) -> None:
    """The whole point of a universe: the same output, made differently, is
    a different thing."""
    graph = _build(
        _project(
            tmp_path,
            baseline="id: baseline\ndecisions:\n  method: mcmc\n",
            alternative="id: alternative\ndecisions:\n  method: nested\n",
        )
    )
    assert (
        graph.tasks[("baseline", "fit")].definition_version
        != graph.tasks[("alternative", "fit")].definition_version
    )


def test_an_output_that_ignores_a_decision_is_not_moved_by_it(tmp_path: Path) -> None:
    """`definition_version` hashes the decisions the output *declares*, so an
    unrelated choice does not stale it."""
    a = _build(_project(tmp_path, baseline="id: baseline\ndecisions:\n  method: mcmc\n"))
    b = _build(_project(tmp_path, baseline="id: baseline\ndecisions:\n  method: nested\n"))
    key = ("baseline", "report")
    assert a.tasks[key].definition_version == b.tasks[key].definition_version


def test_an_output_addresses_its_own_file(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks[("baseline", "fit")]
    assert task.output_path == tmp_path / "results" / "baseline" / "fit.json"
    assert task.manifest_path == tmp_path / "results" / "baseline" / ".fit.manifest.json"
    assert "results/baseline/fit.json" in task.recipe


def test_a_declared_input_resolves_to_its_source(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks[("baseline", "fit")]
    assert task.inputs == {"catalog": tmp_path / "data" / "catalog.fits"}
    assert task.produced_by == {}
    assert "data/catalog.fits" in task.recipe


def test_a_declared_input_outside_the_project_keeps_its_absolute_path(
    tmp_path: Path,
) -> None:
    """A path in a recipe *is* the path on disk, and one outside the tree
    has no project-relative spelling to fall back on. It used to be
    rendered by `relative_to`, which raised a bare ValueError."""
    outside = tmp_path.parent / "shared" / "catalog.fits"
    root = _project(tmp_path, _SPEC.replace("source: data/catalog.fits", f"source: {outside}"))

    task = _build(root).tasks[("baseline", "fit")]
    assert task.inputs == {"catalog": outside}
    assert str(outside) in task.recipe


def test_an_input_another_output_produces_becomes_an_edge(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks[("baseline", "report")]
    assert task.produced_by == {"fit": ("baseline", "fit")}
    assert task.depends_on == (("baseline", "fit"),)
    assert "results/baseline/fit" in task.recipe


def test_an_input_nothing_provides_is_an_error(tmp_path: Path) -> None:
    """Caught by ASTRA rather than by us: resolution answers what a valid
    spec means, so `build` asks whether it is one first — and the error
    names the line at fault instead of the run that tripped over it."""
    spec = _SPEC.replace("inputs: [catalog]", "inputs: [missing]").replace(
        "{inputs.catalog}", "{inputs.missing}"
    )
    with pytest.raises(ProjectError, match="does not validate"):
        _build(_project(tmp_path, spec))


def test_a_spec_astra_rejects_never_reaches_a_recipe(tmp_path: Path) -> None:
    """The gate exists because the resolver assumes validity: without it an
    invalid spec surfaces as a missing decision or an unresolvable input,
    blaming the run for a fault in the file."""
    spec = _SPEC.replace('name: demo', "")
    with pytest.raises(ProjectError, match="MISSING_ROOT_FIELD"):
        _build(_project(tmp_path, spec))


# ---- ordering, closure, targets --------------------------------------------


def test_order_puts_dependencies_first(tmp_path: Path) -> None:
    order = _build(_project(tmp_path)).order()
    assert order.index(("baseline", "fit")) < order.index(("baseline", "report"))


def test_a_cycle_is_a_clean_error(tmp_path: Path) -> None:
    spec = _SPEC.replace("  - id: reexport\n    type: metric\n", "")
    spec = spec.replace("    inputs: [catalog]", "    inputs: [report]")
    spec = spec.replace("{inputs.catalog}", "{inputs.report}")
    with pytest.raises(ProjectError, match="cycle"):
        _build(_project(tmp_path, spec)).order()


def test_asking_for_an_output_asks_for_what_it_is_made_of(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path))
    closure = graph.closure(graph.resolve(["report"]))
    assert sorted(closure.tasks) == [("baseline", "fit"), ("baseline", "report")]


def test_a_bare_target_means_every_universe(tmp_path: Path) -> None:
    graph = _build(
        _project(
            tmp_path,
            baseline="id: baseline\ndecisions:\n  method: mcmc\n",
            alternative="id: alternative\ndecisions:\n  method: nested\n",
        )
    )
    assert sorted(graph.resolve(["fit"])) == [("alternative", "fit"), ("baseline", "fit")]


def test_a_qualified_target_means_exactly_one(tmp_path: Path) -> None:
    graph = _build(
        _project(
            tmp_path,
            baseline="id: baseline\ndecisions:\n  method: mcmc\n",
            alternative="id: alternative\ndecisions:\n  method: nested\n",
        )
    )
    assert graph.resolve(["baseline/fit"]) == [("baseline", "fit")]


def test_an_unknown_target_is_an_error_not_an_empty_run(tmp_path: Path) -> None:
    """Quietly making nothing is the least useful thing a build tool can
    do, so the message lists what there was."""
    graph = _build(_project(tmp_path))
    with pytest.raises(ProjectError, match="no output matches `typo`.*baseline/fit"):
        graph.resolve(["typo"])


# ---- refusals --------------------------------------------------------------


def test_two_universes_cannot_claim_one_id(tmp_path: Path) -> None:
    """They would materialize into one directory, and the second simply
    replaced the first — so a universe went missing with nothing said. The
    natural way to reach it is copying a universe file and forgetting to
    change the id inside."""
    root = _project(
        tmp_path,
        baseline="id: baseline\ndecisions:\n  method: mcmc\n",
        copy="id: baseline\ndecisions:\n  method: nested\n",
    )
    with pytest.raises(ProjectError, match="both declare the universe `baseline`"):
        _build(root)


def test_universes_are_told_apart_by_their_id_not_their_filename(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        baseline="id: baseline\ndecisions:\n  method: mcmc\n",
        alternative="id: alternative\ndecisions:\n  method: nested\n",
    )
    assert {u for u, _ in _build(root).tasks} == {"baseline", "alternative"}


def test_no_spec_is_a_clean_error(tmp_path: Path) -> None:
    (tmp_path / "universes").mkdir()
    with pytest.raises(ProjectError, match="no astra.yaml"):
        _build(tmp_path)


def test_no_universe_is_a_clean_error(tmp_path: Path) -> None:
    (tmp_path / "astra.yaml").write_text(textwrap.dedent(_SPEC))
    with pytest.raises(ProjectError, match="declares no universe"):
        _build(tmp_path)


# ---- sub-analyses ----------------------------------------------------------

_PARENT = """
version: "0.0.13"
name: parent

inputs:
  - id: catalog
    type: data
    source: data/catalog.fits

outputs:
  - id: mass_function
    from: hod.mass_function

  - id: summary
    type: report
    format: md
    inputs: [mass_function]
    recipe:
      command: python summarize.py {inputs.mass_function} {output}

analyses:
  hod:
    path: ./analyses/hod
"""

_SUB = """
version: "0.0.13"
name: hod

inputs:
  - id: catalog
    type: data
    from: ../catalog

outputs:
  - id: mass_function
    type: metric
    format: npz
    inputs: [catalog]
    decisions: [binning]
    recipe:
      command: python hod.py {inputs.catalog} --bins {decisions.binning} {output}

decisions:
  binning:
    label: Binning
    default: log
    options:
      log: {label: log}
      linear: {label: linear}
"""


def _tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "astra.yaml").write_text(textwrap.dedent(_PARENT))
    (root / "universes").mkdir()
    # The sub-analysis's universe is named explicitly: ASTRA has no
    # implicit "same id" fallback, and lc no longer invents one.
    (root / "universes" / "baseline.yaml").write_text(
        "id: baseline\ndecisions: {}\nanalyses:\n  hod:\n    universe: baseline\n"
    )
    sub = root / "analyses" / "hod"
    (sub / "universes").mkdir(parents=True)
    (sub / "astra.yaml").write_text(textwrap.dedent(_SUB))
    (sub / "universes" / "baseline.yaml").write_text("id: baseline\ndecisions:\n  binning: log\n")
    return root


def test_a_sub_analysis_is_addressed_by_its_qualified_id(tmp_path: Path) -> None:
    """One addressing scheme whatever shape the spec has: only the path
    nests, never the key."""
    graph = _build(_tree(tmp_path))
    assert sorted(graph.tasks) == [("baseline", "hod.mass_function"), ("baseline", "summary")]


def test_an_external_sub_analysis_keeps_results_beside_its_own_spec(tmp_path: Path) -> None:
    """A sub-analysis with a `path:` is a self-similar analysis — its own
    astra.yaml, its own universes, and so its own results tree."""
    root = _tree(tmp_path)
    task = _build(root).tasks[("baseline", "hod.mass_function")]
    assert task.output_path == root / "analyses/hod/results/baseline/mass_function.npz"
    assert task.manifest_path == (
        root / "analyses/hod/results/baseline/.mass_function.manifest.json"
    )


def test_a_consumer_is_handed_the_upstream_file_not_its_directory(tmp_path: Path) -> None:
    """The filename contract the spec now carries: a downstream recipe is
    given the actual file, so nothing has to know what is inside a
    directory it was handed."""
    root = _tree(tmp_path)
    recipe = _build(root).tasks[("baseline", "summary")].recipe
    assert "analyses/hod/results/baseline/mass_function.npz" in recipe


def test_a_re_export_resolves_to_what_actually_makes_the_bytes(tmp_path: Path) -> None:
    """`summary` names the parent's re-export, which carries no recipe of
    its own — so its input has to be the sub-analysis's file."""
    root = _tree(tmp_path)
    task = _build(root).tasks[("baseline", "summary")]
    assert task.produced_by == {"mass_function": ("baseline", "hod.mass_function")}
    assert task.inputs["mass_function"] == (
        root / "analyses/hod/results/baseline/mass_function.npz"
    )


def test_a_sub_analysis_reaching_outside_the_project_is_refused(tmp_path: Path) -> None:
    """Its results would land outside the repository, where nothing could
    version or fetch them. The sub-analysis is real and resolves — only
    where it sits is wrong."""
    root = _tree(tmp_path / "project")
    (root / "astra.yaml").write_text(
        textwrap.dedent(_PARENT).replace("path: ./analyses/hod", "path: ../hod")
    )
    outside = tmp_path / "hod"
    (outside / "universes").mkdir(parents=True)
    (outside / "astra.yaml").write_text(textwrap.dedent(_SUB))
    (outside / "universes" / "baseline.yaml").write_text(
        "id: baseline\ndecisions:\n  binning: log\n"
    )
    with pytest.raises(ProjectError, match="outside the project"):
        _build(root)


def test_a_sub_analysis_universe_that_is_not_there_is_refused(tmp_path: Path) -> None:
    """astra logs a warning and settles the sub-analysis from the parent's
    decisions instead, so without this lc would file an artifact under a
    universe it never loaded."""
    root = _tree(tmp_path)
    (root / "universes" / "baseline.yaml").write_text(
        "id: baseline\ndecisions: {}\nanalyses:\n  hod:\n    universe: absent\n"
    )
    with pytest.raises(ProjectError, match="absent"):
        _build(root)
















def test_an_output_without_a_format_is_refused_by_name(tmp_path: Path) -> None:
    """lc names the file from it, so there is nowhere to write the output.
    Every offender at once: a spec is fixed in one pass, not one run per
    missing key."""
    root = _project(tmp_path)
    (root / "astra.yaml").write_text(
        textwrap.dedent(_SPEC).replace("    format: json\n", "").replace("    format: md\n", "")
    )
    with pytest.raises(ProjectError, match="format") as raised:
        _build(root)
    assert "fit" in str(raised.value) and "report" in str(raised.value)


def test_two_tasks_that_would_write_one_file_are_refused(tmp_path: Path) -> None:
    """An external sub-analysis is filed under its own universe, so two
    parent universes selecting the same one resolve to the same path while
    feeding it different inputs. The second would overwrite the first."""
    root = _tree(tmp_path / "project")
    (root / "universes" / "robust.yaml").write_text(
        "id: robust\ndecisions: {}\nanalyses:\n  hod:\n    universe: baseline\n"
    )
    with pytest.raises(ProjectError, match="both materialize to"):
        _build(root)


# ---- rendering a recipe ----------------------------------------------------








