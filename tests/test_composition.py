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

from lightcone.engine import dataset, plan, templates, worker
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
        {
            "stage/astra.yaml": _STAGE,
            ".gitattributes": templates.read("gitattributes.tmpl")
            + "stage/results/** annex.largefiles=anything\n",
        },
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
    """Lightcone's `.gitattributes` lines annex the project's own
    `results/`, not `stage`'s: the refusal names the line that does."""
    root = analysis(_DRIVES, {"stage/astra.yaml": _STAGE})

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert "stage/results/v1/out.txt would be committed to git itself" in str(raised.value)
    assert "add `stage/results/** annex.largefiles=anything`" in str(raised.value)


def test_a_driven_result_is_stored_by_the_projects_own_results_line(
    analysis: Callable[..., Path],
) -> None:
    """A driven recipe runs in `stage`, but its result is the project's, in
    the project's `results/`: where no line annexes that, the refusal names
    the result, and no line for `stage` would help."""
    attributes = templates.read("gitattributes.tmpl").replace(
        "results/** annex.largefiles=anything\n", ""
    )
    root = analysis(
        _DRIVES,
        {
            "stage/astra.yaml": _STAGE,
            ".gitattributes": attributes + "stage/results/** annex.largefiles=anything\n",
        },
    )
    driven = plan.declared_path(root, plan.build(root).tasks["stage.out#u1"].path)

    with pytest.raises(ProjectError) as raised:
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert f"{driven} would be committed to git itself" in str(raised.value)
    assert "stage/results/v1" not in str(raised.value)
    assert "add `" not in str(raised.value)


def test_a_declared_analysis_git_does_not_hold_is_refused(
    analysis: Callable[..., Path], inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`stage/astra.yaml` is on disk but ignored and untracked: the tree is
    clean, and the commit a record names holds no `stage` to check its
    results against. A run is refused, and so is a rerun."""
    root = analysis(_DRIVES, {"stage/astra.yaml": _STAGE})
    (root / ".gitignore").write_text((root / ".gitignore").read_text() + "stage/astra.yaml\n")
    dataset._git(["rm", "-q", "--cached", "stage/astra.yaml"], cwd=root)
    dataset._git(["add", ".gitignore"], cwd=root)
    dataset._git(["commit", "-q", "-m", "stop tracking stage"], cwd=root)
    assert not dataset.status(root)

    with pytest.raises(ProjectError, match="stage/astra.yaml — not in git"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)
    monkeypatch.chdir(root)
    assert worker.main(["stage.out#u1"]) == 2


# ---- analyses declared with a commit ------------------------------------------

_READS_AN_INPUT = """
spec_version: "0.1.0"
analyses:
  previous: {path: ., commit: "%s"}
outputs:
  copy:
    type: data
    format: txt
    inputs: [previous.catalog]
    recipe:
      command: cat {inputs} > {output}
"""


def test_a_commits_tree_changed_since_a_run_made_it_is_made_again(
    analysis: Callable[..., Path], inline: None
) -> None:
    """The tree of a commit is kept from run to run, and a file in it is
    what a recipe reads: a tree that is no longer that commit's is made
    again from the repository rather than trusted."""
    root = analysis(
        'spec_version: "0.1.0"\ninputs:\n  catalog: {path: data/catalog.txt}\n',
        {"data/catalog.txt": "stars\n"},
    )
    at = dataset.head(root)
    spec = root / "astra.yaml"
    spec.write_text(_READS_AN_INPUT % at)
    dataset.save(root, [spec], "copy the catalog as it was")
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    cached = root / plan.COMMITS / at / "data" / "catalog.txt"
    cached.write_text("tampered\n")
    spec.write_text(spec.read_text().replace("cat {inputs}", "cat -u {inputs}"))
    dataset.save(root, [spec], "copy it again")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["copy"], (report.failed, report.warnings)
    assert (root / "results" / "copy.txt").read_text() == "stars\n"
    assert cached.read_text() == "stars\n"


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


_READS_A_SUBMODULE = """
spec_version: "0.1.0"
analyses:
  module: {path: module, commit: "%s"}
outputs:
  copy:
    type: data
    format: txt
    inputs: [module.fit]
    recipe:
      command: cat {inputs} > {output}
"""


def test_a_commit_only_a_submodule_holds_is_not_taken_from_the_project(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """`module` is a submodule, read at the commit its gitlink pins, which
    only its checkout holds, and astra-tools names no repository for it:
    the project's does not hold it, so it is not asked for there, and what
    reads it is blocked, saying why."""
    module = tmp_path / "module"
    module.mkdir()
    (module / "astra.yaml").write_text(FIRST)
    for argv in (["init", "-q"], ["add", "."], ["commit", "-qm", "module"]):
        dataset._git(["-c", "user.name=Test", "-c", "user.email=t@example.com", *argv], cwd=module)
    root = analysis(FIRST)
    dataset._git(
        ["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(module), "module"],
        cwd=root,
    )
    dataset._git(["commit", "-q", "-m", "add the module"], cwd=root)
    spec = root / "astra.yaml"
    spec.write_text(_READS_A_SUBMODULE % dataset.head(root))
    dataset.save(root, [spec], "read the module at a commit")
    pinned = dataset._git(["rev-parse", "HEAD"], cwd=root / "module").strip()

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.blocked == ["copy"], (report.made, report.warnings)
    assert any(
        w.startswith(f"{pinned}: ") and "not in the project's repository" in w
        for w in report.warnings
    ), report.warnings
    assert not (root / plan.COMMITS / pinned).exists()


#: An analysis with nothing but an input, for another to read at a commit.
_AN_INPUT = 'spec_version: "0.1.0"\ninputs:\n  catalog: {path: data/catalog.txt}\n'


def test_a_commits_tree_git_cannot_read_is_made_again(
    analysis: Callable[..., Path], inline: None
) -> None:
    """A tree git cannot even inspect, its index damaged, is not known to
    be its commit's: it is made again, and the recipe reads the commit's
    bytes."""
    root = analysis(_AN_INPUT, {"data/catalog.txt": "stars\n"})
    at = dataset.head(root)
    spec = root / "astra.yaml"
    spec.write_text(_READS_AN_INPUT % at)
    dataset.save(root, [spec], "copy the catalog as it was")
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    (root / plan.COMMITS / at / ".git" / "index").write_bytes(b"not an index")
    spec.write_text(spec.read_text().replace("cat {inputs}", "cat -u {inputs}"))
    dataset.save(root, [spec], "copy it again")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["copy"], (report.blocked, report.warnings)
    assert (root / "results" / "copy.txt").read_text() == "stars\n"


_READS_UPSTREAMS_INPUT = """
spec_version: "0.1.0"
analyses:
  upstream: {url: "%s", commit: "%s"}
outputs:
  copy:
    type: data
    format: txt
    inputs: [upstream.catalog]
    recipe:
      command: cat {inputs} > {output}
  own:
    type: data
    format: txt
    recipe:
      command: echo own > {output}
"""


def test_a_commits_tree_that_cannot_be_given_blocks_only_what_reads_it(
    analysis: Callable[..., Path], inline: None, tmp_path: Path
) -> None:
    """The tree of `upstream`'s commit is no longer its commit's, and the
    repository it came from is gone: nothing reads a file of it, whatever
    is on disk there, and what reads nothing of it still runs."""
    upstream = analysis(_AN_INPUT, {"data/catalog.txt": "stars\n"})
    at = dataset.head(upstream)
    upstream = upstream.rename(tmp_path / "upstream")
    root = analysis(_READS_UPSTREAMS_INPUT % (upstream.as_uri(), at))
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    (root / plan.COMMITS / at / ".git" / "index").write_bytes(b"not an index")
    upstream.rename(tmp_path / "gone")
    spec = root / "astra.yaml"
    spec.write_text(
        spec.read_text()
        .replace("cat {inputs}", "cat -u {inputs}")
        .replace("echo own", "echo own again")
    )
    dataset.save(root, [spec], "make both again")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert (report.made, report.blocked) == (["own"], ["copy"]), report.warnings
    assert any(w.startswith(f"{at}: ") for w in report.warnings), report.warnings
    assert (root / "results" / "copy.txt").read_text() == "stars\n"
    assert not dataset.status(root)
