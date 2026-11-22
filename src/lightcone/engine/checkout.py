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
it is. Its record comes with what the worker reports, so a run reads
nothing in its checkout once a task has reported. A result lands only
while the project's history holds the run's commit, which is where its
record is true.

**Whose it is.** Each run takes a checkout of its own, so several runs of
one project share nothing but the repository, where git serializes their
commits. Not two that make one result, though: each would commit its own,
and the later commit is what the branch ends with, whichever commit it
was made from. So a run says which results it is making, beside the lock
that holds its checkout, and one that would make any of them is refused
(:func:`require_free`). A checkout is held by a lock git keeps, which
names the run's allocation. The run that took it removes it once every task it submitted
has reported; one a run left any other way stays, since a recipe of its
may still be writing there, until its allocation has ended, which is the
one fact that says none is.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Iterable, Sequence
from contextlib import suppress
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

#: Where a run says which results it is making: a file of the folder git
#: keeps for its checkout, one result to a line. There as long as git
#: knows the checkout, and gone with it (measured: `git worktree prune`
#: and `git gc` leave it, `git worktree remove` takes it).
_MAKING = "lc-making"

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

    runs = held(root)
    phases = {holder: compute.phase(holder) for holder in set(runs.values())}
    unknown = []
    for left, holder in runs.items():
        if phases[holder] == "ended" or not left.exists():
            _remove(root, left)
        elif phases[holder] == "unknown":
            unknown.append(left)
    return unknown


def take(root: Path, commit: str, allocation: str, making: Iterable[str]) -> Checkout:
    """Take a checkout of *commit* for a run on *allocation*.

    The run says what it is making as it takes its checkout, and only
    then looks at what the others are: two runs that start at once each
    find the other, and both are refused, where looking first would let
    both through.

    Args:
        root: The project root.
        commit: The commit the run builds.
        allocation: The immutable ID of the allocation the run's recipes
            execute on.
        making: The results the run may make, as lc names them.

    Returns:
        The checkout, at *commit*, every annexed file a stand-in.

    Raises:
        ProjectError: If git cannot make the worktree, or another run of
            the project is making one of *making*, with no checkout left.
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
    making = sorted(making)
    try:
        _name("".join(f"{result}\n" for result in making).encode(), _folder(top) / _MAKING)
        require_free(root, making, own=top)
    except BaseException:
        _remove(root, top)
        raise
    return tree


def require_free(root: Path, results: Iterable[str], own: Path | None = None) -> None:
    """Refuse a run that would make a result another run of the project is making.

    Each would commit its own, and the later commit is what the branch
    ends with: a slow run of an earlier commit over a quick one of a
    later commit, with nothing left to say which is the newer.

    A run is making what it said it was for as long as its checkout is
    held and it has not said otherwise (:func:`withdraw`). One that was
    killed outright says nothing, so what it was making stays refused
    until its checkout goes: with its allocation, or by hand.

    Args:
        root: The project root.
        results: The results a run would make, as lc names them.
        own: The checkout of the run that asks, which is not another's.

    Raises:
        ProjectError: Naming the results, and the checkouts of the runs
            making them.
    """
    theirs: dict[str, Path] = {}
    for top in held(root):
        # A checkout whose folder is gone, or that says nothing, makes nothing.
        with suppress(OSError, ProjectError):
            if top != own:
                theirs.update(dict.fromkeys((_folder(top) / _MAKING).read_text().splitlines(), top))
    if taken := sorted(theirs.keys() & set(results)):
        named = ", ".join(f"`{result}`" for result in taken[:5])
        more = f" and {len(taken) - 5} more" if len(taken) > 5 else ""
        where = ", ".join(sorted({str(theirs[result]) for result in taken}))
        raise ProjectError(
            f"another run of the project is making {named}{more}, in {where}. Two runs "
            "that make one result each commit theirs, and the later commit is what the "
            "branch ends with, whichever commit it was made from. Wait for that run to "
            "end, or interrupt it to make them from this commit instead, then run again. "
            "If it is over and its checkout stayed, `git worktree remove --force --force "
            "<path>` removes it."
        )


def withdraw(tree: Checkout) -> None:
    """Say that a run makes nothing more, where its checkout stays.

    A run that ends with tasks unreported leaves its checkout to the
    recipes still writing there. Their results stay in it: a task whose
    run is gone moves nothing into the project.

    Args:
        tree: The checkout of a run that is ending.
    """
    with suppress(OSError, ProjectError):
        (_folder(tree.top) / _MAKING).unlink(missing_ok=True)


def fill(tree: Checkout, paths: Sequence[str]) -> None:
    """Give the checkout the bytes of what a run reads, and of nothing else.

    An annexed file among *paths* becomes one more name for the object
    this clone's annex keeps its bytes in: which object is asked of the
    checkout's own commit, not of whatever the project's tree holds by
    now. One whose bytes this clone lacks stays the stand-in it is, and
    the task that reads it refuses it by name.

    What Git LFS keeps checks out here as its pointer, and is given the
    same way, from the store Git LFS keeps in the repository, by the
    object that pointer names. A file among *paths* the commit holds
    nothing of is one git ignores, as DVC has it ignore the data it keeps:
    nothing says which bytes the commit means by it, and it is given as
    the project's tree has it.

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
    lfs = set(dataset.filtered(tree.root, "lfs", dataset.PLAIN))
    kept = annexed.keys() | lfs
    stored = dataset.located(tree.project, [annexed[path] for path in wanted & annexed.keys()])
    objects = dataset.lfs_objects(tree.project) if wanted & lfs else None
    for path in kept:
        here = tree.root / path
        if path not in wanted:
            here.unlink(missing_ok=True)
        elif annexed.get(path) in stored:
            _name(stored[annexed[path]], here)
        elif objects is not None and content.standin(here) == "Git LFS":
            oid = re.search(r"^oid sha256:([0-9a-f]{64})$", here.read_text(), re.M)
            if oid and (fetched := objects / oid[1][:2] / oid[1][2:4] / oid[1]).is_file():
                _name(fetched, here)
    for path in wanted - kept:
        here, there = tree.root / path, tree.project / path
        if not os.path.lexists(here) and there.is_file():
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


def land(
    tree: Checkout,
    task: Task,
    digest: tuple[str, int],
    records: Sequence[bytes],
    message: str,
) -> bool:
    """Commit a result a run made, where its worker left it in the project.

    Its record is given its place beside it only now, with the commit: a
    record no commit holds is one no later run can tell from a result
    made and committed. Nothing is read in the run's checkout, which
    another run removes once the allocation has ended, whether or not
    this one is still committing what its tasks reported.

    A merge, a rebase or a cherry-pick the researcher has under way in
    the project's tree is waited for: git commits nothing else until it
    is concluded.

    Args:
        tree: The run's checkout.
        task: The result, as the run made it: in the checkout.
        digest: The hash and size of the bytes its worker hashed for the
            record. The file has been in a tree other runs write in since
            then, and what git stages has to be those bytes.
        records: ASTRA's record of the result as its worker wrote it, at
            each place the result has.
        message: The commit message.

    Returns:
        False where another commit took the result and its record before
        this one could: a commit of the whole index made in the instant
        they were staged, or another run's that found them at their place.

    Raises:
        ProjectError: If the project's history no longer holds the run's
            commit, an operation under way there outlasts
            :data:`LANDING`, or git does not store the result as lc
            stores one. The project's tree is then as its last commit
            has it.
    """
    kept = task.at(tree.project)
    owned = owns(kept)
    deadline = time.monotonic() + LANDING
    try:
        while True:
            while (doing := dataset.under_way(tree.project)) and time.monotonic() < deadline:
                time.sleep(1.0)
            if doing:
                raise ProjectError(
                    f"a {doing} is under way in the project's tree, where git commits "
                    "nothing else until it is concluded. Conclude it and run again"
                )
            if not dataset.is_ancestor(tree.project, tree.commit):
                raise ProjectError(
                    f"the project's history no longer holds {tree.commit[:7]}, the commit this "
                    "run builds, so its record would be true of nothing there. Check out a "
                    "branch that holds it and run again"
                )
            try:
                for record, place in zip(records, kept.record_paths, strict=True):
                    _name(record, place)
            except OSError as e:
                raise ProjectError(f"its record could not be given its place: {e}") from e
            try:
                return dataset.save(
                    tree.project,
                    owned,
                    message,
                    annexed=kept.paths,
                    in_git=kept.record_paths,
                    named=dict.fromkeys(kept.paths, digest),
                    patience=LANDING,
                )
            except ProjectError:
                # One begun since it was asked: waited for like any other.
                if not dataset.under_way(tree.project):
                    raise
    except BaseException:
        # An interrupt too: what the run put in the project's tree and did
        # not commit is not left there for the next run to find. Whatever
        # ended the landing is what is said of it, not what putting back
        # ran into; a result and its record that stay are a true pair,
        # which the next run commits.
        with suppress(ProjectError):
            dataset.restore(tree.project, owned)
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


def held(root: Path) -> dict[Path, str]:
    """Each checkout of the project a run holds → the allocation it names.

    Args:
        root: The project root.

    Returns:
        The checkouts git keeps locked under the project's
        ``.lightcone/runs/``, by path.
    """
    base = (root / RUNS).resolve()
    found = {}
    # One block per worktree: its path first, then what git knows of it.
    for block in dataset._git(["worktree", "list", "--porcelain"], cwd=root).split("\n\n"):
        known = dict(line.partition(" ")[::2] for line in block.splitlines())
        top = Path(known.get("worktree", "")).resolve()
        if top.parent == base and "locked" in known:
            found[top] = known["locked"]
    return found


def _folder(top: Path) -> Path:
    """The folder git keeps for a checkout, in the repository."""
    asked = [*dataset.PLAIN, "rev-parse", "--absolute-git-dir"]
    return Path(dataset._git(asked, cwd=top).strip())


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


def _name(source: Path | bytes, target: Path) -> None:
    """Give *target* the bytes of *source*, never absent and never partial.

    Those of a file, as one more name for them: a hard link made beside
    the target and renamed over it. Where the filesystem makes none — the
    two are not on one filesystem, or the file is someone else's — a copy,
    renamed the same way, as git-annex itself falls back to one. Bytes
    given as they are, a record's, are written beside the target and
    renamed like the rest.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    beside = target.with_name(f".{target.name}.{uuid4().hex[:8]}")
    try:
        if isinstance(source, bytes):
            beside.write_bytes(source)
        else:
            try:
                os.link(source, beside)
            except OSError:
                shutil.copy2(source, beside)
        os.replace(beside, target)
    finally:
        beside.unlink(missing_ok=True)
