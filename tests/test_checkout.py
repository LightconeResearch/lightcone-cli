"""A run's checkout: the tree its recipes run in.

Real git and git-annex throughout. Whether git-annex stays out of a linked
worktree, whether bytes take one more name or a copy, and whether the
project's tree ends as its commits have it are questions no stub answers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from conftest import CLUSTER_ID, _Inline

from lightcone.engine import (
    checkout,
    compute,
    container,
    content,
    dataset,
    plan,
    project,
    sandbox,
    templates,
)
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


def _object(root: Path, name: str) -> Path:
    """The object this clone's annex keeps the bytes of `<commit>:<path>` in."""
    return dataset.located(root, [dataset.keys(root, [name])[name]]).popitem()[1]


def _held(root: Path) -> list[Path]:
    """The checkouts runs hold under *root*, as git lists them."""
    return sorted(checkout.held(root))


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
    assert checkout.held(root) == {tree.top: CLUSTER_ID}
    assert not dataset.status(root)
    assert (root / "data/catalog.txt").read_text() == "1\n2\n"


def test_fill_gives_a_file_the_annexs_bytes_under_one_more_name(root: Path) -> None:
    """No copy: the file a recipe reads is the object the annex keeps. An
    annexed file no task reads is removed, since a recipe that read its
    stand-in would take a line of text for its data. git is told to take
    each for the commit's, so the checkout stays a tree changed by
    nothing."""
    tree = _taken(root)
    name = f"{tree.commit}:./data/catalog.txt"

    checkout.fill(tree, ["astra.yaml", "data/catalog.txt"])

    assert (tree.root / "data/catalog.txt").read_text() == "1\n2\n"
    assert os.path.samefile(
        tree.root / "data/catalog.txt", _object(root, name)
    )
    assert not os.path.lexists(tree.root / "data/unread.txt")
    assert dataset.status(tree.root, untracked_files=True, config=dataset.PLAIN) == []
    assert (root / "data/unread.txt").read_text() == "declared nowhere\n"


def test_fill_leaves_what_this_clone_lacks_the_bytes_of_for_its_task_to_refuse(
    root: Path,
) -> None:
    """A file a task reads whose bytes are in no object of this clone's
    annex stays the stand-in it is: the task that reads it refuses it by
    name, where a file that was simply gone would say nothing of why."""
    name = f"{dataset.head(root)}:./data/catalog.txt"
    stored = _object(root, name)
    (root / "data/catalog.txt").unlink()
    stored.parent.chmod(0o700)
    stored.unlink()
    tree = _taken(root)

    checkout.fill(tree, ["data/catalog.txt"])

    with pytest.raises(content.ContentNotFetchedError):
        content.require_fetched(tree.root / "data/catalog.txt")

    # Nor is it given from the project's tree, whatever is there by now:
    # which bytes the commit means by it is the annex's to say.
    (root / "data/catalog.txt").write_text("written since, and in no commit\n")
    checkout.fill(tree, ["data/catalog.txt"])
    with pytest.raises(content.ContentNotFetchedError):
        content.require_fetched(tree.root / "data/catalog.txt")


def test_fill_gives_a_file_git_ignores_from_the_projects_tree(root: Path) -> None:
    """What DVC keeps is a file git ignores beside a `.dvc` file git
    tracks: no commit holds it, so a checkout has none, and it is given
    from the one place it is."""
    (root / ".gitignore").write_text((root / ".gitignore").read_text() + "/data/kept.txt\n")
    dataset.save(root, [root / ".gitignore"], "ignore what DVC keeps")
    (root / "data/kept.txt").write_text("kept elsewhere\n")
    assert not dataset.status(root)
    tree = _taken(root)
    assert not (tree.root / "data/kept.txt").exists()

    checkout.fill(tree, ["data/kept.txt"])

    assert os.path.samefile(tree.root / "data/kept.txt", root / "data/kept.txt")
    assert dataset.status(tree.root, untracked_files=True, config=dataset.PLAIN) == []


def test_a_file_is_copied_where_the_filesystem_makes_no_second_name_for_it(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Across two filesystems, or for a file someone else owns, no hard
    link is made. git-annex falls back to a copy there, and so does a
    checkout: the bytes are the same, and in place whole."""
    tree = _taken(root)

    def refused(*args: object, **kwargs: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", refused)

    checkout.fill(tree, ["data/catalog.txt"])

    assert (tree.root / "data/catalog.txt").read_text() == "1\n2\n"
    assert not os.path.samefile(tree.root / "data/catalog.txt", root / "data/catalog.txt")
    assert [path.name for path in (tree.root / "data").iterdir()] == ["catalog.txt"]


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

    status({**child_env(), **dataset.plain_env({})}, touched=1_000_000_000)
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
    (UNDECLARED_READ). And whatever lc starts there, the recipe and the
    uv around it, carries the options that keep git-annex out, where what
    it starts in the project's own tree does not."""
    tree = _taken(root)
    project_runtime = container.runtime_for_run(root, build=False)
    runtime = project_runtime.at(tree.root)

    policy = container.policy_for(runtime, [])

    try:
        assert policy.grants(tree.root / "astra.yaml", policy.read)
        assert not policy.grants(root / "astra.yaml", policy.read)
        assert runtime.env.items() >= dataset.plain_env({}).items()
        assert "GIT_CONFIG_COUNT" not in project_runtime.env
    finally:
        shutil.rmtree(policy.tmp_home, ignore_errors=True)


def test_everything_a_run_starts_in_its_checkout_keeps_git_annex_out(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not the recipe alone: `uv sync` builds a packaged project, and a
    build backend that asks git for a version would start git-annex in
    the checkout as surely as a recipe's own `git status`."""
    started: dict[str, dict[str, str] | None] = {}
    run, boundary = project._run, sandbox.run

    def recorded_run(argv: list[str], **kwargs: Any) -> Any:
        if argv[:2] == ["uv", "sync"] and checkout.RUNS in str(kwargs["cwd"]):
            started["sync"] = kwargs.get("env")
        return run(argv, **kwargs)

    def recorded_boundary(*args: Any, **kwargs: Any) -> Any:
        started["recipe"] = kwargs["env"]
        return boundary(*args, **kwargs)

    monkeypatch.setattr(project, "_run", recorded_run)
    monkeypatch.setattr(sandbox, "run", recorded_boundary)

    assert engine.materialize(root, ["other"], cluster_id=CLUSTER_ID).ok

    for env in (started["sync"], started["recipe"]):
        assert env is not None and env.items() >= dataset.plain_env({}).items()


def test_a_checkout_that_declares_another_image_is_refused(
    root: Path, tmp_path: Path
) -> None:
    """The world is resolved from the project's tree, after the run has
    read its commit. Edited in between, the image a recipe would enter is
    not the one its record names: the run is refused, where nothing else
    would notice."""
    tree = tmp_path / "checkout"
    tree.mkdir()
    shutil.copy(root / ".python-version", tree)
    (tree / "pyproject.toml").write_text(
        (root / "pyproject.toml").read_text()
        + '\n[tool.lightcone.image]\nbase = "debian@sha256:' + "0" * 64 + '"\n'
    )
    runtime = container.runtime_for_run(root, build=False)

    assert runtime.at(root).repository == root / ".git"
    with pytest.raises(ProjectError, match="no longer what the commit"):
        runtime.at(tree)


def test_a_git_too_old_to_make_a_checkout_is_refused_before_anything_is_prepared(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git worktree add --lock --reason` is git 2.33's. Asked first, since
    the run would otherwise fetch, build and sync before git's usage text
    told anyone."""
    git = dataset._git
    monkeypatch.setattr(
        dataset,
        "_git",
        lambda argv, **kw: "git version 2.30.2\n" if argv == ["version"] else git(argv, **kw),
    )

    def unexpected(*args: object) -> None:
        pytest.fail("a run that cannot take a checkout contacted the cluster")

    monkeypatch.setattr(engine, "cluster_for_run", unexpected)

    with pytest.raises(ProjectError, match=r"git 2\.33 or later.*git version 2\.30\.2"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)


_UNDECLARED = """
spec_version: "0.1.0"

inputs:
  catalog: {path: data/catalog.txt}

outputs:
  fit:
    type: metric
    format: txt
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} data/unread.txt > {output}
"""


def test_a_recipe_takes_no_stand_in_for_data_it_did_not_declare(
    analysis: Callable[..., Path], inline: None
) -> None:
    """An annexed file no output declares as an input is given no bytes in
    a checkout. Left as the stand-in git stores, a recipe would read its
    one line as data and exit 0, under a valid record; it is not there,
    and the recipe fails on it by name."""
    root = analysis(
        _UNDECLARED, {"data/catalog.txt": "1\n", "data/unread.txt": "declared nowhere\n"}
    )

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.failed == ["fit"] and not report.made, report.warnings
    assert not os.path.lexists(root / "results/fit.txt")
    assert not dataset.status(root)


_KEPT_BY_DVC = """
spec_version: "0.1.0"

inputs:
  catalog: {path: data/kept.txt}

outputs:
  fit:
    type: metric
    format: txt
    inputs: [catalog]
    recipe:
      command: cat {inputs.catalog} > {output}
"""


def test_an_input_dvc_keeps_is_read_from_where_it_is(
    analysis: Callable[..., Path], inline: None
) -> None:
    """DVC keeps a file git ignores beside a `.dvc` file git tracks, and
    lc names `dvc pull` for one that is not there. Once it is, no commit
    holds it, so the run gives it to its checkout from the project's
    tree, and its record names the bytes the recipe read."""
    root = analysis(
        _KEPT_BY_DVC,
        {
            ".gitignore": templates.read("gitignore.tmpl") + "/data/kept.txt\n",
            "data/kept.txt.dvc": "outs:\n- md5: 0\n  path: kept.txt\n",
            "data/kept.txt": "kept elsewhere\n",
        },
    )
    assert not dataset.status(root)
    assert dataset._git(["ls-files", "--", "data/kept.txt"], cwd=root) == ""

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["fit"], (report.failed, report.warnings)
    assert (root / "results/fit.txt").read_text() == "kept elsewhere\n"
    assert not dataset.status(root)


_KEPT_BY_LFS = """
spec_version: "0.1.0"

inputs:
  table: {path: tables/read.bin}

outputs:
  fit:
    type: metric
    format: txt
    inputs: [table]
    recipe:
      command: cat {inputs.table} > {output}

  undeclared:
    type: metric
    format: txt
    recipe:
      command: cat tables/read.bin > {output}
"""


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs is not installed")
def test_a_checkout_asks_git_lfs_for_nothing(
    analysis: Callable[..., Path], inline: None
) -> None:
    """Checked out through its filter, every file Git LFS keeps would be
    downloaded for each run, or fail the checkout where one cannot be:
    here one was never pulled, and no remote has it. A checkout holds
    their pointers; the one a task reads is given from Git LFS's own
    store, by the object its pointer names, and the other is removed,
    like any stand-in no output declares."""
    root = analysis(_KEPT_BY_LFS, {"tables/read.bin": "read\n", "tables/unread.bin": "unread\n"})
    for argv in (
        # What `git lfs install` configures, without its hooks: git-annex has its own.
        ["config", "filter.lfs.clean", "git-lfs clean -- %f"],
        ["config", "filter.lfs.smudge", "git-lfs smudge -- %f"],
        ["config", "filter.lfs.process", "git-lfs filter-process"],
        ["config", "filter.lfs.required", "true"],
        # Where it would download from, and nothing answers there.
        ["config", "lfs.url", "http://127.0.0.1:9/"],
        ["lfs", "track", "tables/*.bin"],
        ["rm", "-q", "--cached", "tables/read.bin", "tables/unread.bin"],
        ["add", ".gitattributes", "tables"],
        ["commit", "-q", "-m", "keep the tables in Git LFS"],
    ):
        dataset._git(argv, cwd=root)
    pointer = dataset._git(["cat-file", "-p", "HEAD:tables/unread.bin"], cwd=root)
    assert pointer.startswith("version https://git-lfs.github.com/spec/v1")
    oid = pointer.split("oid sha256:")[1].split()[0]
    (root / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid).unlink()
    assert not dataset.status(root)

    assert engine.materialize(root, ["fit"], cluster_id=CLUSTER_ID).made == ["fit"]
    assert (root / "results/fit.txt").read_text() == "read\n"
    assert (root / "tables/unread.bin").read_text() == "unread\n"
    assert not dataset.status(root)

    # The same file, read by a recipe whose output does not declare it.
    report = engine.materialize(root, ["undeclared"], cluster_id=CLUSTER_ID)
    assert report.failed == ["undeclared"] and not report.made

    # The commit's bytes, whatever the project's tree holds by now.
    (root / "tables/read.bin").write_text("edited since, and in no commit\n")
    tree = _taken(root)
    checkout.fill(tree, ["tables/read.bin"])
    assert (tree.root / "tables/read.bin").read_text() == "read\n"
    checkout.leave(tree)


def test_a_task_whose_run_is_gone_leaves_its_result_in_its_checkout(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recipe goes on after the driver that submitted it is gone, and
    ends long after a later run has committed the same result. Its worker
    asks before it moves anything: the file stays where the recipe wrote
    it, and the project's tree is not touched."""
    from lightcone.engine import worker

    monkeypatch.setattr(worker, "_abandoned", lambda: True)

    report = engine.materialize(root, ["other"], cluster_id=CLUSTER_ID)

    assert report.failed == ["other"] and not report.made
    assert any("ended before it did" in w for w in report.warnings)
    assert not os.path.lexists(root / "results/other.txt") and not dataset.status(root)


def test_a_worker_knows_its_run_is_gone_from_its_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dask tells a worker that nothing holds a task's result any more
    while the task runs on, since it cannot stop it. Outside a Dask
    worker, where the rerun entry point and an in-thread scheduler run a
    task, there is no run to outlive."""
    import distributed

    from lightcone.engine import worker

    assert worker._abandoned() is False

    class _Worker:
        def __init__(self, state: str | None) -> None:
            tasks = {} if state is None else {"lc-key": MagicMock(state=state)}
            self.state = MagicMock(tasks=tasks)

        def get_current_task(self) -> str:
            return "lc-key"

    for state, gone in (("executing", False), ("cancelled", True), (None, True)):
        monkeypatch.setattr(distributed, "get_worker", lambda state=state: _Worker(state))
        assert worker._abandoned() is gone


def test_a_record_that_cannot_be_given_its_place_is_a_refusal(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk, or a folder that cannot be written: the result is not
    committed, the project's tree is put back, and the run says so as it
    says any refusal."""
    name = checkout._name

    def full(source: Path | bytes, target: Path) -> None:
        if ".astra" in target.parts and checkout.RUNS not in target.as_posix():
            raise OSError(28, "No space left on device")
        name(source, target)

    monkeypatch.setattr(checkout, "_name", full)

    with pytest.raises(ProjectError, match="its record could not be given its place"):
        engine.materialize(root, ["other"], cluster_id=CLUSTER_ID)

    assert not os.path.lexists(root / "results/other.txt") and not dataset.status(root)


def test_a_run_says_which_submodules_its_checkout_holds_no_file_of(
    root: Path, tmp_path: Path, inline: None
) -> None:
    """git checks out no submodule in a linked worktree. A recipe that
    reads or runs something there finds an empty folder, so a run names
    each, where the recipe's own error would name none."""
    library = tmp_path / "library"
    library.mkdir()
    for argv in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        dataset._git(argv, cwd=library)
    (library / "tool.py").write_text("print(1)\n")
    dataset._git(["add", "."], cwd=library)
    dataset._git(["commit", "-q", "-m", "a library"], cwd=library)
    dataset._git(
        ["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library), "vendor/lib"],
        cwd=root,
    )
    dataset._git(["commit", "-q", "-m", "vendor the library"], cwd=root)

    report = engine.materialize(root, ["other"], cluster_id=CLUSTER_ID)

    assert report.made == ["other"], (report.failed, report.warnings)
    assert any("submodule" in w and "vendor/lib" in w for w in report.warnings)


# ---- whose it is -----------------------------------------------------------


def test_a_checkout_stays_until_its_allocation_has_ended(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that ended without removing its checkout may have left a
    recipe writing there. Nothing asks whether it did: the checkout goes
    when the allocation it names has ended, and another's stays. One whose
    allocation cannot be asked about stays too, and is named, since
    nothing else ever will."""
    first, second, third = _taken(root), _taken(root, _ELSEWHERE), _taken(root)
    monkeypatch.setattr(compute, "phase", lambda allocation: "active")
    assert checkout.reap(root) == []
    assert _held(root) == sorted([first.top, second.top, third.top])

    monkeypatch.setattr(
        compute, "phase", lambda allocation: "ended" if allocation == CLUSTER_ID else "unknown"
    )

    assert checkout.reap(root) == [second.top]
    assert _held(root) == [second.top]
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

    assert checkout.reap(root) == [kept.top]
    assert _held(root) == [kept.top]


# ---- landing ---------------------------------------------------------------


def test_a_result_is_written_once_and_the_tree_ends_as_its_commits_have_it(
    root: Path, inline: None
) -> None:
    """The file its recipe wrote in the checkout is the one the project
    holds, moved there, and the one the annex keeps, under a second name:
    no copy was made of it, it is as writable as one made in place, and
    nothing of the run is left, no checkout and no change."""
    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert sorted(report.made) == ["fit", "other"], (report.failed, report.warnings)
    assert (root / "results/fit.txt").read_text() == "1\n2\n"
    made, name = root / "results/other.txt", "HEAD:./results/other.txt"
    assert os.path.samefile(made, _object(root, name))
    assert made.stat().st_nlink == 2 and os.access(made, os.W_OK)
    assert not dataset.status(root)
    assert _held(root) == []
    assert [path.name for path in (root / checkout.RUNS).iterdir()] == ["uv.toml"]
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).up_to_date


def test_a_result_is_in_the_project_before_what_reads_it_starts(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker moves the file its recipe made to its place in the
    project before it says the result exists, and leaves a link where the
    recipe wrote it: a task that reads it finds it there, and the bytes
    were written once."""
    seen: dict[str, Any] = {}

    class Looking(_Inline):
        def submit(
            self, fn: Callable[..., Any], *args: Any, key: str, resources: dict[str, float],
        ) -> Any:
            outcome = fn(*args)
            task = args[1]
            seen[key] = (task.path.is_symlink(), task.path.resolve(), outcome.stamp)
            return outcome

    _cluster(monkeypatch, Looking())

    assert engine.materialize(root, ["other"], cluster_id=CLUSTER_ID).made == ["other"]

    linked, target, stamp = seen["other"]
    assert linked and target == root / "results/other.txt"
    assert stamp == content.stamp(root / "results/other.txt")


def test_a_landing_waits_for_the_projects_index_as_long_as_another_run_holds_it(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another run of the project holds the index for as long as
    git-annex takes to hash the result it lands, which is no minute's
    work for a large one. A landing waits for that, where any other git
    command gives up: a result made is at stake."""
    monkeypatch.setattr(dataset, "_PATIENCE", 0.2)
    lock = root / ".git" / "index.lock"

    class Held(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            lock.write_text("")
            threading.Timer(1.5, lock.unlink).start()
            yield from handles

    _cluster(monkeypatch, Held())

    report = engine.materialize(root, ["other"], cluster_id=CLUSTER_ID)

    assert report.made == ["other"], (report.failed, report.warnings)
    assert not dataset.status(root)


def _merging(root: Path) -> None:
    """Leave a merge stopped for its conflict in the project's tree, on a
    commit that follows the one it was at."""
    for argv in (
        ["checkout", "-q", "-b", "theirs"],
        ["commit", "-q", "--allow-empty", "-m", "theirs"],
        ["checkout", "-q", "-"],
    ):
        dataset._git(argv, cwd=root)
    (root / "notes.txt").write_text("ours\n")
    dataset.save(root, [root / "notes.txt"], "ours")
    dataset._git(["checkout", "-q", "theirs"], cwd=root)
    (root / "notes.txt").write_text("theirs\n")
    dataset.save(root, [root / "notes.txt"], "theirs, on the same file")
    dataset._git(["checkout", "-q", "-"], cwd=root)
    assert not dataset._git_ok(["merge", "-q", "theirs"], cwd=root)
    assert dataset.under_way(root) == "merge"


def test_a_landing_waits_for_a_merge_under_way_in_the_projects_tree(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git commits nothing else in a tree while a merge is under way
    there, and a rebase would take the commit with it if abandoned. The
    researcher is in the middle of something: the result waits for them
    to conclude it, and is committed then."""

    class Merging(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            _merging(root)
            threading.Timer(
                1.5, lambda: dataset._git(["merge", "--abort"], cwd=root)
            ).start()
            yield from handles

    _cluster(monkeypatch, Merging())

    report = engine.materialize(root, ["other"], cluster_id=CLUSTER_ID)

    assert report.made == ["other"], (report.failed, report.warnings)
    assert dataset.under_way(root) is None and not dataset.status(root)


def test_a_merge_that_outlasts_a_landing_ends_the_run_and_takes_nothing_of_it(
    root: Path, inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the time a landing waits, the run says what it waited for.
    Here the merge began as the result was being committed, so git had
    staged it before it refused the commit: nothing of the result is left
    in the merge the researcher goes on with, at its place or staged."""
    monkeypatch.setattr(checkout, "LANDING", 0.2)
    save = dataset.save
    begun: list[bool] = []

    def as_a_merge_begins(*args: Any, **kwargs: Any) -> bool:
        if not begun:
            begun.append(True)
            _merging(root)
        return save(*args, **kwargs)

    monkeypatch.setattr(dataset, "save", as_a_merge_begins)

    with pytest.raises(ProjectError, match="a merge is under way"):
        engine.materialize(root, ["other"], cluster_id=CLUSTER_ID)

    assert not os.path.lexists(root / "results/other.txt")
    assert "results" not in dataset._git(["diff", "--cached", "--name-only"], cwd=root)
    dataset._git(["merge", "--abort"], cwd=root)
    assert not dataset.status(root)


def test_a_run_does_not_start_in_a_tree_with_a_merge_under_way(root: Path, inline: None) -> None:
    """Every result it made would wait for the merge to be concluded: said
    before anything runs."""
    _merging(root)

    with pytest.raises(ProjectError, match="a merge is under way"):
        engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert _held(root) == []


def test_a_run_commits_what_its_tasks_reported_once_its_checkout_is_gone(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout goes once its allocation has ended, removed by whichever
    run finds it, and the run that took it may still be committing what
    its tasks reported. It needs nothing there: the file is in the
    project's tree, and its record came with the report."""

    class Reaped(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (top,) = checkout.held(root)
            checkout._remove(root, top)
            assert not top.exists()
            yield from handles

    _cluster(monkeypatch, Reaped())

    report = engine.materialize(root, [], cluster_id=CLUSTER_ID)

    assert report.made == ["fit", "other"], (report.failed, report.warnings)
    assert (root / "results/fit.txt").read_text() == "1\n2\n"
    assert not dataset.status(root) and _held(root) == []
    assert engine.check(root, []).up_to_date


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


_CHAIN = """
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

  again:
    type: metric
    format: txt
    inputs: [fit]
    recipe:
      command: cat {inputs.fit} > {output}
"""


def test_a_plan_that_is_not_of_the_checkouts_commit_is_refused(
    analysis: Callable[..., Path], inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan is read from the project's tree, and the checkout is of
    the commit the project is at when it is taken: another run may commit
    a result in between. A result the run leaves alone is read as its
    record in the plan gives it, so where the checkout holds another one
    the run does not start: each record it wrote would name bytes no
    recipe read."""
    root = analysis(_CHAIN, {"data/catalog.txt": "1\n"})
    assert engine.materialize(root, [], cluster_id=CLUSTER_ID).ok
    spec = root / "astra.yaml"
    spec.write_text(spec.read_text().replace("cat {inputs.fit}", "cat -u {inputs.fit}"))
    dataset.save(root, [spec], "another recipe for what reads the fit")
    build = plan.build

    def another_run_commits_the_fit_meanwhile(path: Path) -> plan.Graph:
        graph = build(path)
        record = root / ".astra/records/fit.json"
        made = json.loads(record.read_text())
        record.write_text(json.dumps({**made, "size": made["size"] + 1}))
        dataset.save(root, [record], "the fit, as another run made it")
        return graph

    monkeypatch.setattr(plan, "build", another_run_commits_the_fit_meanwhile)

    with pytest.raises(ProjectError, match="another run committed it meanwhile"):
        engine.materialize(root, ["again"], cluster_id=CLUSTER_ID)

    assert not dataset.status(root) and _held(root) == []


def test_a_run_is_of_the_commit_the_project_is_at_when_its_checkout_is_taken(
    analysis: Callable[..., Path], inline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between reading the plan and taking the checkout, the image
    converge may commit an archive, and the researcher anything. The run
    is of the later commit, which its records name, where that commit
    holds the analysis, the environment and the inputs the plan was made
    from; where one of them was committed anew, the plan is of nothing,
    and the run does not start."""
    root = analysis(_CHAIN, {"data/catalog.txt": "1\n"})
    resolve = container.runtime_for_run

    def committing(change: str) -> Callable[..., container.Runtime]:
        def resolved(*args: Any, **kwargs: Any) -> container.Runtime:
            (root / change).write_text("committed while the run was preparing\n")
            dataset.save(root, [root / change], "meanwhile")
            return resolve(*args, **kwargs)

        return resolved

    monkeypatch.setattr(container, "runtime_for_run", committing("notes.md"))
    assert engine.materialize(root, ["fit"], cluster_id=CLUSTER_ID).made == ["fit"]
    record = json.loads((root / ".astra/records/fit.json").read_text())
    named = record["generated_by"]["commit"]
    assert dataset._git(["log", "-1", "--format=%s", named], cwd=root).strip() == "meanwhile"

    monkeypatch.setattr(container, "runtime_for_run", committing("data/catalog.txt"))
    with pytest.raises(ProjectError, match="committed anew"):
        engine.materialize(root, ["again"], cluster_id=CLUSTER_ID)
    assert _held(root) == []


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

    assert not os.path.lexists(root / "results/other.txt")
    assert not dataset.status(root)
    assert dataset._git(["log", "-1", "--format=%s"], cwd=root).strip() == "another commit"
