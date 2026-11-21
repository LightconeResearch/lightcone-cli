"""A run's checkout: the tree its recipes run in.

Real git and git-annex throughout. Whether git-annex stays out of a linked
worktree, whether bytes take one more name or a copy, and whether the
project's tree ends as its commits have it are questions no stub answers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import CLUSTER_ID, _Inline

from lightcone.engine import checkout, compute, container, content, dataset, project, templates
from lightcone.engine import materialize as engine
from lightcone.engine.compute.model import Identity
from lightcone.engine.project import ProjectError, child_env

_SPEC = """
spec_version: "0.1.0"

inputs:
  catalog: {path: data/catalog.txt}

outputs:
  fit:
    type: metric
    format: txt
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} > {output}

  other:
    type: metric
    format: txt
    recipe:
      command: echo other > {output}
"""

#: Another allocation than the one the suite's runs are on.
_ELSEWHERE = Identity(provider="local", native_id="other", token="other").encode()


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC, {"data/catalog.txt": "1\n2\n", "data/unread.txt": "declared nowhere\n"})


def _taken(root: Path, allocation: str = CLUSTER_ID) -> checkout.Checkout:
    return checkout.take(root, dataset.head(root), allocation)


def _held(root: Path) -> list[Path]:
    """The checkouts runs hold under *root*, as git lists them."""
    return sorted(checkout._held(root))


# ---- the tree --------------------------------------------------------------


def test_a_checkout_is_the_commits_tree_and_git_annex_has_not_run_in_it(root: Path) -> None:
    """A linked worktree of the commit under the project's ignored
    `.lightcone/`, held by a lock that names the run's allocation. Every
    annexed file in it is the stand-in git stores, and its `.git` is the
    file git wrote, which git-annex replaces where it runs."""
    tree = _taken(root)

    assert tree.root.parent == root / checkout.RUNS
    assert (tree.root / "astra.yaml").read_text() == (root / "astra.yaml").read_text()
    assert (tree.top / ".git").is_file()
    assert content.standin(tree.root / "data/catalog.txt") == "git-annex"
    assert checkout._held(root) == {tree.top: CLUSTER_ID}
    assert not dataset.status(root)
    assert (root / "data/catalog.txt").read_text() == "1\n2\n"


def test_fill_gives_a_file_the_annexs_bytes_under_one_more_name(root: Path) -> None:
    """No copy: the file a recipe reads is the object the annex keeps. git
    is told to take it for the commit's, so the checkout stays a tree
    changed by nothing; a file no task reads stays a stand-in."""
    tree = _taken(root)
    name = f"{tree.commit}:./data/catalog.txt"

    checkout.fill(tree, ["astra.yaml", "data/catalog.txt"])

    assert (tree.root / "data/catalog.txt").read_text() == "1\n2\n"
    assert os.path.samefile(
        tree.root / "data/catalog.txt", dataset.objects(root, [name])[name]
    )
    assert content.standin(tree.root / "data/unread.txt") == "git-annex"
    assert dataset.status(tree.root, untracked_files=True, config=dataset.PLAIN) == []


def test_git_annex_run_in_a_checkout_copies_what_it_holds_so_no_git_there_runs_it(
    root: Path,
) -> None:
    """The measurement the module rests on. Under the options every git
    lc or a recipe runs in a checkout carries, an annexed file stays the
    stand-in it is. Without them, a bare `git status` has git-annex fill
    each file it holds the bytes of, and replace the worktree's `.git`."""
    tree = _taken(root)
    unread = tree.root / "data/unread.txt"

    def status(env: dict[str, str], touched: int) -> None:
        # A file whose times moved is one git has to read again, which is
        # when it starts a filter: nothing here is left to when the test ran.
        os.utime(unread, (touched, touched))
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=tree.root, env=env, capture_output=True
        )

    status({**child_env(), **dataset.PLAIN_ENV}, touched=1_000_000_000)
    assert content.standin(unread) == "git-annex"
    assert (tree.top / ".git").is_file()

    status(child_env(), touched=1_100_000_000)
    assert unread.read_text() == "declared nowhere\n"
    assert not os.path.samefile(unread, root / "data/unread.txt")
    assert (tree.top / ".git").is_symlink()


def test_a_recipe_in_a_checkout_reaches_neither_the_projects_tree_nor_git_annex(
    root: Path,
) -> None:
    """The policy a recipe runs under is built from the tree it runs in:
    the checkout is readable, the project's own working tree is not
    (UNDECLARED_READ), and whatever git it runs carries the options that
    keep git-annex out."""
    tree = _taken(root)
    runtime = container.runtime_for_run(root, build=False).at(tree.root)

    policy = container.policy_for(runtime, [])

    try:
        assert policy.grants(tree.root / "astra.yaml", policy.read)
        assert not policy.grants(root / "astra.yaml", policy.read)
        assert policy.env.items() >= dataset.PLAIN_ENV.items()
        assert "GIT_CONFIG_COUNT" not in container.policy_for(
            container.runtime_for_run(root, build=False), []
        ).env
    finally:
        shutil.rmtree(policy.tmp_home, ignore_errors=True)


# ---- whose it is -----------------------------------------------------------


def test_a_checkout_stays_until_its_allocation_has_ended(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that ended without removing its checkout may have left a
    recipe writing there. Nothing asks whether it did: the checkout goes
    when the allocation it names has ended, and another's stays."""
    first, second = _taken(root), _taken(root, _ELSEWHERE)
    monkeypatch.setattr(compute, "ended", lambda allocation: False)
    third = _taken(root)
    assert _held(root) == sorted([first.top, second.top, third.top])

    monkeypatch.setattr(compute, "ended", lambda allocation: allocation == CLUSTER_ID)
    fourth = _taken(root, _ELSEWHERE)

    assert _held(root) == sorted([second.top, fourth.top])
    assert not first.top.exists() and not third.top.exists()


def test_a_run_removes_its_own_checkout_and_forgets_one_that_was_removed(root: Path) -> None:
    """`leave` removes the folder and what git keeps of it. A folder
    someone removed by hand is held for nothing, and one git no longer
    reads as a worktree is removed all the same."""
    kept, removed, broken = _taken(root), _taken(root), _taken(root)
    shutil.rmtree(removed.top)
    (broken.top / ".git").unlink()

    checkout.leave(broken)
    assert _held(root) == sorted([kept.top, removed.top])
    assert not broken.top.exists()

    last = _taken(root)
    assert _held(root) == sorted([kept.top, last.top])


# ---- landing ---------------------------------------------------------------


def test_a_landed_result_is_one_copy_and_the_tree_ends_as_its_commits_have_it(
    root: Path, inline: None
) -> None:
    """The recipe wrote its file in the checkout. In the project it is the
    annex's own object under one more name, as a result made in place is,
    and nothing of the run is left: no checkout, and no change."""
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert sorted(report.made) == ["fit", "other"], (report.failed, report.warnings)
    assert (root / "results/fit.txt").read_text() == "1\n2\n"
    made, name = root / "results/other.txt", "HEAD:./results/other.txt"
    assert os.path.samefile(made, dataset.objects(root, [name])[name])
    assert made.stat().st_nlink == 2
    assert not dataset.status(root)
    assert _held(root) == []
    assert [path.name for path in (root / checkout.RUNS).iterdir()] == ["uv.toml"]
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).up_to_date


def test_a_project_below_the_top_of_its_repository_runs_in_a_checkout_of_all_of_it(
    tmp_path: Path, real_tools: None, inline: None
) -> None:
    """`lc init subdir/` adopts the repository around it. A worktree is of
    the whole repository, so the run's root is the project's folder in the
    checkout, and each result lands at its place in the project's."""
    top, root = tmp_path / "repository", tmp_path / "repository" / "project"
    (root / "data").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "analysis"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n'
    )
    (root / ".python-version").write_text(templates.python_version())
    (root / ".gitattributes").write_text(templates.read("gitattributes.tmpl"))
    (root / ".gitignore").write_text(templates.read("gitignore.tmpl"))
    (root / "astra.yaml").write_text(_SPEC.replace("cat {inputs.catalog}", "pwd"))
    (root / "data/catalog.txt").write_text("1\n2\n")
    (top / "README.md").write_text("above the project\n")
    for argv in (["uv", "lock", "-q"], ["uv", "sync", "-q", "--locked", "--exact"]):
        assert project._run([*argv, "--project", str(root)], cwd=root).returncode == 0
    dataset.init_git(top)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=top)
    dataset.init_annex(top)
    dataset.save(top, [top], "scaffold")

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert sorted(report.made) == ["fit", "other"], (report.failed, report.warnings)
    ran_in = Path((root / "results/fit.txt").read_text().strip())
    assert ran_in.name == "project" and ran_in.parent.parent == root / checkout.RUNS
    assert not dataset.status(top)
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).up_to_date


def _cluster(monkeypatch: pytest.MonkeyPatch, scheduler: _Inline) -> None:
    @contextmanager
    def fake(cluster_id: str) -> Iterator[_Inline]:
        yield scheduler

    monkeypatch.setattr(engine, "cluster_for_run", fake)


def test_two_runs_at_once_each_land_what_they_make(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each run has a checkout of its own, so one started while another is
    in flight is refused nothing and shares nothing with it but the
    repository: both results are committed, and the tree ends clean."""
    inner: list[engine.MaterializeReport] = []

    class Overlapped(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            # This run's recipes have run, and nothing of it has landed.
            _cluster(monkeypatch, _Inline())
            inner.append(engine.materialize(root, ["other"], cluster_id=CLUSTER_ID))
            yield from handles

    _cluster(monkeypatch, Overlapped())

    outer = engine.materialize(root, ["fit"], cluster_id=CLUSTER_ID)

    assert outer.made == ["fit"], (outer.failed, outer.warnings)
    assert inner[0].made == ["other"], (inner[0].failed, inner[0].warnings)
    assert not dataset.status(root)
    assert _held(root) == []
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).up_to_date


def test_the_researchers_commits_during_a_run_take_nothing_from_it(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The project's branch moves on while the run is in flight. Its
    history still holds the run's commit, so each result lands on top of
    the researcher's work, and its record goes on naming the commit its
    recipe ran on."""
    started = dataset.head(root)

    class Committing(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (root / "notes.md").write_text("written while the run was in flight\n")
            dataset.save(root, [root / "notes.md"], "notes")
            yield from handles

    _cluster(monkeypatch, Committing())

    report = engine.materialize(root, ["other"], cluster_id=CLUSTER_ID)

    assert report.made == ["other"], (report.failed, report.warnings)
    assert f'"commit": "{started}"' in (root / ".astra/records/other.json").read_text()
    assert (root / "notes.md").is_file() and not dataset.status(root)


def test_a_result_does_not_land_once_the_history_no_longer_holds_the_runs_commit(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record is true where the commit it names is: rewritten away
    while the run was in flight, nothing of the run is committed, and the
    project's tree is as the researcher has it."""

    class Rewriting(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            dataset._git(["commit", "-q", "--amend", "-m", "another commit"], cwd=root)
            yield from handles

    _cluster(monkeypatch, Rewriting())

    with pytest.raises(ProjectError, match="no longer holds"):
        engine.materialize(root, ["other"], cluster_id=CLUSTER_ID)

    assert not (root / "results/other.txt").exists()
    assert not dataset.status(root)
    assert dataset._git(["log", "-1", "--format=%s"], cwd=root).strip() == "another commit"
