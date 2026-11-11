"""ASTRA 0.1's own shapes, run through lc end to end.

One project modelled on the specification's worked example: an analysis
that declares another, drives its decision, pins results across
universes, imports one of the declared analysis's own universes, derives
a universe from another and switches an output on with `when`. What the
spec *means* is ASTRA's to test; these check that lc builds, places,
records and re-checks each kind of result it plans.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import CLUSTER_ID

from lightcone.engine import dataset, materialize, plan

ROOT = """
    spec_version: "0.1.0"
    analyses:
      stage: {path: stage}
    inputs:
      catalog: {path: data/catalog.csv}
    decisions:
      method:
        options: {a: {}, b: {}}
      extra:
        when: [method.b]
        options: {on: {}, off: {}}
    outputs:
      fit:
        type: metric
        format: txt
        inputs: [catalog, stage.cleaned]
        decisions: [method, stage.cut]
        recipe:
          command: >-
            cat {inputs} > {output} &&
            echo {decisions.method} {decisions.stage.cut} >> {output}
      diagnostic:
        type: metric
        format: txt
        when: [method.b]
        inputs: [fit]
        decisions: [extra]
        recipe:
          command: "cp {inputs.fit} {output} && echo {decisions.extra} >> {output}"
      comparison:
        type: table
        format: txt
        inputs: ["fit#u1", "fit#u2", "stage.cleaned#stage_v1"]
        recipe:
          command: "cat {inputs} > {output}"
    universes:
      u1:
        decisions: {method: b, extra: on, stage.cut: tight}
      u2:
        derived_from: u1
        decisions: {method: a}
      stage_v1: {analysis: stage, universe: v1}
"""

STAGE = """
    spec_version: "0.1.0"
    inputs:
      raw: {path: data/raw.csv}
    decisions:
      cut:
        options: {tight: {}, loose: {}}
    outputs:
      cleaned:
        type: data
        format: csv
        inputs: [raw]
        decisions: [cut]
        recipe:
          command: "(cat {inputs.raw}; echo {decisions.cut}; basename $PWD) > {output}"
    universes:
      v1:
        decisions: {cut: loose}
"""

FILES = {
    "stage/astra.yaml": STAGE,
    "stage/data/raw.csv": "x\n1\n",
    "data/catalog.csv": "a,b\n1,2\n",
}

#: Every result the project plans, as lc names its task → where its bytes go.
PLACES = {
    "stage.cleaned#u1": "results/u1/stage.cleaned.csv",
    "fit#u1": "results/u1/fit.txt",
    "diagnostic#u1": "results/u1/diagnostic.txt",
    "stage.cleaned#u2": "results/u2/stage.cleaned.csv",
    "fit#u2": "results/u2/fit.txt",
    "stage/cleaned#v1": "stage/results/v1/cleaned.csv",
    "comparison": "results/comparison.txt",
}


@pytest.fixture
def project(analysis: Callable[..., Path]) -> Path:
    return analysis(ROOT, FILES)


@pytest.fixture
def built(project: Path, inline: None) -> Path:
    report = materialize.materialize(project, [], cluster_id=CLUSTER_ID)
    assert report.ok and sorted(report.made) == sorted(PLACES), report.warnings
    return project


def test_each_planned_result_is_one_task_at_the_place_astra_gives_it(project: Path) -> None:
    """Per universe, built once, driven, and belonging to the declared
    analysis: four kinds of result, one naming rule. `diagnostic` is
    planned only where its `when` holds, so `u2` has none."""
    graph = plan.build(project)

    assert {key: task.places[0].bytes for key, task in graph.tasks.items()} == PLACES
    assert graph.tasks["stage/cleaned#v1"].places[0].record == (
        "stage/.astra/records/v1/cleaned.json"
    )
    assert graph.tasks["comparison"].places[0].record == ".astra/records/comparison.json"


def test_a_pin_and_an_import_are_dependencies_like_any_other(project: Path) -> None:
    """A pinned entry reads another universe's result, and an imported
    universe reads the declared analysis's own — so the task built once
    waits on all three, each under the entry exactly as written."""
    task = plan.build(project).tasks["comparison"]

    assert task.produced_by == {
        "fit#u1": "fit#u1",
        "fit#u2": "fit#u2",
        "stage.cleaned#stage_v1": "stage/cleaned#v1",
    }
    assert task.inputs["stage.cleaned#stage_v1"] == "stage/results/v1/cleaned.csv"


def test_a_derived_universe_runs_under_its_effective_selection(project: Path) -> None:
    """`u2` sets only `method`; `stage.cut` is inherited, and `extra`,
    whose `when` no longer holds, is dropped with the output that needs it."""
    tasks = plan.build(project).tasks

    assert tasks["fit#u2"].decisions == {"method": "a", "stage.cut": "tight"}
    assert tasks["diagnostic#u1"].decisions == {"extra": "on"}
    assert "diagnostic#u2" not in tasks


def test_a_declared_analysis_s_recipe_runs_in_its_own_folder(built: Path) -> None:
    """Driven or not, the recipe's working directory is the folder of the
    analysis the output is declared in — while a driven result's bytes go
    to the driver's `results/`, under the driver's selection."""
    driven = (built / PLACES["stage.cleaned#u1"]).read_text().split()
    own = (built / PLACES["stage/cleaned#v1"]).read_text().split()

    assert driven[-2:] == ["tight", "stage"]
    assert own[-2:] == ["loose", "stage"]


def test_every_result_is_recorded_committed_and_current(built: Path) -> None:
    """The run ends as clean as it started, each result's record names it
    by address and lists exactly its output's entries, and the checker —
    asked again from scratch — finds nothing to do."""
    assert not dataset.status(built)

    record = json.loads((built / ".astra/records/comparison.json").read_text())
    assert record["address"] == "comparison"
    assert [item["entry"] for item in record["generated_by"]["inputs"]] == [
        "fit#u1",
        "fit#u2",
        "stage.cleaned#stage_v1",
    ]
    owned = json.loads((built / "stage/.astra/records/v1/cleaned.json").read_text())
    assert owned["address"] == "cleaned#v1"
    assert (built / "stage/results/v1/.cleaned.manifest.json").is_file()

    status = materialize.status(built)
    assert {o.output: o.status for o in status.outputs} == dict.fromkeys(PLACES, "current")
    assert materialize.check(built, []).up_to_date


def test_an_input_of_the_declared_analysis_stales_what_reads_it(built: Path) -> None:
    """The edit of the specification's own table: the declared analysis's
    input changes, so everything made from it is stale in every universe
    — and what never read it stays put."""
    (built / "stage/data/raw.csv").write_text("x\n2\n")
    dataset.save(built, ["stage/data/raw.csv"], "new raw data")

    report = materialize.check(built, [])

    assert sorted(report.planned) == sorted(PLACES)
    assert "content of `raw`" in report.planned["stage/cleaned#v1"]


def test_a_selection_edit_stales_only_the_universe_it_touches(
    built: Path, inline: None
) -> None:
    """`u2` now drives the declared analysis differently: its results are
    remade, with the comparison that pins one of them, and `u1` is left
    alone."""
    spec = built / "astra.yaml"
    spec.write_text(
        spec.read_text().replace(
            "decisions: {method: a}", "decisions: {method: a, stage.cut: loose}"
        )
    )
    dataset.save(built, ["astra.yaml"], "u2 loosens the cut")

    report = materialize.materialize(built, [], cluster_id=CLUSTER_ID)

    assert report.ok
    assert sorted(report.made) == ["comparison", "fit#u2", "stage.cleaned#u2"]
    assert (built / PLACES["stage.cleaned#u2"]).read_text().split()[-2] == "loose"


def test_a_target_is_a_task_or_a_name_in_every_universe(project: Path) -> None:
    graph = plan.build(project)

    assert graph.resolve(["fit"]) == ["fit#u1", "fit#u2"]
    assert graph.resolve(["stage/cleaned#v1"]) == ["stage/cleaned#v1"]
    assert list(graph.closure(["fit#u2"]).tasks) == ["stage.cleaned#u2", "fit#u2"]


def test_an_analysis_declared_at_a_commit_is_refused_not_guessed(
    analysis: Callable[..., Path],
) -> None:
    """Reading results from a commit means reading them through their
    records at that commit, which this engine does not do — so it says so
    before anything runs."""
    project = analysis(ROOT, FILES)
    head = dataset.head(project)[0]
    spec = project / "astra.yaml"
    spec.write_text(
        spec.read_text()
        .replace("stage: {path: stage}", f"stage: {{path: stage, commit: {head}}}")
        .replace("extra: on, stage.cut: tight}", "extra: on}")
        .replace('inputs: [catalog, stage.cleaned]', 'inputs: [catalog, "stage.cleaned#stage_v1"]')
        .replace("decisions: [method, stage.cut]", "decisions: [method]")
        .replace(" {decisions.stage.cut}", "")
    )
    dataset.save(project, ["astra.yaml"], "pin stage to a commit")

    with pytest.raises(plan.ProjectError, match="lc does not build from"):
        plan.build(project)
