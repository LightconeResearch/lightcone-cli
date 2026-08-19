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
    inputs: [catalog]
    decisions: [method]
    recipe:
      command: python src/fit.py {inputs.catalog} --method {decisions.method} {output}

  - id: report
    type: report
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


def test_an_output_addresses_its_own_directory(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks[("baseline", "fit")]
    assert task.output_dir == tmp_path / "results" / "baseline" / "fit"
    assert "results/baseline/fit" in task.recipe


def test_a_declared_input_resolves_to_its_source(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks[("baseline", "fit")]
    assert task.inputs == {"catalog": tmp_path / "data" / "catalog.fits"}
    assert task.produced_by == {}
    assert "data/catalog.fits" in task.recipe


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


def test_a_sub_analysis_output_is_addressed_flat_and_qualified(tmp_path: Path) -> None:
    """One addressing scheme and one place to look, whatever shape the
    spec has."""
    graph = _build(_tree(tmp_path))
    assert sorted(graph.tasks) == [("baseline", "hod.mass_function"), ("baseline", "summary")]
    task = graph.tasks[("baseline", "hod.mass_function")]
    assert task.output_dir == tmp_path / "results" / "baseline" / "hod.mass_function"
















# ---- rendering a recipe ----------------------------------------------------








