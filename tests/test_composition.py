"""The specification's ways of composing analyses, run end to end.

An analysis declares others: by path, to drive one or read the results of
its universes, or with a `commit`, to read the results it had there, from
the project's own history or another repository's. Each is run here on a
real project, and what it leaves is looked at: where each result's bytes
and record are, what each recipe read, and that ASTRA's checker finds it
all unchanged afterwards.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import CLUSTER_ID, FIRST, clone_of, since

from lightcone.engine import dataset, plan, templates
from lightcone.engine import materialize as engine
from lightcone.engine.project import ProjectError, child_env

#: An analysis another declares: an output per universe, which says where
#: its recipe ran, and a universe of its own.
_STAGE = """
spec_version: "0.1.0"
decisions:
  d:
    options: {a: {}, b: {}}
outputs:
  out:
    type: data
    format: txt
    decisions: [d]
    recipe:
      command: echo {decisions.d} > {output} && pwd >> {output}
universes:
  v1:
    decisions: {d: b}
"""

#: A project that drives `stage` in a universe of its own, and reads the
#: result `stage` makes in its own.
_DRIVES = """
spec_version: "0.1.0"
analyses:
  stage: {path: stage}
outputs:
  both:
    type: report
    format: txt
    inputs: [stage.out, stage.out#v1]
    recipe:
      command: cat {inputs} > {output}
universes:
  u1:
    decisions: {stage.d: a}
"""


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


# ---- analyses declared by path ------------------------------------------------


def test_a_declared_analysis_is_built_in_its_own_folder_driven_or_not(
    analysis: Callable[..., Path], inline: None
) -> None:
    """Driven in the project's universe or built in its own, `stage`'s
    recipe runs in `stage`'s folder; a driven result is the project's, at
    its place, and one of `stage`'s own universe is at `stage`'s. Each is
    committed with its record, and ASTRA's checker finds them all
    unchanged."""
    root = analysis(
        _DRIVES,
        {"stage/astra.yaml": _STAGE, "stage/.gitattributes": templates.read("gitattributes.tmpl")},
    )
    tasks = plan.build(root).tasks

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert sorted(report.made) == ["both#u1", "stage.out#u1", "stage:out#v1"], report.warnings
    driven, own = tasks["stage.out#u1"], tasks["stage:out#v1"]
    assert driven.path.is_relative_to(root / "results")
    assert own.path.is_relative_to(root / "stage" / "results")
    assert _lines(driven.path) == ["a", str(root / "stage")]
    assert _lines(own.path) == ["b", str(root / "stage")]
    assert _lines(tasks["both#u1"].path) == ["a", str(root / "stage"), "b", str(root / "stage")]
    assert all(record.is_file() for task in tasks.values() for record in task.record_paths)
    assert not dataset.status(root)
    assert engine.check(root, []).up_to_date


def test_a_declared_analysis_whose_results_nothing_annexes_is_refused(
    analysis: Callable[..., Path],
) -> None:
    """Lightcone's `.gitattributes` lines reach the project's own
    `results/`, not `stage`'s: the refusal says to give `stage` its own."""
    root = analysis(_DRIVES, {"stage/astra.yaml": _STAGE})

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert "stage/results/v1/out.txt would be committed to git itself" in str(raised.value)
    assert "copy the project's .gitattributes into stage" in str(raised.value)


_SHARED = """
spec_version: "0.1.0"
inputs:
  table: {path: data/table.txt}
outputs:
  rows:
    type: data
    format: txt
    inputs: [table]
    recipe:
      command: bash rows.sh {inputs.table} {output}
"""

_READS_SHARED = """
spec_version: "0.1.0"
analyses:
  shared: {path: ../shared}
outputs:
  report:
    type: report
    format: txt
    inputs: [shared.rows]
    recipe:
      command: cat {inputs} > {output}
"""


def test_an_analysis_beside_the_project_runs_in_its_folder_and_is_kept_clean(
    analysis: Callable[..., Path], inline: None
) -> None:
    """`shared` is beside the project, in the same repository: its recipe
    runs in its folder and reads its own code and input there, outside the
    project's tree, and its result is committed there. A change in its folder is a
    change to the commit a record names, so a run refuses it."""
    root = analysis(
        _READS_SHARED,
        {
            "../shared/astra.yaml": _SHARED,
            "../shared/data/table.txt": "one\ntwo\n",
            "../shared/rows.sh": 'cat "$1" > "$2"\n',
            "../shared/.gitattributes": templates.read("gitattributes.tmpl"),
        },
        above=True,
    )

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert sorted(report.made) == ["report", "shared:rows"], (report.failed, report.warnings)
    assert _lines(root / ".." / "shared" / "results" / "rows.txt") == ["one", "two"]
    assert _lines(root / "results" / "report.txt") == ["one", "two"]
    assert not dataset.status(root, also=["../shared"])

    table = root / ".." / "shared" / "data" / "table.txt"
    table.unlink()  # annexed thin: writing it in place would write its annex object
    table.write_text("edited\n")
    with pytest.raises(ProjectError, match="uncommitted changes"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)


# ---- analyses declared with a commit ------------------------------------------


def test_a_bytes_free_clone_gives_the_tree_of_a_past_commit_from_what_it_fetches(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """The clone holds pointers only. The content of the past commit's
    result is fetched into the clone, from wherever the clone fetches its
    own, and the commit's tree takes it from there."""
    root = analysis(FIRST)
    since(root)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    clone = clone_of(root, tmp_path)
    spec = clone / "astra.yaml"
    spec.write_text(spec.read_text().replace("cat {inputs}", "cat -u {inputs}"))
    dataset.save(clone, [spec], "compare again")

    report = engine.materialize(clone, [], cluster_id=CLUSTER_ID)

    assert report.made == ["compare"], (report.failed, report.warnings)
    assert _lines(clone / "results" / "compare.txt") == ["second", "first"]
    assert not dataset.status(clone)


_READS_UPSTREAM = """
spec_version: "0.1.0"
analyses:
  upstream: {url: "%s", commit: "%s"}
outputs:
  copy:
    type: data
    format: txt
    inputs: [upstream.fit]
    recipe:
      command: cat {inputs} > {output}
"""


def test_a_result_of_another_repository_is_read_from_a_clone_of_it_at_its_commit(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """The same as for the project's own history, from the repository the
    declaration names."""
    upstream = analysis(FIRST)
    assert engine.materialize(upstream, [], cluster_id=CLUSTER_ID).ok
    made_at = dataset.head(upstream)
    upstream = upstream.rename(tmp_path / "upstream")
    root = analysis(_READS_UPSTREAM % (upstream.as_uri(), made_at))

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["copy"], (report.failed, report.warnings)
    assert _lines(root / "results" / "copy.txt") == ["first"]
    assert (root / plan.COMMITS / made_at / ".git").is_dir()
    assert not dataset.status(root)


def test_the_recorded_command_gives_a_past_commits_tree_on_a_fresh_clone(
    analysis: Callable[..., Path],
    inline: None,
    tmp_path: Path,
    engine_dist: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`datalad rerun` knows nothing of another commit's tree: it fetches
    the files of the dataset the run record lists, and runs the worker,
    which gives the tree as a run does. On a fresh clone there is none
    until it does."""
    pytest.importorskip("datalad")
    version, dist = engine_dist
    monkeypatch.setattr(engine, "_engine_requirement", lambda: f"lightcone-cli=={version}")
    root = analysis(FIRST)
    made_at = since(root)
    assert engine.materialize(root, ["compare"], cluster_id=CLUSTER_ID).ok
    original = json.loads((root / ".astra/records/compare.json").read_text())
    clone = clone_of(root, tmp_path)
    assert not (clone / plan.COMMITS).exists()

    proc = subprocess.run(
        [sys.executable, "-c", "from datalad.api import rerun; rerun('HEAD')"],
        cwd=clone,
        capture_output=True,
        text=True,
        env={**child_env(), "UV_FIND_LINKS": str(dist)},
    )

    assert proc.returncode == 0, proc.stderr
    assert (clone / plan.COMMITS / made_at / ".git").is_dir()
    rerun = json.loads((clone / ".astra/records/compare.json").read_text())
    assert rerun["content_hash"] == original["content_hash"]
    assert _lines(clone / "results" / "compare.txt") == ["second", "first"]
