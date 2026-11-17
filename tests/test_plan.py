"""Tests for `lightcone.engine.plan` — the analysis, read as a graph.

What the analysis *means* is ASTRA's and is tested there. What is pinned
here is what lc adds: that each planned result becomes one task, keyed by
ASTRA's id for it, with the file each entry reads and the options it is
made with, beside what ASTRA's checker found of it; and that everything lc
cannot build, or a target that matches nothing, is an error rather than a
guess.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest
from astra import api

from lightcone.engine import plan
from lightcone.engine.plan import Graph
from lightcone.engine.project import ProjectError

_SPEC = """
spec_version: "0.1.0"
label: demo

inputs:
  catalog: {path: data/catalog.fits}

decisions:
  method:
    options:
      mcmc: {}
      nested: {}

outputs:
  fit:
    type: metric
    format: json
    inputs: [catalog]
    decisions: [method]
    recipe:
      command: python src/fit.py {inputs.catalog} --method {decisions.method} {output}

  report:
    type: report
    format: md
    inputs: [fit]
    recipe:
      command: python src/report.py {inputs.fit} {output}

  summary:
    type: table
    format: tar.gz
    inputs: [fit#baseline]
    recipe:
      command: python src/summary.py {inputs} {output}

universes:
  baseline: {decisions: {method: mcmc}}
"""

#: A second universe, selecting the other option.
_TWO = _SPEC + "  alternative: {decisions: {method: nested}}\n"


def _project(root: Path, spec: str = _SPEC) -> Path:
    """Write the analysis into a repository, its input tracked: ASTRA reads
    an analysis in a git working tree, and an input git does not track
    is no input."""
    (root / "data").mkdir(exist_ok=True)
    (root / "astra.yaml").write_text(textwrap.dedent(spec))
    (root / "data" / "catalog.fits").write_text("catalog\n")
    for argv in (["init", "-q"], ["add", "."]):
        subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)
    return root


def _build(root: Path) -> Graph:
    return plan.build(root)


# ---- what the graph contains -----------------------------------------------


def test_one_task_per_planned_result_keyed_by_its_id(tmp_path: Path) -> None:
    """Per universe, or once for an output that reads only what is pinned.
    The key is ASTRA's id for the result, never a second naming of lc's."""
    graph = _build(_project(tmp_path))
    assert sorted(graph.tasks) == ["fit#baseline", "report#baseline", "summary"]


def test_every_universe_gets_its_own_task(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path, _TWO))
    assert {task.universe for task in graph.tasks.values() if task.name == "fit"} == {
        "baseline",
        "alternative",
    }


def test_the_options_a_result_is_made_with_are_its_universes(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path, _TWO))
    assert graph.tasks["fit#alternative"].decisions == {"decisions.method": "nested"}
    assert graph.tasks["report#alternative"].decisions == {}


def test_a_result_is_at_the_place_astra_gives_it(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks["fit#baseline"]
    assert task.output_path == tmp_path / "results" / "baseline" / "fit.json"
    assert task.records == (tmp_path / ".astra" / "records" / "baseline" / "fit.json",)
    assert "results/baseline/fit.json" in task.recipe


def test_a_result_built_once_has_no_universe_folder(tmp_path: Path) -> None:
    """And a format holding a dot still leaves the record named after the
    result alone."""
    task = _build(_project(tmp_path)).tasks["summary"]
    assert (task.name, task.universe) == ("summary", "")
    assert task.output_path == tmp_path / "results" / "summary.tar.gz"
    assert task.records == (tmp_path / ".astra" / "records" / "summary.json",)


def test_recipe_resources_survive_graph_resolution(tmp_path: Path) -> None:
    spec = _SPEC.replace(
        "command: python src/fit.py",
        "resources: {cpus: 4, memory: 6Gi, time_limit: 1h30m}\n"
        "      command: python src/fit.py",
    )
    graph = _build(_project(tmp_path, spec))
    assert graph.tasks["fit#baseline"].resources == {
        "cpus": 4,
        "memory": "6Gi",
        "time_limit": "1h30m",
    }
    assert graph.tasks["report#baseline"].resources == {}


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("gpus: 1", {"gpus": 1}),
        ("disk: 10Gi", {"disk": "10Gi"}),
        ("cpus: 0.5", {"cpus": 0.5}),
        ("cpus: 0", {"cpus": 0}),
    ],
)
def test_execution_support_does_not_limit_graph_construction(
    tmp_path: Path, declaration: str, expected: dict[str, object],
) -> None:
    spec = _SPEC.replace(
        "command: python src/fit.py",
        f"resources: {{{declaration}}}\n      command: python src/fit.py",
    )
    task = _build(_project(tmp_path, spec)).tasks["fit#baseline"]
    assert task.resources == expected


def test_a_declared_input_resolves_to_its_path(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks["fit#baseline"]
    assert task.inputs == {"catalog": tmp_path / "data" / "catalog.fits"}
    assert task.produced_by == {}
    assert "data/catalog.fits" in task.recipe


def test_an_input_another_output_produces_becomes_an_edge(tmp_path: Path) -> None:
    task = _build(_project(tmp_path)).tasks["report#baseline"]
    assert task.produced_by == {"fit": "fit#baseline"}
    assert task.depends_on == ("fit#baseline",)
    assert task.inputs == {"fit": tmp_path / "results" / "baseline" / "fit.json"}
    assert "results/baseline/fit.json" in task.recipe


def test_a_pinned_input_is_an_edge_to_that_universe(tmp_path: Path) -> None:
    """The entry is named as the output writes it, pin and all."""
    task = _build(_project(tmp_path, _TWO)).tasks["summary"]
    assert task.produced_by == {"fit#baseline": "fit#baseline"}


def test_an_input_nothing_provides_is_an_error(tmp_path: Path) -> None:
    """Caught by ASTRA rather than by lc, in ASTRA's words, naming the line
    at fault instead of the run that tripped over it."""
    spec = _SPEC.replace("inputs: [catalog]", "inputs: [missing]").replace(
        "{inputs.catalog}", "{inputs.missing}"
    )
    with pytest.raises(ProjectError, match="(?s)does not validate.*UNRESOLVED_ADDRESS"):
        _build(_project(tmp_path, spec))


def test_an_output_without_a_format_is_refused_by_astra(tmp_path: Path) -> None:
    """lc places a result from its format, so ASTRA's refusal comes before
    anything runs — every offender at once."""
    spec = _SPEC.replace("    format: json\n", "").replace("    format: md\n", "")
    with pytest.raises(ProjectError, match="MISSING_FIELD") as raised:
        _build(_project(tmp_path, spec))
    assert "fit" in str(raised.value) and "report" in str(raised.value)


def test_a_cycle_is_refused_by_astra(tmp_path: Path) -> None:
    spec = _SPEC.replace("    inputs: [catalog]", "    inputs: [report]").replace(
        "{inputs.catalog}", "{inputs.report}"
    )
    with pytest.raises(ProjectError, match="OUTPUT_CYCLE"):
        _build(_project(tmp_path, spec))


def test_what_astra_notes_is_kept_for_the_run_to_report(tmp_path: Path) -> None:
    """A command that runs code from outside its analysis breaks no rule,
    and a run would otherwise live with it silently."""
    spec = _SPEC.replace("python src/report.py", "python ../elsewhere/report.py")
    notes = _build(_project(tmp_path, spec)).notes
    assert any("elsewhere" in note for note in notes)


def test_the_analysis_label_and_description_are_kept(tmp_path: Path) -> None:
    spec = _SPEC.replace("label: demo", "label: demo\ndescription: A test.")
    graph = _build(_project(tmp_path, spec))
    assert (graph.label, graph.description) == ("demo", "A test.")


# ---- ordering, closure, targets --------------------------------------------


def test_each_task_comes_after_the_tasks_it_reads(tmp_path: Path) -> None:
    order = list(_build(_project(tmp_path)).tasks)
    assert order.index("fit#baseline") < order.index("report#baseline")
    assert order.index("fit#baseline") < order.index("summary")


def test_asking_for_an_output_asks_for_what_it_is_made_of(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path))
    closure = graph.closure(graph.resolve(["report"]))
    assert list(closure.tasks) == ["fit#baseline", "report#baseline"]


def test_a_bare_target_means_every_universe(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path, _TWO))
    assert sorted(graph.resolve(["fit"])) == ["fit#alternative", "fit#baseline"]


def test_a_task_target_means_exactly_one(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path, _TWO))
    assert graph.resolve(["fit#baseline"]) == ["fit#baseline"]


def test_an_unknown_target_is_an_error_not_an_empty_run(tmp_path: Path) -> None:
    """Quietly making nothing is the least useful thing a build tool can
    do, so the message lists what there was."""
    graph = _build(_project(tmp_path))
    with pytest.raises(ProjectError, match="no output matches `typo`.*fit#baseline"):
        graph.resolve(["typo"])


# ---- refusals --------------------------------------------------------------


def test_no_spec_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no astra.yaml"):
        _build(tmp_path)


def test_an_analysis_that_declares_another_is_refused_by_name(tmp_path: Path) -> None:
    """Its results would run in another folder, under names lc does not
    place — refused whole rather than run in part."""
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "astra.yaml").write_text('spec_version: "0.1.0"\n')
    spec = _SPEC.replace('label: demo', "label: demo\nanalyses:\n  stage: {path: stage}")
    with pytest.raises(ProjectError, match="declares another analysis \\(`stage`\\)"):
        _build(_project(tmp_path, spec))


def test_an_analysis_with_no_universe_builds_what_is_built_once(tmp_path: Path) -> None:
    spec = """
    spec_version: "0.1.0"
    outputs:
      hello:
        type: report
        format: txt
        recipe:
          command: echo hello > {output}
    """
    graph = _build(_project(tmp_path, spec))
    assert list(graph.tasks) == ["hello"]


# ---- what ASTRA's checker found, kept beside the tasks ---------------------


def test_every_task_has_what_the_checker_found_of_it(tmp_path: Path) -> None:
    """One status per task, in the tasks' order: the state a run starts
    from, read once and handed to whoever classifies. Before any run that
    is ASTRA's `missing`, with the rule broken said in its own words."""
    graph = _build(_project(tmp_path))
    assert list(graph.found) == list(graph.tasks)
    for found in graph.found.values():
        assert (found.state, found.record) == ("missing", None)
        assert [broken.code for broken in found.why] == ["MISSING_RECORD"]


def test_the_graph_asks_astra_for_the_analysis_and_its_status_once_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the results are and how each stands are two answers, each asked
    once: validity comes with either, so nothing asks for it."""
    asked: list[str] = []
    resolve, status = api.resolve, api.status

    def resolved(path: Path) -> api.ResolvedAnalysis:
        asked.append("resolve")
        return resolve(path)

    def checked(path: Path) -> api.StatusReport:
        asked.append("status")
        return status(path)

    def refused(*args: object, **kwargs: object) -> None:
        raise AssertionError("the graph is read through `resolve` and `status` alone")

    monkeypatch.setattr(api, "resolve", resolved)
    monkeypatch.setattr(api, "status", checked)
    monkeypatch.setattr(api, "validate", refused)
    root = _project(tmp_path)

    assert _build(root).tasks
    assert asked == ["resolve", "status"]


def test_statuses_read_at_another_commit_than_the_analysis_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each answer names the commit it was read at: where a commit landed
    between the two, the statuses are not of the results resolved."""
    from dataclasses import replace

    status = api.status
    monkeypatch.setattr(api, "status", lambda path: replace(status(path), head="0" * 40))

    with pytest.raises(ProjectError, match="moved from .* while lc was reading"):
        _build(_project(tmp_path))


def test_narrowing_keeps_what_was_found_of_what_is_left(tmp_path: Path) -> None:
    graph = _build(_project(tmp_path))
    closure = graph.closure(["summary"])
    assert list(closure.found) == list(closure.tasks) == ["fit#baseline", "summary"]
    assert closure.found["fit#baseline"] == graph.found["fit#baseline"]
