"""A run's checkout: the tree its recipes run in.

A run builds one commit, and each of its recipes has to run on that
commit's files, changed by nothing but the run's own records and results.
The project's working tree cannot promise that for the hours a run may
take: it is where the researcher works, and where another run would write.
So a run takes a tree of its own, a linked git worktree of the commit
under the project's ignored ``.lightcone/runs/``. That is on the
filesystem the project and its annex are on: the one compute nodes share,
and the one on which a file changes place without its bytes being
written again. Recipes run there, and what the researcher edits, stages
or commits meanwhile is no part of the run.

**git-annex never runs in a checkout, nor does Git LFS.** Run in a linked
worktree, git-annex fills every annexed file it holds the bytes of, by
copy, and replaces the worktree's ``.git``; Git LFS downloads every file
it keeps, or fails the checkout (both measured). So every git command
here, and whatever lc starts in a checkout, carries :data:`dataset.PLAIN`.
What either tool keeps then checks out as the stand-in git stores, and
:func:`fill` gives the ones a run reads their bytes itself: an annexed
file as one more name for the object the annex already holds. The others
it removes: a recipe that read one would take a line of text for its
data.

**A result is written once.** Its recipe writes it in the checkout, where
no other run and no commit sees it half made. The worker then moves it to
its place in the project's tree (:func:`hand_over`), which costs a rename
whatever its size, and leaves a link in its stead for the tasks that read
it. The driver commits it there (:func:`land`) as it would one made in
place: git-annex takes a file that has one name as its own object, without
a copy, and git takes the file for unchanged because it was added where
it is. A result lands only while the project's history holds the run's
commit, which is where its record is true.

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
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from lightcone.engine import content, dataset, project
from lightcone.engine.plan import COMMITS, Task
from lightcone.engine.project import ProjectError

#: Where a project's checkouts are, from the project root.
RUNS = ".lightcone/runs"

#: The git a run needs: ``worktree add --lock --reason`` is 2.33's, and
#: configuration given in the environment 2.31's.
_GIT = (2, 33)

#: How long a landing waits for the project's index, in seconds. Another
#: run of the project holds it for as long as git-annex takes to hash the
#: result it lands (measured: 7 s per GiB), and a result made is at
#: stake. A lock a killed git left is found before a run starts
#: (``dataset.require_free_index``).
LANDING = 3600.0


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


def require_git(root: Path) -> None:
    """Refuse a git too old to make a checkout, before anything is prepared.

    Args:
        root: The project root.

    Raises:
        ProjectError: Naming the version a run needs, and the one found.
    """
    found = dataset._git(["version"], cwd=root).strip()
    if tuple(int(part) for part in re.findall(r"\d+", found)[:2]) < _GIT:
        raise ProjectError(
            f"a run takes a checkout of its commit, which needs git "
            f"{'.'.join(map(str, _GIT))} or later, and this is {found}."
        )


def reap(root: Path) -> list[Path]:
    """Remove the checkouts no run can be using any more.

    One whose allocation has ended, and one whose folder someone removed.
    Unknown is never ended: a checkout whose allocation cannot be asked
    about stays, and is named, since nothing else ever will.

    Args:
        root: The project root.

    Returns:
        The checkouts kept for not knowing whether their runs are over.
    """
    from lightcone.engine import compute

    held = _held(root)
    phases = {holder: compute.phase(holder) for holder in set(held.values())}
    unknown = []
    for left, holder in held.items():
        if phases[holder] == "ended" or not left.exists():
            _remove(root, left)
        elif phases[holder] == "unknown":
            unknown.append(left)
    return unknown


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
    base = root / RUNS
    base.mkdir(parents=True, exist_ok=True)
    # uv reads the settings of the first project above one that has none of
    # its own, which from a checkout is the researcher's, mid-edit or not
    # (measured). An empty file of its own stands between the two.
    (base / "uv.toml").touch()
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
    """Give the checkout the bytes of what a run reads, and of nothing else.

    An annexed file among *paths* becomes one more name for the object
    this clone's annex keeps its bytes in: which object is asked of the
    checkout's own commit, not of whatever the project's tree holds by
    now. One whose bytes this clone lacks stays the stand-in it is, and
    the task that reads it refuses it by name.

    A file among *paths* that another tool keeps is given from the
    project's tree, where that tool put its bytes: what Git LFS keeps,
    which checks out here as its pointer, and what DVC keeps, which git
    ignores, so that the commit holds no file of it.

    Every other file a tool keeps is removed. No output declares it as an
    input, and left as a stand-in it would be read all the same: a line of
    text, for data, by a recipe that then exits 0.

    git is then told to take each for the commit's: left to compare, it
    finds bytes, or nothing, where it stored a stand-in.

    Args:
        tree: The run's checkout.
        paths: Files a task of the run reads, from the project root.

    Raises:
        ProjectError: If git or git-annex cannot be asked, or git told.
    """
    wanted = set(paths)
    # Listed from the top of the repository, which the project may sit below.
    below = tree.root.relative_to(tree.top).as_posix()
    inside = "" if below == "." else f"{below}/"
    annexed = {
        path.removeprefix(inside): key
        for path, key in dataset.annexed(tree.project, tree.commit).items()
        if path.startswith(inside)
    }
    kept = annexed.keys() | set(dataset.filtered(tree.root, "lfs", dataset.PLAIN))
    stored = dataset.located(tree.project, [annexed[path] for path in wanted & annexed.keys()])
    for path in kept:
        if path not in wanted:
            (tree.root / path).unlink(missing_ok=True)
        elif annexed.get(path) in stored:
            _name(stored[annexed[path]], tree.root / path)
    for path in wanted:
        here, there = tree.root / path, tree.project / path
        if (
            (not os.path.lexists(here) or content.standin(here))
            and there.is_file()
            and not content.standin(there)
        ):
            _name(there, here)
    if kept:
        marked = project._run(
            ["git", *dataset.PLAIN, "update-index", "--assume-unchanged", "-z", "--stdin"],
            cwd=tree.root,
            stdin="".join(f"{path}\0" for path in sorted(kept)),
        )
        if marked.returncode != 0:
            raise ProjectError(f"`git update-index` failed:\n{marked.stderr.strip()}")


def hand_over(task: Task, root: Path) -> None:
    """Move the file a recipe made in a checkout to its place in the project.

    It is given its name there, then loses the one it had: on the one
    filesystem the two are on, the bytes are written once, by the recipe.
    The file is then at its place under one name, which is what lets
    git-annex take it as its own object when the driver commits it there.
    A link is left at each place it had in the checkout, for the tasks of
    the run that read it.

    For the worker, which starts no git: called once its recipe has ended
    and its record is written, and before anything is told the result
    exists.

    Args:
        task: The result, as the run made it: in the checkout.
        root: The project root, in the researcher's tree.

    Raises:
        OSError: If a file cannot be moved there.
    """
    for made, place in zip(task.paths, task.at(root).paths, strict=True):
        _name(made, place)
        made.unlink()
        os.symlink(place, made)


def land(tree: Checkout, task: Task, digest: tuple[str, int], message: str) -> None:
    """Commit a result a run made, where its worker left it in the project.

    Its record is given its place beside it only now, with the commit: a
    record no commit holds is one no later run can tell from a result
    made and committed.

    Args:
        tree: The run's checkout.
        task: The result, as the run made it: in the checkout.
        digest: The hash and size of the bytes its worker hashed for the
            record. The file has been in a tree other runs write in since
            then, and what git stages has to be those bytes.
        message: The commit message.

    Raises:
        ProjectError: If the project's history no longer holds the run's
            commit, or git does not store the result as lc stores one,
            with the project's tree as its last commit has it.
    """
    kept = task.at(tree.project)
    owned = owns(kept)
    try:
        if not dataset.is_ancestor(tree.project, tree.commit):
            raise ProjectError(
                f"the project's history no longer holds {tree.commit[:7]}, the commit this "
                "run builds, so its record would be true of nothing there. Check out a "
                "branch that holds it and run again"
            )
        try:
            for made, place in zip(task.record_paths, kept.record_paths, strict=True):
                _name(made, place)
        except OSError as e:
            raise ProjectError(f"its record could not be given its place: {e}") from e
        dataset.save(
            tree.project,
            owned,
            message,
            annexed=kept.paths,
            in_git=kept.record_paths,
            named=dict.fromkeys(kept.paths, digest),
            patience=LANDING,
        )
    except BaseException:
        # An interrupt too: what the run put in the project's tree and did
        # not commit is not left there for the next run to find.
        dataset.restore(tree.project, owned, patience=LANDING)
        raise


def owns(task: Task) -> list[str]:
    """Every path one result owns, as git pathspecs taken to the letter:
    its file and its records, from the project root.

    Never the parent directory: siblings share it.
    """
    return [
        f":(literal){path.relative_to(task.root).as_posix()}"
        for path in (*task.paths, *task.record_paths)
    ]


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
