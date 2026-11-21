"""A run's checkout: the tree its recipes run in.

A run builds one commit, and each of its recipes has to run on that
commit's files, changed by nothing but the run's own records and results.
The project's working tree cannot promise that for the hours a run may
take: it is where the researcher works, and where another run would write.
So a run takes a tree of its own, a linked git worktree of the commit
under the project's ignored ``.lightcone/runs/``. That is on the
filesystem the project and its annex are on: the one compute nodes share,
and the one on which bytes take one more name instead of a copy. Recipes
run there. The project's tree is touched once per result, when the result
is landed in it and committed, and what the researcher edits, stages or
commits meanwhile is no part of the run.

**git-annex never runs in a checkout.** Run in a linked worktree, it fills
every annexed file it holds the bytes of, by copy, and replaces the
worktree's ``.git`` (measured). So every git command here, and every git a
recipe runs, carries :data:`dataset.PLAIN`: annexed files check out as the
stand-ins git stores, and :func:`fill` gives the ones a run reads their
bytes itself, as one more name for the object the annex already holds.

**Landing.** A result a run made is given a second name at its place in
the project's tree and committed there, by the partial commit every save
uses: git-annex runs where it always has. It stores a file that has two
names by copy (measured), so the project's file is then made a name of
that copy, which leaves one copy once the checkout is gone, as a result
made in place has. A result lands only while the project's history holds
the run's commit, which is where its record is true.

**Whose it is.** Each run takes a checkout of its own, so several runs of
one project share nothing but the repository, where git serializes their
commits. A checkout is held by a lock git keeps, which names the run's
allocation. The run that took it removes it once every task it submitted
has reported; one a run left any other way stays, since a recipe of its
may still be writing there, until its allocation has ended, which is the
one fact that says none is.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from lightcone.engine import dataset, project
from lightcone.engine.plan import COMMITS, Task
from lightcone.engine.project import ProjectError

#: Where a project's checkouts are, from the project root.
RUNS = ".lightcone/runs"


@dataclass(frozen=True)
class Checkout:
    """One run's checkout, and the project it lands results in."""

    #: The project root, in the researcher's working tree.
    project: Path
    #: The top of the checkout, a worktree of the whole repository.
    top: Path
    #: The project root inside the checkout: what a task is given as root.
    root: Path
    #: The commit checked out, which every record of the run names.
    commit: str


def take(root: Path, commit: str, allocation: str) -> Checkout:
    """Take a checkout of *commit* for a run on *allocation*.

    Args:
        root: The project root.
        commit: The commit the run builds.
        allocation: The immutable ID of the allocation the run's recipes
            execute on.

    Returns:
        The checkout, at *commit*, every annexed file a stand-in.

    Raises:
        ProjectError: If git cannot make the worktree.
    """
    from lightcone.engine import compute

    base = root / RUNS
    base.mkdir(parents=True, exist_ok=True)
    # uv reads the settings of the first project above one that has none of
    # its own, which from a checkout is the researcher's, mid-edit or not
    # (measured). An empty file of its own stands between the two.
    (base / "uv.toml").touch()
    held = _held(root)
    over = {holder for holder in set(held.values()) if compute.ended(holder)}
    for left, holder in held.items():
        # One whose folder someone removed is held for nothing.
        if holder in over or not left.exists():
            _remove(root, left)
    top = base / uuid4().hex[:12]
    # Locked as it is made, so that no other run finds it unheld.
    dataset._git(
        [*dataset.PLAIN, "worktree", "add", "-q", "--detach", "--lock", "--reason", allocation,
         str(top), commit],
        cwd=root,
    )  # fmt: skip
    # A project may sit below the top of its repository; the worktree is
    # of the whole of it.
    prefix = dataset._git(["rev-parse", "--show-prefix"], cwd=root).strip()
    tree = Checkout(project=root, top=top, root=top / prefix, commit=commit)
    # The trees of the other commits a recipe reads files at are the same
    # for every run, and ASTRA's commands name them from the project root.
    if (root / COMMITS).is_dir():
        (tree.root / COMMITS).parent.mkdir(parents=True, exist_ok=True)
        (tree.root / COMMITS).symlink_to(root / COMMITS)
    return tree


def fill(tree: Checkout, paths: Sequence[str]) -> None:
    """Give the checkout the bytes of the annexed files among *paths*.

    Each becomes one more name for the object this clone's annex keeps
    its bytes in: which object is asked of the checkout's own commit, not
    of whatever the project's tree holds by now. git is then told to take
    the file for the commit's, which it is: left to compare, it finds
    bytes where it stored a stand-in.

    A file whose bytes this clone lacks stays the stand-in it is, and the
    task that reads it refuses it.

    Args:
        tree: The run's checkout.
        paths: Files a task of the run reads, from the project root.

    Raises:
        ProjectError: If git cannot be told.
    """
    stored = dataset.objects(tree.project, [f"{tree.commit}:./{path}" for path in paths])
    filled = [path for path in paths if f"{tree.commit}:./{path}" in stored]
    for path in filled:
        _name(stored[f"{tree.commit}:./{path}"], tree.root / path)
    if filled:
        marked = project._run(
            ["git", *dataset.PLAIN, "update-index", "--assume-unchanged", "-z", "--stdin"],
            cwd=tree.root,
            stdin="".join(f"{path}\0" for path in filled),
        )
        if marked.returncode != 0:
            raise ProjectError(f"`git update-index` failed:\n{marked.stderr.strip()}")


def land(tree: Checkout, task: Task, message: str) -> None:
    """Bring one result a run made into the project's tree, and commit it.

    Args:
        tree: The run's checkout, holding what the task made.
        task: The result, as the run made it: in the checkout.
        message: The commit message.

    Raises:
        ProjectError: If the project's history no longer holds the run's
            commit, or git does not store the result as lc stores one,
            with the project's tree as it was.
    """
    if not dataset.is_ancestor(tree.project, tree.commit):
        raise ProjectError(
            f"the project's history no longer holds {tree.commit[:7]}, the commit this run "
            "builds, so its record would be true of nothing there. Check out a branch "
            "that holds it and run again"
        )
    kept = task.at(tree.project)
    # Taken to the letter, and never the folder: siblings share it.
    owned = [
        f":(literal){path.relative_to(tree.project).as_posix()}"
        for path in (*kept.paths, *kept.record_paths)
    ]
    try:
        for here, there in zip(
            (*task.paths, *task.record_paths), (*kept.paths, *kept.record_paths), strict=True
        ):
            _name(here, there)
        dataset.save(tree.project, owned, message, annexed=kept.paths, in_git=kept.record_paths)
    except BaseException:
        # An interrupt too: what lc put in the project's tree and did not
        # commit is not left for anyone to take for their own change.
        dataset.restore(tree.project, owned)
        raise
    files = {f"HEAD:./{path.relative_to(tree.project).as_posix()}": path for path in kept.paths}
    for name, stored in dataset.objects(tree.project, list(files)).items():
        if not os.path.samefile(stored, files[name]):
            _name(stored, files[name])


def leave(tree: Checkout) -> None:
    """Remove a run's checkout, once nothing of the run can be writing there.

    Args:
        tree: The checkout of a run with no task unreported.
    """
    _remove(tree.project, tree.top)


def _held(root: Path) -> dict[Path, str]:
    """Each checkout of the project a run holds → the allocation it names."""
    base = (root / RUNS).resolve()
    held = {}
    # One block per worktree: its path first, then what git knows of it.
    for block in dataset._git(["worktree", "list", "--porcelain"], cwd=root).split("\n\n"):
        known = dict(line.partition(" ")[::2] for line in block.splitlines())
        top = Path(known.get("worktree", "")).resolve()
        if top.parent == base and "locked" in known:
            held[top] = known["locked"]
    return held


def _remove(root: Path, top: Path) -> None:
    """Remove a checkout and what git keeps of it.

    Forced twice, which is git's own override for a worktree that is
    locked. git refuses a folder it no longer reads as a worktree, and
    forgets one that is gone (measured): so what is left of such a folder
    goes first.
    """
    remove = [*dataset.PLAIN, "worktree", "remove", "--force", "--force", str(top)]
    if not dataset._git_ok(remove, cwd=root):
        shutil.rmtree(top, ignore_errors=True)
        dataset._git_ok(remove, cwd=root)


def _name(source: Path, target: Path) -> None:
    """Give *target* the bytes of *source*, never absent and never partial.

    As one more name for them: a hard link made beside the target and
    renamed over it. Where the filesystem makes none — the two are not on
    one filesystem, or the file is someone else's — a copy, renamed the
    same way, as git-annex itself falls back to one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    beside = target.with_name(f".{target.name}.{uuid4().hex[:8]}")
    try:
        try:
            os.link(source, beside)
        except OSError:
            shutil.copy2(source, beside)
        os.replace(beside, target)
    finally:
        beside.unlink(missing_ok=True)
