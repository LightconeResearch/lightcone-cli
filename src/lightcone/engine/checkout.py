"""A run's checkout: the working tree its recipes run in.

A run builds one commit, and each recipe has to run in a checkout of that
commit changed by nothing but records and results. The project's own
working tree cannot promise that for the hours a run may take — it is
where the researcher works — so a run takes a checkout of its own: a
linked git worktree of the commit, under the project's ignored
``.lightcone/checkouts/``. That puts it on the filesystem the project and
its annex are on, which is the one compute nodes share and the one on
which content can arrive as hard links. Recipes run there. The project's
tree is touched once per result, when the result is landed in it and
committed, and what the researcher edits, stages or commits meanwhile is
no part of the run.

**Slots.** Checkouts are numbered, kept and reused: a run takes the
lowest one no run holds, and moving a kept checkout to another commit
rewrites only what differs, its environment included. A run holds its
slot by a marker naming its allocation, and gives it back once every
task it submitted has reported. A run that ends any other way leaves the
marker, because a recipe of its may still be writing there: the slot
stays out of use until that allocation has ended — the one fact that
proves no recipe is left — and other runs take the next slot meanwhile.
Nothing asks whether a run's recipes are alive, and no run waits on
another's.

**git-annex never runs in a checkout.** Run there, it fills every annexed
file it holds the content of, as a full copy wherever the content already
has a second name — which a result committed thin always has (measured).
So every git command here switches the annex filter and the hooks off,
annexed files check out as the pointers git stores, and lc links the few
files a run reads to the annex objects themselves: one more name for
bytes already on disk. A locked file, a symlink into a store a linked
worktree has no path to, becomes such a link too.

**Landing.** A finished result is linked into the project's tree at its
own paths and committed there by the partial commit every save uses,
one landing at a time among the runs of a project.
git-annex takes a file with two names by copy, so the project's file is
then made a link to that stored copy: one copy between the project and
the annex, as a thin add leaves, and a read-only one. The checkout keeps
the recipe's own file, for the tasks that read it, until the run gives
the slot back. A result lands only where its record will be true and
kept: on the branch the run started from, with the run's commit in its
history, and with no other git operation under way.

**Results from a commit.** A result an analysis declared with a
``commit`` holds is read, never built: a run gives its checkout the bytes
that result's record names, under ``.lightcone/commits/``, at the path
ASTRA's command for a result that reads it names. They are git-annex's,
found by the key the commit's own file names, from the project's annex
or from a clone of the other repository lc keeps beside its checkouts.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import posixpath
import random
import shutil
import socket
import stat
import time
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import psutil

from lightcone.engine import assets, dataset
from lightcone.engine.plan import Graph, Key, Task, at_commit, name
from lightcone.engine.project import ProjectError

if TYPE_CHECKING:
    from astra.api import Result, Status

#: What every git command in a checkout carries: no hooks, and no annex
#: filter. ``required`` has to be cleared with the commands, or git reads
#: a filter with none as one that failed.
_PLAIN = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "filter.annex.smudge=",
    "-c", "filter.annex.clean=",
    "-c", "filter.annex.process=",
    "-c", "filter.annex.required=false",
]  # fmt: skip

#: How old a lock held from another host has to be before it is taken
#: for abandoned: this host cannot ask whether its holder is alive.
#: Choosing a slot takes well under a second; a landing takes as long as
#: git-annex needs to store the largest result.
_CHOOSING_ABANDONED = 120.0
_LANDING_ABANDONED = 3600.0


@dataclass
class Checkout:
    """One run's checkout, and the project it lands results in."""

    #: The project root, in the researcher's working tree.
    project: Path
    #: The top of the checkout: a worktree of the whole repository.
    top: Path
    #: The project root inside the checkout: what a task is given as root.
    root: Path
    #: The commit checked out, which every record of the run names.
    commit: str
    #: The branch the project was on when the run started; empty for a
    #: detached ``HEAD``.
    branch: str
    #: What says the slot is held, and by which allocation.
    marker: Path
    #: The files :func:`fill` gave their bytes, from the project root,
    #: each with what it was when linked — or ``None`` for one a task of
    #: the run may replace with what it makes. git sees every one of them
    #: as modified, since it compares with a pointer.
    filled: dict[str, tuple[int, int, int] | None] = field(default_factory=dict)


def take(project: Path, commit: str, allocation: str) -> Checkout:
    """Take a checkout of *commit* for a run on *allocation*.

    Args:
        project: The project root.
        commit: The commit the run builds.
        allocation: The full ID of the allocation the run's recipes
            execute on.

    Returns:
        The checkout, at *commit*, holding no annexed content yet.

    Raises:
        ProjectError: If ``.lightcone/`` is not ignored, or git fails.
    """
    from lightcone.engine import compute

    base = _base(project)
    # Asked before the lock is taken: a native scheduler may take seconds
    # to answer, and choosing is what every other run waits on.
    marks = {held: _read(held) for held in base.glob("*.run")}
    ended = {named: compute.ended(named) for named in set(marks.values())}
    over = {held for held, named in marks.items() if ended[named]}
    with _exclusive(base / "choosing", _CHOOSING_ABANDONED):
        # A slot whose allocation has ended holds nothing that can still
        # be written: freed, with the copies its run left of what it made.
        for held in sorted(over):
            if _read(held) == marks[held]:
                _reset(held.with_suffix(""), "HEAD")
                held.unlink(missing_ok=True)
        slot = next(n for n in itertools.count() if not (base / f"{n}.run").exists())
        marker = base / f"{slot}.run"
        marker.write_text(allocation)
    top = base / str(slot)
    try:
        if not _reset(top, commit) or dataset.status(top, config=_PLAIN):
            # Never made, or no longer a worktree git can bring to the
            # commit: a stray file inside a submodule is one thing no
            # reset removes. Made again: all a slot holds is derived from
            # the repository.
            shutil.rmtree(top, ignore_errors=True)
            dataset._git(["worktree", "prune"], cwd=project)
            # Forced twice: git's own override for a worktree a killed
            # `add` left registered, locked and missing, which `prune`
            # skips for good.
            dataset._git(
                [*_PLAIN, "worktree", "add", "-q", "-f", "-f", "--detach", str(top), commit],
                cwd=project,
            )
            if (top / ".gitmodules").is_file():
                # A linked worktree starts with its submodules empty, and
                # a commit's checkout has their files.
                dataset._git(
                    [*_PLAIN, "submodule", "update", "-q", "--init", "--recursive"], cwd=top
                )
        # A project may sit below the top of its repository; the checkout
        # is of the whole of it, as a declared analysis can be anywhere.
        prefix = dataset._git(["rev-parse", "--show-prefix"], cwd=project).strip()
        return Checkout(
            project=project,
            top=top,
            root=top / prefix,
            commit=commit,
            branch=dataset.branch(project),
            marker=marker,
        )
    except BaseException:
        # Nothing of this run was handed the slot, so nothing can be
        # writing in it: a held one here would be held for nothing.
        marker.unlink(missing_ok=True)
        raise


def give_back(checkout: Checkout) -> None:
    """Free the slot for the next run.

    For a run with no task unreported: nothing of its can still be
    writing there. What it left goes first — the recipes' own files,
    which hold a second copy of every result until then.
    """
    _reset(checkout.top, checkout.commit)
    checkout.marker.unlink(missing_ok=True)


def _reset(top: Path, commit: str) -> bool:
    """Bring a kept worktree to *commit*'s files, ignored ones aside.

    ``clean`` is forced twice because git otherwise leaves an untracked
    repository in place — what a submodule the commit no longer has is —
    and a submodule is checked out again even at the right commit, to
    undo an edit below it.

    Returns:
        Whether git took it there. False for a folder that is no
        worktree, and for one a step failed in.
    """
    if not (top / ".git").is_file():
        return False
    steps = [["checkout", "-q", "-f", "--detach", commit], ["clean", "-q", "-ffd"]]
    if not all(dataset._git_ok([*_PLAIN, *argv], cwd=top) for argv in steps):
        return False
    update = ["submodule", "update", "-q", "--init", "--recursive", "--force"]
    return not (top / ".gitmodules").is_file() or dataset._git_ok([*_PLAIN, *update], cwd=top)


def _base(project: Path) -> Path:
    """The folder a project's checkouts are kept in, made if need be.

    Raises:
        ProjectError: If ``.lightcone/`` is not ignored.
    """
    if dataset.ignore_rule(project, ".lightcone/") is None:
        raise ProjectError(
            ".lightcone/ is not git-ignored, and a run's checkout is made there — it "
            "would show as an uncommitted change. Run `lc init` to add the rule, and "
            "commit it."
        )
    base = project / ".lightcone" / "checkouts"
    base.mkdir(parents=True, exist_ok=True)
    # uv walks up from a project: for a workspace, to the first
    # pyproject.toml above it, and for its settings, to the first uv.toml
    # or `[tool.uv]` table. From a checkout both are the researcher's own
    # files, mid-edit or not (measured). An empty one of each stands
    # between the two; a checkout's own are still found first.
    for barrier in ("pyproject.toml", "uv.toml"):
        (base / barrier).touch()
    return base


def _read(marker: Path) -> str:
    """A marker's text; empty for one that is gone."""
    try:
        return marker.read_text().strip()
    except OSError:
        return ""


@contextlib.contextmanager
def landing(project: Path) -> Iterator[None]:
    """Keep the project's tree to one run for a moment.

    Landing a result is several steps in the project's tree — its files
    linked in, added, committed, then linked to what git-annex stored —
    and two runs taking them in turn can leave a record beside another
    run's bytes (measured). A run that reads the tree to learn whether it
    is a commit's has to stay out of those steps too, or it finds another
    run's result half landed and takes it for an edit.

    Raises:
        ProjectError: If ``.lightcone/`` is not ignored.
    """
    with _exclusive(_base(project) / "landing", _LANDING_ABANDONED):
        yield


@contextlib.contextmanager
def _exclusive(lock: Path, abandoned: float) -> Iterator[None]:
    """Hold *lock* alone among the processes that share a project.

    A directory, because making one is atomic on every filesystem a
    project lives on, where ``flock`` is not — holding one file that
    names its holder. A holder that was killed leaves both: on this host
    that is seen at once, since its process is gone; from another host
    only by its age, measured against a file made now on the same
    filesystem and not against this host's clock. Whoever takes an
    abandoned lock removes the very file it judged, never the directory
    a new holder has made since, and a holder goes on only once its file
    is the one in there.
    """
    host = socket.gethostname()
    mine = f"{host}.{os.getpid()}.{uuid4().hex}"
    while True:
        try:
            lock.mkdir()
            (lock / mine).touch(exist_ok=False)
            if os.listdir(lock) == [mine]:
                break
            (lock / mine).unlink(missing_ok=True)  # made again under us: nobody's yet
        except FileExistsError:
            _free_if_abandoned(lock, host, abandoned)
        except FileNotFoundError:
            pass  # taken away between the making and the naming
        time.sleep(0.05 + 0.1 * random.random())
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            (lock / mine).unlink()
            lock.rmdir()


def _free_if_abandoned(lock: Path, host: str, abandoned: float) -> None:
    """Remove a lock whose holder is gone, and nothing else."""
    probe = lock.parent / f".now-{uuid4().hex}"
    try:
        held = os.listdir(lock)
        probe.touch()
        old = probe.stat().st_mtime - lock.stat().st_mtime > abandoned
    except OSError:
        return  # just freed, or just taken again
    finally:
        probe.unlink(missing_ok=True)

    def gone(holder: str) -> bool:
        owner, pid, _ = holder.rsplit(".", 2)
        return owner == host and not psutil.pid_exists(int(pid))

    if old or (held and all(gone(holder) for holder in held)):
        for holder in held:
            (lock / holder).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            lock.rmdir()


def fill(checkout: Checkout, paths: Sequence[str], replaceable: Collection[str] = ()) -> str:
    """Give the checkout the bytes of the stand-ins among *paths*.

    An annexed file becomes a hard link to its annex object: which one is
    read off the checkout's own file, so it is the commit's and not
    whatever the project's tree holds by now. Content this clone lacks is
    fetched first — lc fetches rather than telling anyone to. The object
    is made read-only before it is linked, as git-annex keeps every
    object no working tree holds thin, so nothing can change what a
    recipe reads by writing to another of its names.

    A Git LFS pointer is filled from the repository's own LFS store,
    which transfers nothing: lc fetches nothing from LFS.

    A file whose content cannot be had is left as checked out — a
    pointer, or a dangling link — and the task that reads it refuses it.

    Args:
        checkout: The run's checkout.
        paths: Files a task of the run reads, from the project root.
        replaceable: Those among them that a task of the run may replace
            with what it makes: a result it may be left to stand.

    Returns:
        git-annex's own account of what it could not fetch, or empty.

    Raises:
        ProjectError: If git-annex cannot be asked.
    """
    if kept := [path for path in paths if assets.lfs_pointer(checkout.root / path)]:
        dataset._git_ok([*_PLAIN, "lfs", "checkout", "--", *kept], cwd=checkout.root)
    keys = {path: key for path in paths if (key := assets.annex_key(checkout.root / path))}
    wanted = list(dict.fromkeys(keys.values()))
    unfetched = dataset.annex_get(checkout.project, wanted)
    stored = dataset.annex_objects(checkout.project, wanted)
    for path, key in keys.items():
        if key not in stored:
            continue
        _read_only(stored[key])
        _place(stored[key], checkout.root / path)
        checkout.filled[path] = (
            None if path in replaceable else assets.stamp(checkout.root / path)
        )
    return unfetched


def stale_input(found: Status) -> str:
    """Say why nothing may be built from a result from a commit.

    It is read through its record at that commit, so one ASTRA finds
    anything but unchanged is no result to build on (``STALE_INPUT``).

    Args:
        found: What ASTRA's checker found of it.

    Returns:
        Why, in ASTRA's words; empty for one a run may read.
    """
    if found.state == "unchanged" and found.record is not None:
        return ""
    said = "; ".join(dict.fromkeys(d.message for d in found.why if d.level == "error"))
    return (
        f"it is {found.state} at its commit{f' ({said})' if said else ''}, and lc builds "
        "nothing from it (STALE_INPUT)"
    )


def pin(project: Path, root: Path, graph: Graph, keys: Collection[Key]) -> dict[Key, str]:
    """Give a tree the bytes of each result from a commit its recipes read.

    Each at :func:`~lightcone.engine.plan.at_commit` under *root*: the
    bytes its record names, and nothing else. The key is read off the
    file the commit itself holds, and the content fetched where this
    clone lacks it — for a commit of the project's repository into its
    own annex, for one of another repository into lc's clone of that
    (:func:`~lightcone.engine.dataset.clone_of`). Each is a hard link to
    its annex object, made read-only as :func:`fill` makes one, and is
    hashed once against its record: a commit can hold a file its record
    does not describe.

    Args:
        project: The project root, whose repository and annex are asked.
        root: The tree whose recipes read them: the run's checkout, or
            the project's own for a rerun.
        graph: The graph, with each result from a commit its tasks read
            and what ASTRA's checker found of it.
        keys: The results from a commit to give.

    Returns:
        Why each of *keys* could not be given its bytes, or empty for one
        that was.
    """
    why: dict[Key, str] = {}
    wanted: dict[str | None, list[tuple[Result, str]]] = {}
    for key in sorted(keys):
        result = graph.pinned[key]
        assert result.commit is not None  # a result from a commit
        if refused := stale_input(graph.found[key]):
            why[key] = refused
        else:
            wanted.setdefault(result.commit.repository, []).append((result, result.commit.id))
    for repository, results in wanted.items():
        try:
            with contextlib.ExitStack() as held:
                repo = project
                if repository is not None:
                    # One fetch at a time into a clone the runs of a
                    # project share.
                    held.enter_context(_exclusive(_base(project) / "fetching", _LANDING_ABANDONED))
                    repo = dataset.clone_of(project, repository, {c for _, c in results})
                refs = [f"{commit}:{result.places[0].file_path}" for result, commit in results]
                keyed = dict(zip(refs, dataset.keys_at(repo, refs), strict=True))
                annexed = [key for key in dict.fromkeys(keyed.values()) if key]
                unfetched = dataset.annex_get(repo, annexed)
                if unfetched and repository is not None:
                    # git-annex records where content is in a journal it
                    # commits only later, so a repository can hold what it
                    # has not said it holds: it is asked directly.
                    unfetched = dataset.annex_get(repo, annexed, source="origin")
                stored = dataset.annex_objects(repo, annexed)
        except ProjectError as e:
            why |= {result.id: f"its bytes could not be read: {e}" for result, _ in results}
            continue
        for (result, commit), ref in zip(results, refs, strict=True):
            content, target = keyed[ref], root / at_commit(result)
            record = graph.found[result.id].record
            assert record is not None  # `stale_input` refuses one with none
            if not content:
                why[result.id] = (
                    f"what {commit[:7]} holds of it is not kept by git-annex, which lc fetches from"
                )
            elif content not in stored:
                why[result.id] = "its bytes could not be fetched" + (
                    f":\n{unfetched}" if unfetched else ""
                )
            else:
                _read_only(stored[content])
                _place(stored[content], target)
                why[result.id] = ""
                if assets.digest_file(target) != (record.content_hash, record.size):
                    target.unlink()
                    why[result.id] = f"what {commit[:7]} holds is not the bytes its record names"
    return why


def _read_only(file: Path) -> None:
    """Make an annex object read-only before a recipe reads it.

    An object a working tree holds thin is writable, and under one more
    name in a run's tree: written in place through any of them, the
    committed bytes are gone and the recipe reads the edit.
    """
    mode = stat.S_IMODE(file.stat().st_mode)
    if mode & 0o222:
        os.chmod(file, mode & ~0o222)


def changed(checkout: Checkout, written: Sequence[str]) -> list[str]:
    """Find what the checkout holds that its commit does not.

    Outside what a run puts there itself: the folders its results and
    records are written in, where a recipe's scratch file beside its
    output is no edit of the commit and is never landed. A file
    :func:`fill` linked counts once it is no longer that link — git
    cannot say, as it sees a pointer replaced either way. Ignored files
    do not count, and a submodule counts whenever it differs at all.

    Args:
        checkout: The run's checkout.
        written: The folders the run's tasks write in, from the project
            root.

    Returns:
        Each such path, from the project root. Empty while every recipe
        has run in the commit's own checkout.
    """
    listed = dataset.status(
        checkout.root,
        untracked_files=True,
        also=[f":(exclude){folder}" for folder in written],
        config=_PLAIN,
    )
    edited = {path for _, path in listed} - set(checkout.filled)
    for path, linked in checkout.filled.items():
        file = checkout.root / path
        if linked is not None and (not file.is_file() or assets.stamp(file) != linked):
            edited.add(path)
    return sorted(edited)


def land(checkout: Checkout, task: Task, message: str) -> str:
    """Bring one finished result into the project's tree and commit it.

    Args:
        checkout: The run's checkout, holding what the task made.
        task: The result that was made.
        message: The commit message.

    Returns:
        Empty once the result is committed in the project. Otherwise why
        it cannot be yet, with the project's tree as it was: a state of
        the project that may pass, so the caller can ask again.

    Raises:
        ProjectError: If a commit failed and what it left in the
            project's tree could not be put back.
    """
    project = checkout.project
    with landing(project):
        if why := dataset.busy(project):
            return f"{why} in the project"
        if dataset.branch(project) != checkout.branch:
            return (
                f"the project is no longer on {checkout.branch or 'the detached commit'} "
                "the run started from"
            )
        if not dataset.is_ancestor(project, checkout.commit):
            return (
                f"the project's history no longer holds {checkout.commit[:7]}, the commit "
                "the run built"
            )

        folders = {posixpath.dirname(path) for path in task.files}
        landed = []
        try:
            for folder in sorted(folders):
                made = {
                    entry.name
                    for entry in os.scandir(checkout.root / folder)
                    if entry.is_file(follow_symlinks=False)
                    and task.owns(f"{folder}/{entry.name}")
                }
                kept = project / folder
                kept.mkdir(parents=True, exist_ok=True)
                # What the result owned before and the run did not make
                # again: a payload in a format the output no longer
                # declares.
                for entry in list(os.scandir(kept)):
                    if (
                        entry.name not in made
                        and not entry.is_dir(follow_symlinks=False)
                        and task.owns(f"{folder}/{entry.name}")
                    ):
                        os.unlink(entry.path)
                for file in made:
                    _place(checkout.root / folder / file, kept / file)
                    landed.append(f"{folder}/{file}")
            dataset.save(project, owned(task), message)
        except BaseException as e:
            # An interrupt too: what lc put in the project's tree and did
            # not commit is not left for the next run to blame on anyone.
            dataset.restore(project, owned(task))
            if not isinstance(e, ProjectError):
                raise
            left = dataset._git(["status", "--porcelain", "--", *owned(task)], cwd=project)
            if left:
                raise ProjectError(
                    f"{e}\nand what that left in the project could not be put back:\n{left}"
                ) from e
            return str(e)
        # git-annex stored a copy of each file it took, since the file it
        # was given has a second name in the checkout. The project's file
        # becomes that copy.
        keys = {
            path: key.strip()
            for path in landed
            if (key := dataset._ask(["annex", "lookupkey", "--", path], cwd=project))
        }
        stored = dataset.annex_objects(project, list(dict.fromkeys(keys.values())))
        for path, key in keys.items():
            if key in stored and not os.path.samefile(project / path, stored[key]):
                _place(stored[key], project / path)
        return ""


def owned(task: Task) -> list[str]:
    """What :meth:`Task.owns` says is one result's, as git pathspecs.

    The folder is a path an analysis is free to name, so what git would
    read as a pattern in it is escaped, and a record's path is taken to
    the letter.

    Args:
        task: The result.

    Returns:
        One glob and one record path per place.
    """
    paths = []
    for place in task.result.places:
        folder = posixpath.dirname(place.file_path)
        literal = "".join(f"\\{c}" if c in "*?[]\\" else c for c in folder)
        paths += [f":(glob){literal}/{name(place)}.*", f":(literal){place.record_path}"]
    return paths


def _place(source: Path, target: Path) -> None:
    """Give *target* the bytes of *source*, never absent and never partial.

    A hard link made beside it and renamed over it; a copy renamed the
    same way where the two are on different filesystems, or the
    filesystem makes no hard links.
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
