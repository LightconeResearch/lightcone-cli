"""Tests for `lightcone.engine.checkout` — the tree a run's recipes run in.

Real git and git-annex throughout: what is pinned is where bytes are, how
many names they have, and what the project's own tree looks like to git
afterwards, none of which a stub can answer.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest
from conftest import CLUSTER_ID

from lightcone.engine import assets, checkout, compute, dataset, materialize, plan
from lightcone.engine import project as project_mod
from lightcone.engine.project import ProjectError

_SPEC = """
spec_version: "0.1.0"

inputs:
  catalog: {path: data/catalog.txt}

decisions:
  method:
    options: {alpha: {}}

outputs:
  fit:
    type: metric
    format: txt
    inputs: [catalog]
    decisions: [method]
    recipe:
      command: cat {inputs.catalog} > {output} && echo {decisions.method} >> {output}

universes:
  baseline:
    decisions: {method: alpha}
"""


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_SPEC, {"data/catalog.txt": "1\n2\n", "src/fit.py": "print('fit')\n"})


def _take(root: Path) -> checkout.Checkout:
    return checkout.take(root, dataset.head(root), CLUSTER_ID)


def _born() -> int:
    """When this process started, as a lock's holder names it."""
    return int(psutil.Process().create_time())


def _stored(root: Path, path: str) -> Path:
    """The annex object behind an annexed file of the project."""
    key = dataset._git(["annex", "lookupkey", path], cwd=root).strip()
    return dataset.annex_objects(root, [key])[key]


# ---- taking one --------------------------------------------------------------


def test_a_checkout_is_the_commits_files_under_the_ignored_folder(root: Path) -> None:
    tree = _take(root)

    assert tree.root == root / ".lightcone/checkouts/0"
    assert (tree.root / "src/fit.py").read_text() == "print('fit')\n"
    assert dataset._git(["rev-parse", "HEAD"], cwd=tree.root).strip() == tree.commit
    assert not dataset.status(root)  # the project does not see it
    assert (root / ".lightcone/checkouts/0.run").read_text() == CLUSTER_ID


def test_annexed_files_check_out_as_pointers_and_git_annex_never_runs_there(root: Path) -> None:
    """Run in a checkout, git-annex would fill every file it holds the
    content of, by copy, and turn the worktree's `.git` file into a link
    git can no longer manage."""
    tree = _take(root)

    assert assets.is_pointer(tree.root / "data/catalog.txt")
    assert (tree.root / ".git").is_file()
    assert checkout.changed(tree, []) == []


def test_filling_links_the_stored_bytes_and_freezes_them(root: Path) -> None:
    """One more name for bytes already on disk — and no name they can be
    written through, so nothing changes what a recipe reads."""
    tree = _take(root)
    stored = _stored(root, "data/catalog.txt")

    checkout.fill(tree, ["data/catalog.txt", "src/fit.py"])

    assert (tree.root / "data/catalog.txt").read_text() == "1\n2\n"
    assert (tree.root / "data/catalog.txt").samefile(stored)
    assert set(tree.filled) == {"data/catalog.txt"}  # git itself carries the script
    for name in (tree.root / "data/catalog.txt", root / "data/catalog.txt"):
        with pytest.raises(PermissionError):
            name.open("a")
    assert checkout.changed(tree, []) == []
    assert not dataset.status(root)


def test_a_locked_file_is_filled_too(root: Path) -> None:
    """A link into a store the linked worktree has no path to."""
    dataset._git(["annex", "lock", "data/catalog.txt"], cwd=root)
    dataset._git(["commit", "-q", "-m", "lock the catalog"], cwd=root)
    tree = _take(root)
    assert (tree.root / "data/catalog.txt").is_symlink()
    assert not (tree.root / "data/catalog.txt").exists()

    checkout.fill(tree, ["data/catalog.txt"])

    assert (tree.root / "data/catalog.txt").read_text() == "1\n2\n"
    assert not (tree.root / "data/catalog.txt").is_symlink()


def _clone(root: Path, tmp_path: Path) -> Path:
    """A clone that holds no annexed content, with *root* as its origin."""
    # git-annex writes down where content is when it is next asked, and
    # a clone learns it from what was written when it was made.
    dataset._git(["annex", "whereis", "data/catalog.txt"], cwd=root)
    clone = tmp_path / "clone"
    dataset._git(["clone", "-q", str(root), str(clone)], cwd=tmp_path)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=clone)
    dataset.init_annex(clone)
    return clone


def test_content_this_clone_lacks_is_fetched_by_the_key_the_commit_names(
    root: Path, tmp_path: Path
) -> None:
    """lc fetches rather than telling anyone to — and by key, so it is the
    commit's content that comes, whatever the project's tree holds by now."""
    clone = _clone(root, tmp_path)
    tree = _take(clone)
    (clone / "data/catalog.txt").unlink()  # the researcher, meanwhile

    assert checkout.fill(tree, ["data/catalog.txt"]) == ""

    assert (tree.root / "data/catalog.txt").read_text() == "1\n2\n"


def test_content_that_cannot_be_had_is_left_for_the_task_to_refuse(
    root: Path, tmp_path: Path
) -> None:
    clone = _clone(root, tmp_path)
    dataset._git(["remote", "remove", "origin"], cwd=clone)
    tree = _take(clone)

    why = checkout.fill(tree, ["data/catalog.txt"])

    assert why  # git-annex's own account, for the driver to report
    assert not tree.filled
    with pytest.raises(assets.ContentNotFetchedError):
        assets.require_fetched(tree.root / "data/catalog.txt")


def test_a_kept_checkout_is_moved_to_the_next_commit(root: Path) -> None:
    """Only what differs is rewritten: what a run left there goes, what
    git ignores stays — the environment among it."""
    tree = _take(root)
    checkout.fill(tree, ["data/catalog.txt"])
    (tree.root / "results/baseline").mkdir(parents=True)
    (tree.root / "results/baseline/fit.txt").write_text("left by a run\n")
    (tree.root / ".venv").mkdir()
    (tree.root / ".venv/kept").write_text("ignored\n")
    checkout.give_back(tree)
    (root / "src/fit.py").write_text("print('fit, again')\n")
    dataset.save(root, [root / "src/fit.py"], "edit")

    again = _take(root)

    assert again.root == tree.root and again.commit != tree.commit
    assert (again.root / "src/fit.py").read_text() == "print('fit, again')\n"
    assert not (again.root / "results/baseline/fit.txt").exists()
    assert assets.is_pointer(again.root / "data/catalog.txt")
    assert (again.root / ".venv/kept").exists()
    assert (root / "data/catalog.txt").read_text() == "1\n2\n"  # its other name is untouched


def test_a_held_checkout_is_left_alone_until_its_allocation_has_ended(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that did not give its checkout back may have a recipe still
    writing there. The next run takes the next one; once the allocation
    has ended, nothing is left to write, and the slot is taken again."""
    first = _take(root)

    second = _take(root)

    assert second.root == root / ".lightcone/checkouts/1"
    checkout.give_back(second)
    monkeypatch.setattr(compute, "ended", lambda allocation: allocation == CLUSTER_ID)
    assert _take(root).root == first.root


def test_a_checkout_that_is_no_longer_a_worktree_is_made_again(root: Path) -> None:
    tree = _take(root)
    checkout.give_back(tree)
    (tree.root / ".git").unlink()

    again = _take(root)

    assert (again.root / "src/fit.py").is_file()
    assert dataset._git(["rev-parse", "HEAD"], cwd=again.root).strip() == again.commit


def test_a_take_that_fails_does_not_keep_the_slot(root: Path) -> None:
    """Nothing was handed the slot, so nothing can be writing in it."""
    with pytest.raises(ProjectError):
        checkout.take(root, "0" * 40, CLUSTER_ID)

    assert not (root / ".lightcone/checkouts/0.run").exists()
    assert _take(root).root == root / ".lightcone/checkouts/0"


def test_the_researchers_own_uv_files_are_not_a_checkouts(root: Path) -> None:
    """uv walks up from a project for a workspace and for its settings,
    and from a checkout would reach the project's own files, mid-edit or
    not. An empty one of each stands in between."""
    tree = _take(root)
    held = root / ".lightcone/checkouts"
    assert (held / "pyproject.toml").read_text() == "" and (held / "uv.toml").read_text() == ""
    (root / "pyproject.toml").write_text("this is [not toml\n")

    ran = project_mod._run(
        ["uv", "run", "--no-sync", "--project", str(tree.root), "--", "python", "-c", "print(1)"],
        cwd=tree.root,
    )

    assert ran.returncode == 0, ran.stderr


def test_giving_a_checkout_back_frees_what_the_run_left_in_it(root: Path) -> None:
    """The recipes' own files are a second copy of every landed result."""
    tree = _take(root)
    task = _made(tree)
    assert checkout.land(tree, task, "[DATALAD RUNCMD] fit#baseline") == ""

    checkout.give_back(tree)

    assert not (tree.root / "results/baseline/fit.txt").exists()
    assert (root / "results/baseline/fit.txt").read_text() == "1\n2\n"
    assert not tree.marker.exists()


def test_choosing_waits_for_another_run_and_outlives_one_that_died(root: Path) -> None:
    lock = root / ".lightcone/checkouts/choosing"
    held = lock / f"{socket.gethostname()}.{os.getpid()}.{_born()}.another-thread"
    lock.mkdir(parents=True)
    held.touch()

    def done() -> None:
        held.unlink()
        lock.rmdir()

    threading.Timer(0.3, done).start()
    began = time.monotonic()

    first = _take(root)

    assert time.monotonic() - began >= 0.25  # it waited for the other to be done
    checkout.give_back(first)

    # Left by a run that was killed while choosing: its process is gone,
    # which this host can see at once.
    lock.mkdir()
    (lock / f"{socket.gethostname()}.999999999.0.killed").touch()
    began = time.monotonic()
    assert _take(root).root == first.root
    assert time.monotonic() - began < 5


def test_a_lock_held_from_another_host_is_abandoned_only_by_its_age(root: Path) -> None:
    """This host cannot ask whether that holder is alive."""
    lock = root / ".lightcone/checkouts/choosing"
    lock.mkdir(parents=True)
    (lock / "another-login-node.4242.0.somebody").touch()
    taken: list[checkout.Checkout] = []
    waiter = threading.Thread(target=lambda: taken.append(_take(root)))
    waiter.start()
    waiter.join(timeout=1)
    assert waiter.is_alive() and not taken  # still waiting on it

    long_ago = time.time() - 2 * checkout._CHOOSING_ABANDONED
    os.utime(lock, (long_ago, long_ago))
    waiter.join(timeout=10)

    assert taken and taken[0].root == root / ".lightcone/checkouts/0"


def test_a_holder_alive_on_this_host_keeps_the_lock_however_old_it_is(root: Path) -> None:
    """A landing takes as long as storing its result does, which no age
    bounds: on this host the holder's process answers, and age never does."""
    lock = root / ".lightcone/checkouts/landing"
    lock.mkdir(parents=True)
    holder = lock / f"{socket.gethostname()}.{os.getpid()}.{_born()}.storing-a-large-result"
    holder.touch()
    long_ago = time.time() - 2 * checkout._LANDING_ABANDONED
    os.utime(lock, (long_ago, long_ago))

    def land() -> None:
        with checkout.landing(root):
            pass

    waiter = threading.Thread(target=land)
    waiter.start()
    waiter.join(timeout=1)
    assert waiter.is_alive()  # still waiting on it

    holder.unlink()
    lock.rmdir()
    waiter.join(timeout=10)
    assert not waiter.is_alive()


def test_a_pid_that_another_process_has_taken_since_holds_nothing(root: Path) -> None:
    """After a reboot, say: the pid is alive, and is not the holder's."""
    lock = root / ".lightcone/checkouts/landing"
    lock.mkdir(parents=True)
    (lock / f"{socket.gethostname()}.{os.getpid()}.{_born() - 3600}.before-a-reboot").touch()
    taken = threading.Event()

    def land() -> None:
        with checkout.landing(root):
            taken.set()

    threading.Thread(target=land, daemon=True).start()

    assert taken.wait(timeout=5)


def test_one_landing_at_a_time(root: Path) -> None:
    """Landing is several steps in the project's tree, and two runs taking
    them in turn can leave one's record beside the other's bytes."""
    order: list[str] = []

    def other() -> None:
        with checkout.landing(root):
            order.append("other")

    with checkout.landing(root):
        waiter = threading.Thread(target=other)
        waiter.start()
        waiter.join(timeout=0.5)
        assert waiter.is_alive()
        order.append("first")
    waiter.join(timeout=10)

    assert order == ["first", "other"]


def test_a_worktree_a_killed_git_left_locked_is_made_again(root: Path) -> None:
    """`git worktree add`, killed, leaves the slot registered, locked and
    missing — which `git worktree prune` skips for good."""
    tree = _take(root)
    checkout.give_back(tree)
    dataset._git(["worktree", "lock", str(tree.top)], cwd=root)
    (tree.top / ".git").unlink()

    again = _take(root)

    assert (again.root / "src/fit.py").is_file()
    assert dataset._git(["rev-parse", "HEAD"], cwd=again.root).strip() == again.commit


def test_every_slot_whose_allocation_ended_is_freed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not only the one taken: each holds its run's own copy of what it made."""
    live, killed, also_killed = _take(root), _take(root), _take(root)
    for tree in (killed, also_killed):
        tree.marker.write_text("clu_of_an_ended_allocation")
        (tree.root / "results/baseline").mkdir(parents=True)
        (tree.root / "results/baseline/fit.txt").write_text("left by a killed run\n")
    monkeypatch.setattr(compute, "ended", lambda allocation: allocation.startswith("clu_of_an"))

    taken = _take(root)

    assert taken.root == killed.root and live.marker.exists()
    assert not also_killed.marker.exists()
    assert not (also_killed.root / "results/baseline/fit.txt").exists()


def test_a_project_that_does_not_ignore_the_folder_is_refused(root: Path) -> None:
    ignore = root / ".gitignore"
    ignore.write_text(ignore.read_text().replace(".lightcone/\n", ""))
    dataset.save(root, [ignore], "stop ignoring .lightcone/")

    with pytest.raises(ProjectError, match="lc init"):
        _take(root)


def test_a_project_below_the_top_of_its_repository(
    analysis: Callable[..., Path], tmp_path: Path
) -> None:
    """The checkout is of the whole repository, as an analysis a project
    declares can be anywhere in it; the project is at its own place there."""
    built = analysis(_SPEC, {"data/catalog.txt": "1\n2\n"})
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "shared.txt").write_text("beside the project\n")
    for name in (".git", ".gitattributes"):
        os.rename(built / name, outer / name)
    root = outer / "project"
    os.rename(built, root)
    (root / ".gitattributes").write_text((outer / ".gitattributes").read_text())
    dataset._git(["add", "-A", "."], cwd=outer)
    dataset._git(["commit", "-q", "-m", "move the project below the top"], cwd=outer)

    tree = checkout.take(root, dataset.head(root), CLUSTER_ID)
    checkout.fill(tree, ["data/catalog.txt"])

    top = root / ".lightcone/checkouts/0"
    assert tree.root == top / "project"
    assert (top / "shared.txt").read_text() == "beside the project\n"
    assert (tree.root / "data/catalog.txt").read_text() == "1\n2\n"
    assert checkout.changed(tree, []) == []
    assert not dataset._git(["status", "--porcelain"], cwd=outer).strip()


# ---- what a run may change there ---------------------------------------------


def test_what_is_changed_outside_a_runs_own_writing_is_named(root: Path) -> None:
    tree = _take(root)
    checkout.fill(tree, ["data/catalog.txt"])
    (tree.root / "results/baseline").mkdir(parents=True)
    (tree.root / "results/baseline/fit.txt").write_text("a result\n")
    (tree.root / "results/baseline/scratch.tmp").write_text("beside it\n")
    (tree.root / "__pycache__").mkdir()
    (tree.root / "__pycache__/fit.pyc").write_text("ignored\n")

    assert checkout.changed(tree, ["results/baseline", ".astra/records/baseline"]) == []

    (tree.root / "src/fit.py").write_text("print('edited by a recipe')\n")
    (tree.root / "notes.md").write_text("left by a recipe\n")

    assert checkout.changed(tree, ["results/baseline", ".astra/records/baseline"]) == [
        "notes.md", "src/fit.py",
    ]


def test_a_file_the_run_was_given_to_read_has_to_stay_that_file(root: Path) -> None:
    """git reports a filled file as modified whatever it holds, so whether
    a recipe replaced one is lc's own question — unless replacing it is
    the job of a task of the run."""
    tree = _take(root)
    checkout.fill(tree, ["data/catalog.txt"])
    assert checkout.changed(tree, []) == []

    (tree.root / "data/catalog.txt").unlink()
    (tree.root / "data/catalog.txt").write_text("forged\n")

    assert checkout.changed(tree, []) == ["data/catalog.txt"]

    tree.filled["data/catalog.txt"] = None  # as `fill` marks one a task may remake
    assert checkout.changed(tree, []) == []


# ---- landing -----------------------------------------------------------------


def _made(tree: checkout.Checkout, text: str = "1\n2\n") -> plan.Task:
    """Leave in the checkout what a worker would for `fit#baseline`."""
    task = plan.build(tree.project).tasks["fit#baseline"]
    place = task.result.places[0]
    # As the worker does: what the result's name names goes first.
    for stale in (tree.root / place.file_path).parent.glob("fit.*"):
        stale.unlink()
    record = f'{{"made": "{text.strip()}"}}\n'
    for path, content in ((place.file_path, text), (place.record_path, record)):
        (tree.root / path).parent.mkdir(parents=True, exist_ok=True)
        (tree.root / path).write_text(content)
    return task


def test_landing_commits_the_result_in_the_project_and_keeps_one_copy(root: Path) -> None:
    tree = _take(root)
    task = _made(tree)

    assert checkout.land(tree, task, "[DATALAD RUNCMD] fit#baseline") == ""

    assert (root / "results/baseline/fit.txt").read_text() == "1\n2\n"
    assert (root / "results/baseline/fit.txt").samefile(_stored(root, "results/baseline/fit.txt"))
    assert (tree.root / "results/baseline/fit.txt").read_text() == "1\n2\n"  # still there to read
    committed = dataset._git(["show", "--name-only", "--format=%s", "HEAD"], cwd=root).split("\n")
    assert committed[0] == "[DATALAD RUNCMD] fit#baseline"
    assert sorted(filter(None, committed[1:])) == [
        ".astra/records/baseline/fit.json", "results/baseline/fit.txt",
    ]
    assert not dataset.status(root)


def test_landing_leaves_the_researchers_work_exactly_as_it_was(root: Path) -> None:
    """Edited, staged, untracked and committed since the run started: none
    of it is in lc's commit, and none of it is disturbed."""
    tree = _take(root)
    task = _made(tree)
    (root / "notes.md").write_text("committed meanwhile\n")
    dataset.save(root, [root / "notes.md"], "notes")
    (root / "src/fit.py").write_text("print('being edited')\n")
    (root / "src/new.py").write_text("staged = True\n")
    dataset._git(["add", "--", "src/new.py"], cwd=root)
    (root / "scratch.txt").write_text("untracked\n")
    before = dataset.status(root)

    assert checkout.land(tree, task, "[DATALAD RUNCMD] fit#baseline") == ""

    assert dataset.status(root) == before
    assert dataset._git(["diff", "--cached", "--name-only"], cwd=root).split() == ["src/new.py"]
    assert dataset._git(["log", "--format=%s", "-2"], cwd=root).splitlines() == [
        "[DATALAD RUNCMD] fit#baseline", "notes",
    ]


def test_a_payload_in_a_format_no_longer_declared_goes_with_the_landing(root: Path) -> None:
    tree = _take(root)
    assert checkout.land(tree, _made(tree), "[DATALAD RUNCMD] fit#baseline") == ""
    checkout.give_back(tree)
    spec = root / "astra.yaml"
    spec.write_text(spec.read_text().replace("format: txt", "format: csv"))
    dataset.save(root, [spec], "another format")

    tree = _take(root)
    task = _made(tree, "a,b\n")
    assert checkout.land(tree, task, "[DATALAD RUNCMD] fit#baseline") == ""

    changed = dataset._git(["show", "--name-status", "--format=", "HEAD"], cwd=root).split("\n")
    assert sorted(filter(None, changed)) == [
        "A\tresults/baseline/fit.csv",
        "D\tresults/baseline/fit.txt",
        "M\t.astra/records/baseline/fit.json",
    ]
    assert not dataset.status(root)


_STATES = [
    "another branch",
    "a detached head",
    "a history without the commit",
    "a merge",
    "a rebase",
    "a cherry-pick",
    "a revert",
]


@pytest.mark.parametrize("state", _STATES)
def test_a_result_does_not_land_where_its_record_would_not_hold(root: Path, state: str) -> None:
    """Refused with the project's tree left as it was, so it can be asked
    again once the project is back. A partial commit made during a rebase
    or a revert would succeed, onto a history about to be rewritten."""
    tree = _take(root)
    task = _made(tree)

    def git(*argv: str) -> None:
        project_mod._run(["git", *argv], cwd=root)  # some of these stop on purpose

    def side(text: str) -> None:
        """A branch whose one commit conflicts with the next one here."""
        git("checkout", "-q", "-b", "side")
        (root / "src/fit.py").write_text(f"print('{text}')\n")
        dataset.save(root, [root / "src/fit.py"], "side")
        git("checkout", "-q", "-")
        (root / "src/fit.py").write_text("print('here')\n")
        dataset.save(root, [root / "src/fit.py"], "here")

    if state == "another branch":
        git("checkout", "-q", "-b", "elsewhere")
    elif state == "a detached head":
        git("checkout", "-q", "--detach")
    elif state == "a history without the commit":
        git("commit", "-q", "--amend", "-m", "rewritten")
    elif state == "a merge":
        side("side")
        git("merge", "-q", "side")
    elif state == "a rebase":
        side("side")
        git("rebase", "-q", "side")
    elif state == "a cherry-pick":
        side("side")
        git("cherry-pick", "side")
    else:
        (root / "notes.md").write_text("to be reverted\n")
        dataset.save(root, [root / "notes.md"], "notes")
        git("revert", "--no-commit", "HEAD")
    before = dataset._git(["status", "--porcelain"], cwd=root)

    why = checkout.land(tree, task, "[DATALAD RUNCMD] fit#baseline")

    assert why, state
    assert dataset._git(["status", "--porcelain"], cwd=root) == before
    assert not (root / "results/baseline/fit.txt").exists()
    assert "RUNCMD" not in dataset._git(["log", "--all", "--format=%s"], cwd=root)


@pytest.mark.parametrize("since", ["a run of a later commit", "the researcher"])
def test_a_result_committed_since_the_runs_commit_is_left_as_it_is(
    root: Path, since: str
) -> None:
    """An older run finishing last never takes the place of what was
    committed after its commit: that is newer than what it made."""
    older = _take(root)
    task = _made(older, "older\n")
    if since == "the researcher":
        (root / "results/baseline").mkdir(parents=True)
        (root / "results/baseline/fit.txt").write_text("newer\n")
        dataset.save(root, ["results/baseline/fit.txt"], "by hand")
    else:
        (root / "notes.md").write_text("a later commit\n")
        dataset.save(root, ["notes.md"], "notes")
        newer = _take(root)
        assert checkout.land(newer, _made(newer, "newer\n"), "[DATALAD RUNCMD] newer") == ""
    head = dataset.head(root)

    why = checkout.land(older, task, "[DATALAD RUNCMD] older")

    assert "is newer than what this run made" in why
    assert (root / "results/baseline/fit.txt").read_text() == "newer\n"
    assert dataset.head(root) == head and not dataset.status(root)


def test_an_interrupt_in_the_middle_of_a_landing_leaves_nothing_of_it_behind(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between the files being linked in and their commit: what lc put in
    the project's tree is not left for the next run to call an edit."""
    tree = _take(root)
    task = _made(tree)

    def interrupted(*args: object) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(dataset, "save", interrupted)

    with pytest.raises(KeyboardInterrupt):
        checkout.land(tree, task, "[DATALAD RUNCMD] fit#baseline")

    assert not dataset.status(root)
    assert not (root / "results/baseline/fit.txt").exists()
    assert not (root / ".lightcone/checkouts/landing").exists()


def test_a_commit_git_refuses_leaves_nothing_of_the_landing_behind(root: Path) -> None:
    """Staged files survive a `git clean`, so they are unstaged first."""
    tree = _take(root)
    task = _made(tree)
    hook = root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    why = checkout.land(tree, task, "[DATALAD RUNCMD] fit#baseline")

    assert "commit" in why
    assert not dataset.status(root)
    assert not (root / "results/baseline/fit.txt").exists()


def test_a_folder_git_would_read_as_a_pattern_is_taken_to_the_letter(
    analysis: Callable[..., Path], inline: None
) -> None:
    """An analysis is free to name its folder; a pathspec is not free to
    read that name as a glob."""
    root = analysis(
        """
        spec_version: "0.1.0"
        analyses:
          stage: {path: "stage[v2]"}
        outputs:
          report:
            type: report
            format: txt
            inputs: [stage.fit]
            recipe:
              command: cat {inputs.stage.fit} > {output}
        """,
        {
            "stage[v2]/astra.yaml": """
                spec_version: "0.1.0"
                outputs:
                  fit:
                    type: metric
                    format: txt
                    recipe:
                      command: echo fit > {output}
                """,
        },
    )

    assert checkout.owned(plan.build(root).tasks["stage:fit"]) == [
        ":(glob)stage\\[v2\\]/results/fit.*",
        ":(literal)stage[v2]/.astra/records/fit.json",
    ]
    report = materialize.materialize(root, [], cluster_id=CLUSTER_ID)
    assert report.ok and sorted(report.made) == ["report", "stage:fit"]
    assert not dataset.status(root)
