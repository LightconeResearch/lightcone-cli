"""Tests for `lightcone.engine.plan` — ASTRA's plan, carried across as tasks.

What the spec *means* is ASTRA's and is tested there. What is pinned here
is what lc adds: that each planned result becomes one task holding
everything a worker needs, that every path in it is measured from the
project root — wherever that sits in its repository — and that what this
engine cannot execute, or a target that matches nothing, is an error
rather than a guess.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from lightcone.engine import plan
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


def test_one_task_per_planned_result_keyed_by_its_address(tmp_path: Path) -> None:
    """Built once, per universe, and the stage's output built in the
    project's universes because they drive it."""
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
    assert all(key == task.key for key, task in graph.tasks.items())


def test_a_result_built_per_universe_has_its_place_under_the_universe(tmp_path: Path) -> None:
    task = plan.build(_project(tmp_path)).tasks["fit#baseline"]
    (place,) = task.places
    assert (place.name, place.address) == ("fit", "fit#baseline")
    assert place.bytes == "results/baseline/fit.json"
    assert place.record == ".astra/records/baseline/fit.json"


def test_a_result_built_once_has_no_universe_directory(tmp_path: Path) -> None:
    task = plan.build(_project(tmp_path)).tasks["summary"]
    (place,) = task.places
    assert (place.name, place.address) == ("summary", "summary")
    assert place.bytes == "results/summary.csv"
    assert place.record == ".astra/records/summary.json"
    assert task.decisions == {}


def test_the_command_arrives_rendered(tmp_path: Path) -> None:
    """A worker is handed a shell command, never a template to fill."""
    task = plan.build(_project(tmp_path)).tasks["fit#alternative"]
    assert task.workdir == ""
    assert task.command == (
        "python src/fit.py results/summary.csv results/alternative/stage.mask.txt "
        "-m nested results/alternative/fit.json"
    )
    assert task.decisions == {"method": "nested"}


def test_inputs_keep_their_entries_and_name_files_from_the_root(tmp_path: Path) -> None:
    task = plan.build(_project(tmp_path)).tasks["fit#baseline"]
    assert task.inputs == {
        "summary": "results/summary.csv",
        "catalog": "data/catalog.csv",
        "stage.mask": "results/baseline/stage.mask.txt",
    }
    assert list(task.inputs) == ["summary", "catalog", "stage.mask"]


def test_only_inputs_another_task_makes_become_edges(tmp_path: Path) -> None:
    graph = plan.build(_project(tmp_path))
    fit = graph.tasks["fit#baseline"]
    assert fit.produced_by == {"summary": "summary", "stage.mask": "stage.mask#baseline"}
    assert fit.depends_on == ("summary", "stage.mask#baseline")
    assert graph.tasks["summary"].produced_by == {}
    assert graph.tasks["summary"].depends_on == ()


def test_reading_one_task_twice_is_one_dependency(tmp_path: Path) -> None:
    spec = _SPEC.replace("inputs: [fit]", "inputs: [fit, fit#baseline]").replace(
        "{inputs.fit}", "{inputs}"
    )
    task = plan.build(_project(tmp_path, spec)).tasks["report#baseline"]
    assert task.produced_by == {"fit": "fit#baseline", "fit#baseline": "fit#baseline"}
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
    (place,) = task.places
    assert place.name == "stage.mask"
    assert place.bytes == "results/baseline/stage.mask.txt"
    assert place.record == ".astra/records/baseline/stage.mask.json"
    assert task.workdir == "stage"
    assert task.command == "python mask.py deep ../results/baseline/stage.mask.txt"
    assert task.decisions == {"depth": "deep"}


def test_a_result_of_another_analysis_is_keyed_under_its_folder(tmp_path: Path) -> None:
    """Read from one of the stage's own universes, the result stays the
    stage's — so its address alone would not say whose it is."""
    graph = plan.build(_project(tmp_path, _PINNED))
    task = graph.tasks["stage/mask#v"]
    (place,) = task.places
    assert place.address == "mask#v"
    assert place.bytes == "stage/results/v/mask.txt"
    assert place.record == "stage/.astra/records/v/mask.json"
    assert task.workdir == "stage"
    assert task.command == "python mask.py deep results/v/mask.txt"

    fit = graph.tasks["fit#baseline"]
    assert fit.inputs["stage.mask#v"] == "stage/results/v/mask.txt"
    assert fit.produced_by["stage.mask#v"] == "stage/mask#v"


@pytest.mark.parametrize("spec", [_SPEC, _PINNED])
def test_a_project_below_its_repository_top_plans_the_same(tmp_path: Path, spec: str) -> None:
    """ASTRA measures from the top of the repository; everything lc does
    with a path starts at the project root."""
    at_top = plan.build(_project(tmp_path / "top", spec))
    below = plan.build(_project(tmp_path / "nested", spec, below="papers/one"))
    assert below == at_top


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
    assert graph.resolve(["fit#baseline", "stage/mask#v"]) == ["fit#baseline", "stage/mask#v"]


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


def test_a_result_owned_by_a_repository_of_its_own_is_refused(tmp_path: Path) -> None:
    """lc commits what it makes, and only in the project's repository."""
    root = _project(tmp_path, _PINNED)
    (root / "stage" / ".git").mkdir()
    with pytest.raises(ProjectError, match="repository of its own") as raised:
        plan.build(root)
    assert "stage/mask#v" in str(raised.value)


