"""Tests for `lightcone.engine.plan` — the run's plan, read from ASTRA.

What the spec *means* is ASTRA's and is tested there. What is pinned here
is what lc adds: that each planned result lc builds becomes one task —
ASTRA's own account of it, with what a worker needs looked up beside it,
every path measured from the project root wherever that sits in its
repository; that one question to ASTRA gets the tasks and how each stands;
and that what this engine cannot execute, or a target that matches
nothing, is an error rather than a guess.
"""

from __future__ import annotations

import subprocess
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest
from astra import api

from lightcone.engine import dataset, plan
from lightcone.engine.project import ProjectError

_SPEC = """
spec_version: "0.1.0"

analyses:
  stage: {path: stage}

inputs:
  catalog: {path: data/catalog.csv}

decisions:
  method:
    options:
      mcmc: {}
      nested: {}

universes:
  baseline: {decisions: {method: mcmc, stage.depth: deep}}
  alternative: {decisions: {method: nested, stage.depth: shallow}}

outputs:
  summary:
    type: table
    format: csv
    inputs: [catalog]
    recipe:
      command: python src/summary.py {inputs.catalog} {output}

  fit:
    type: metric
    format: json
    inputs: [summary, catalog, stage.mask]
    decisions: [method]
    recipe:
      command: python src/fit.py {inputs.summary} {inputs.stage.mask} -m {decisions.method} {output}

  report:
    type: report
    format: md
    inputs: [fit]
    recipe:
      command: python src/report.py {inputs.fit} {output}
"""

#: The same analysis reading one of the stage's own universes instead of
#: driving it, so the stage's result stays the stage's.
_PINNED = (
    _SPEC.replace(", stage.depth: deep", "")
    .replace(", stage.depth: shallow", "")
    .replace("stage.mask]", "stage.mask#v]")
    .replace("{inputs.stage.mask}", "{inputs.stage.mask#v}")
)

_STAGE = """
spec_version: "0.1.0"

decisions:
  depth:
    options:
      deep: {}
      shallow: {}

universes:
  v: {decisions: {depth: deep}}

outputs:
  mask:
    type: data
    format: txt
    decisions: [depth]
    recipe:
      command: python mask.py {decisions.depth} {output}
"""


def _git(repo: Path, *argv: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", *argv],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _project(repo: Path, spec: str = _SPEC, *, below: str = "") -> Path:
    """Write the analysis and its stage into a repository, tracked.

    Args:
        repo: The top of the repository to create.
        spec: The root analysis.
        below: The project root's path from the top of the repository.

    Returns:
        The project root.
    """
    root = repo / below
    for folder in ("data", "stage"):
        (root / folder).mkdir(parents=True)
    (root / "astra.yaml").write_text(textwrap.dedent(spec))
    (root / "stage" / "astra.yaml").write_text(textwrap.dedent(_STAGE))
    (root / "data" / "catalog.csv").write_text("x,y\n1,2\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    return root


# ---- what a task carries ---------------------------------------------------


def test_one_task_per_planned_result_keyed_by_its_id(tmp_path: Path) -> None:
    """Built once, per universe, and the stage's output built in the
    project's universes because they drive it. The key is ASTRA's id for
    the result, never a second naming of lc's own."""
    graph = plan.build(_project(tmp_path))
    assert sorted(graph.tasks) == [
        "fit#alternative",
        "fit#baseline",
        "report#alternative",
        "report#baseline",
        "stage.mask#alternative",
        "stage.mask#baseline",
        "summary",
    ]
    assert all(key == task.key == task.result.id for key, task in graph.tasks.items())


def test_a_result_built_per_universe_has_its_place_under_the_universe(tmp_path: Path) -> None:
    task = plan.build(_project(tmp_path)).tasks["fit#baseline"]
    (place,) = task.result.places
    assert (plan.name(place), place.address) == ("fit", "fit#baseline")
    assert place.bytes_path == "results/baseline/fit.json"
    assert place.record_path == ".astra/records/baseline/fit.json"


def test_a_result_built_once_has_no_universe_directory(tmp_path: Path) -> None:
    task = plan.build(_project(tmp_path)).tasks["summary"]
    (place,) = task.result.places
    assert (plan.name(place), place.address) == ("summary", "summary")
    assert place.bytes_path == "results/summary.csv"
    assert place.record_path == ".astra/records/summary.json"
    assert task.decisions == {}


def test_the_command_arrives_as_it_runs(tmp_path: Path) -> None:
    """A worker is handed a shell command, never a template to fill:
    ASTRA's, taken as given, its paths spelled from where the recipe runs
    — the project's own folder, for the project's own output."""
    task = plan.build(_project(tmp_path)).tasks["fit#alternative"]
    assert task.workdir == "."
    assert task.command == task.result.command == (
        "python src/fit.py results/summary.csv results/alternative/stage.mask.txt "
        "-m nested results/alternative/fit.json"
    )


def test_the_options_a_result_is_made_with_are_looked_up_by_ref(tmp_path: Path) -> None:
    """ASTRA's model names a decision once, by ref, and says what a
    universe selects for it; a task holds the two joined, for the
    decisions its own output lists and no other."""
    tasks = plan.build(_project(tmp_path)).tasks
    assert tasks["fit#alternative"].decisions == {"decisions.method": "nested"}
    assert tasks["fit#baseline"].decisions == {"decisions.method": "mcmc"}
    assert tasks["stage.mask#alternative"].decisions == {"stage.decisions.depth": "shallow"}
    assert tasks["report#baseline"].decisions == {}


def test_reads_keep_their_entries_and_name_files_from_the_root(tmp_path: Path) -> None:
    """Each entry as written and in order: a declared input's own file, or
    the bytes of the result the entry reads."""
    task = plan.build(_project(tmp_path)).tasks["fit#baseline"]
    assert task.reads == {
        "summary": "results/summary.csv",
        "catalog": "data/catalog.csv",
        "stage.mask": "results/baseline/stage.mask.txt",
    }
    assert list(task.reads) == ["summary", "catalog", "stage.mask"]
    assert [source.entry for source in task.result.inputs] == list(task.reads)


def test_only_entries_another_task_makes_become_edges(tmp_path: Path) -> None:
    graph = plan.build(_project(tmp_path))
    fit = graph.tasks["fit#baseline"]
    assert _made_by(fit) == {"summary": "summary", "stage.mask": "stage.mask#baseline"}
    assert fit.depends_on == ("summary", "stage.mask#baseline")
    assert _made_by(graph.tasks["summary"]) == {}
    assert graph.tasks["summary"].depends_on == ()


def _made_by(task: plan.Task) -> dict[str, str]:
    """Each entry of *task* that reads a result, and that result's task."""
    return {
        source.entry: source.result
        for source in task.result.inputs
        if source.result is not None
    }


def test_reading_one_task_twice_is_one_dependency(tmp_path: Path) -> None:
    spec = _SPEC.replace("inputs: [fit]", "inputs: [fit, fit#baseline]").replace(
        "{inputs.fit}", "{inputs}"
    )
    task = plan.build(_project(tmp_path, spec)).tasks["report#baseline"]
    assert _made_by(task) == {"fit": "fit#baseline", "fit#baseline": "fit#baseline"}
    assert task.depends_on == ("fit#baseline",)


def test_tasks_come_after_the_tasks_they_read(tmp_path: Path) -> None:
    """Submission walks the graph as given, handing each task its
    upstreams' futures — so they must already have been submitted."""
    order = list(plan.build(_project(tmp_path)).tasks)
    for key, task in plan.build(_project(tmp_path / "again")).tasks.items():
        assert all(order.index(upstream) < order.index(key) for upstream in task.depends_on)


def test_a_driven_analysis_result_belongs_to_the_project_and_runs_in_its_own_folder(
    tmp_path: Path,
) -> None:
    """The result is named through the declaration and lands in the
    project's `results/`; the recipe is the stage's, so it runs there and
    its paths are spelled from there."""
    task = plan.build(_project(tmp_path)).tasks["stage.mask#baseline"]
    (place,) = task.result.places
    assert plan.name(place) == "stage.mask"
    assert place.bytes_path == "results/baseline/stage.mask.txt"
    assert place.record_path == ".astra/records/baseline/stage.mask.json"
    assert task.workdir == "stage"
    assert task.command == "python mask.py deep ../results/baseline/stage.mask.txt"
    assert task.decisions == {"stage.decisions.depth": "deep"}


def test_a_result_of_another_analysis_is_keyed_under_its_analysis(tmp_path: Path) -> None:
    """Read from one of the stage's own universes, the result stays the
    stage's — so its address alone would not say whose it is, and its key
    leads with the stage's own."""
    graph = plan.build(_project(tmp_path, _PINNED))
    assert "stage/mask#v" not in graph.tasks
    task = graph.tasks["stage:mask#v"]
    (place,) = task.result.places
    assert (plan.name(place), place.address) == ("mask", "mask#v")
    assert place.bytes_path == "stage/results/v/mask.txt"
    assert place.record_path == "stage/.astra/records/v/mask.json"
    assert task.workdir == "stage"
    assert task.command == "python mask.py deep results/v/mask.txt"
    assert task.decisions == {"stage.decisions.depth": "deep"}

    fit = graph.tasks["fit#baseline"]
    assert fit.reads["stage.mask#v"] == "stage/results/v/mask.txt"
    assert _made_by(fit)["stage.mask#v"] == "stage:mask#v"
    assert fit.depends_on == ("summary", "stage:mask#v")


@pytest.mark.parametrize("spec", [_SPEC, _PINNED])
def test_a_project_below_its_repository_top_plans_the_same(tmp_path: Path, spec: str) -> None:
    """Everything lc does with a path starts at the project root, and
    ASTRA gives every path of a task from there. What the checker found
    and what ASTRA notes are kept in ASTRA's own words, which place a
    diagnostic from the top of the repository — so of those, only what
    they say is compared."""
    at_top = plan.build(_project(tmp_path / "top", spec))
    below = plan.build(_project(tmp_path / "nested", spec, below="papers/one"))
    assert below.tasks == at_top.tasks
    assert _standing(below) == _standing(at_top)
    assert len(below.notes) == len(at_top.notes)


def _standing(graph: plan.Graph) -> dict[str, str]:
    return {key: found.state for key, found in graph.found.items()}


def test_recipe_resources_are_carried_as_declared(tmp_path: Path) -> None:
    """Only what was declared: whether this executor can honour it is
    decided when executing, not when planning."""
    spec = _SPEC.replace(
        "command: python src/summary.py",
        "resources: {cpus: 0.5, memory: 6Gi, gpus: 1, time_limit: 1h30m}\n"
        "      command: python src/summary.py",
    )
    graph = plan.build(_project(tmp_path, spec))
    assert graph.tasks["summary"].resources == {
        "cpus": 0.5,
        "memory": "6Gi",
        "gpus": 1,
        "time_limit": "1h30m",
    }
    assert graph.tasks["report#baseline"].resources == {}


def test_a_command_reaching_outside_its_analysis_is_noted_not_refused(tmp_path: Path) -> None:
    spec = _SPEC.replace("python src/report.py", "python ../src/report.py")
    graph = plan.build(_project(tmp_path, spec))
    assert "report#baseline" in graph.tasks
    assert any("../src/report.py" in note for note in graph.notes)
    assert plan.build(_project(tmp_path / "clean")).notes == ()


def test_an_output_nothing_plans_is_noted(tmp_path: Path) -> None:
    """A run would silently never make it, which is worth one line: the
    stage declares a second output the project neither reads nor cites."""
    root = _project(tmp_path)
    with (root / "stage" / "astra.yaml").open("a") as stage:
        stage.write(
            "  spare:\n    type: data\n    format: txt\n"
            "    recipe:\n      command: python spare.py {output}\n"
        )
    graph = plan.build(root)
    assert not [key for key in graph.tasks if "spare" in key]
    (note,) = graph.notes
    assert "`spare` has no planned result" in note


def test_how_an_analysis_is_written_is_not_repeated_at_every_run(tmp_path: Path) -> None:
    """ASTRA also notes a pin that names the stage's universe only because
    the project has none of that id. That is how this analysis is written,
    not something a run lives with."""
    root = _project(tmp_path, _PINNED)
    assert "FALLBACK_PIN" in [note.code for note in api.resolve(root).notes]
    assert plan.build(root).notes == ()


# ---- what ASTRA's checker found, kept beside the tasks ---------------------


def test_every_task_has_what_the_checker_found_of_it(tmp_path: Path) -> None:
    """One status per task, in the tasks' order: the state a run starts
    from, read once and handed to whoever classifies. Before any run that
    is ASTRA's `missing`, with the rule broken said in its own words."""
    graph = plan.build(_project(tmp_path))
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

    assert plan.build(root).tasks
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
        plan.build(_project(tmp_path))


def test_the_analysis_names_itself_in_its_own_words(tmp_path: Path) -> None:
    """What a publication of the results is titled with — the file's
    `label` and `description`, and nothing made up where it gives none."""
    spec = 'label: "Supernova fit"\ndescription: "Fits the 2024 catalog."\n' + (
        textwrap.dedent(_SPEC)
    )
    graph = plan.build(_project(tmp_path / "titled", spec))
    assert (graph.label, graph.description) == ("Supernova fit", "Fits the 2024 catalog.")

    untitled = plan.build(_project(tmp_path / "untitled"))
    assert (untitled.label, untitled.description) == ("", "")


def test_narrowing_keeps_what_was_found_of_what_is_left(tmp_path: Path) -> None:
    graph = plan.build(_project(tmp_path))
    closure = graph.closure(["fit#baseline"])
    assert list(closure.found) == list(closure.tasks)
    assert closure.found["summary"] == graph.found["summary"]


def test_reading_the_graph_writes_nothing(analysis: Callable[..., Path]) -> None:
    """ASTRA reads records, hashes inputs and reads git; a read-only verb
    built on this must leave the tree as it found it."""
    root = analysis(_SPEC, {"data/catalog.csv": "x,y\n1,2\n", "stage/astra.yaml": _STAGE})
    plan.build(root)
    assert dataset.status(root) == []


# ---- closure, targets ------------------------------------------------------


def test_asking_for_an_output_asks_for_what_it_is_made_of(tmp_path: Path) -> None:
    graph = plan.build(_project(tmp_path))
    closure = graph.closure(["report#baseline"])
    assert list(closure.tasks) == [
        key
        for key in graph.tasks
        if key in {"summary", "stage.mask#baseline", "fit#baseline", "report#baseline"}
    ]


def test_a_bare_name_means_every_universe(tmp_path: Path) -> None:
    graph = plan.build(_project(tmp_path))
    assert sorted(graph.resolve(["fit"])) == ["fit#alternative", "fit#baseline"]
    assert sorted(graph.resolve(["stage.mask"])) == [
        "stage.mask#alternative",
        "stage.mask#baseline",
    ]
    assert graph.resolve(["summary"]) == ["summary"]


def test_a_key_means_exactly_one(tmp_path: Path) -> None:
    graph = plan.build(_project(tmp_path, _PINNED))
    assert graph.resolve(["fit#baseline", "stage:mask#v"]) == ["fit#baseline", "stage:mask#v"]


def test_a_bare_name_is_a_result_s_own_whoever_it_belongs_to(tmp_path: Path) -> None:
    """The stage's result is named `mask` where it is kept, so that is the
    name that finds it; the entry the project reads it by is not a name."""
    graph = plan.build(_project(tmp_path, _PINNED))
    assert graph.resolve(["mask"]) == ["stage:mask#v"]
    with pytest.raises(ProjectError, match="no output matches `stage.mask`"):
        graph.resolve(["stage.mask"])


def test_an_unknown_target_is_an_error_not_an_empty_run(tmp_path: Path) -> None:
    """Quietly making nothing is the least useful thing a build tool can
    do, so the message lists what there was."""
    graph = plan.build(_project(tmp_path))
    with pytest.raises(ProjectError, match="no output matches `typo`.*fit#baseline"):
        graph.resolve(["typo"])


# ---- refusals --------------------------------------------------------------


def test_no_spec_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no astra.yaml"):
        plan.build(tmp_path)


def test_a_spec_astra_rejects_is_refused_in_astras_words(tmp_path: Path) -> None:
    """Every broken rule at once, each placed in its file — a spec is fixed
    in one pass, and the fault is the file's rather than the run's."""
    spec = _SPEC.replace("inputs: [catalog]", "inputs: [missing]")
    with pytest.raises(ProjectError, match="does not validate") as raised:
        plan.build(_project(tmp_path, spec))
    message = str(raised.value)
    assert "UNRESOLVED_ADDRESS" in message
    assert "UNDECLARED_TEMPLATE_REF" in message
    assert "astra.yaml:" in message


def test_an_unparseable_spec_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="does not validate"):
        plan.build(_project(tmp_path, "outputs: [unclosed\n"))


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [(_SPEC, "`stage.mask#baseline` belongs to"), (_PINNED, "reads `stage.mask#v` from")],
)
def test_an_analysis_declared_with_a_commit_is_refused(
    tmp_path: Path, spec: str, fragment: str
) -> None:
    """Its results are made from that commit's tree, which is not the tree
    a recipe would run in here."""
    root = _project(tmp_path, spec)
    _git(root, "commit", "-q", "-m", "stage")
    pinned = f"stage: {{path: stage, commit: {_git(root, 'rev-parse', 'HEAD')}}}"
    (root / "astra.yaml").write_text(spec.replace("stage: {path: stage}", pinned))

    with pytest.raises(ProjectError, match="declared with a `commit`") as raised:
        plan.build(root)
    assert fragment in str(raised.value)


def test_a_result_from_a_commit_that_nothing_here_reads_is_no_task(tmp_path: Path) -> None:
    """It is never built again, only read through its record at that
    commit — so where no recipe of the working tree reads it, there is
    nothing to refuse and nothing to make: here the project only cites
    it as evidence."""
    cited = (
        _SPEC.replace(", stage.depth: deep", "")
        .replace(", stage.depth: shallow", "")
        .replace("inputs: [summary, catalog, stage.mask]", "inputs: [summary, catalog]")
        .replace(" {inputs.stage.mask}", "")
        + """
findings:
  masked:
    claim: The mask holds.
    evidence:
      - artifact: stage.mask#v
"""
    )
    root = _project(tmp_path, cited)
    _git(root, "commit", "-q", "-m", "stage")
    pinned = f"stage: {{path: stage, commit: {_git(root, 'rev-parse', 'HEAD')}}}"
    (root / "astra.yaml").write_text(
        textwrap.dedent(cited).replace("stage: {path: stage}", pinned)
    )

    planned = [result.id for result in api.resolve(root).results]
    graph = plan.build(root)

    assert "stage:mask#v" in planned
    assert sorted(graph.tasks) == sorted(set(planned) - {"stage:mask#v"})
    assert list(graph.found) == list(graph.tasks)


def test_a_result_owned_by_a_repository_of_its_own_is_refused(tmp_path: Path) -> None:
    """lc commits what it makes, and only in the project's repository."""
    root = _project(tmp_path, _PINNED)
    (root / "stage" / ".git").mkdir()
    with pytest.raises(ProjectError, match="repository of its own") as raised:
        plan.build(root)
    assert "`stage:mask#v` belongs to the analysis in stage" in str(raised.value)


