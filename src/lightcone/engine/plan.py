"""The analysis, read as a graph of tasks.

What an analysis *means* is ASTRA's to say, in two answers:
``astra.api.resolve`` gives every result the analysis plans, each with
where its bytes and its record go, what each entry of its output's
``inputs`` reads, and its command as it runs; ``astra.api.status`` says
how each result's record stands. Each names the commit it was read at,
and the two must agree. None of that is re-derived here, and every path
is already measured from the project root.

A task is ASTRA's account of one result, with the few facts a run needs
looked up once: the folder its recipe runs in, the file each entry reads
and what its recipe asks for. The checker's report is kept beside the
tasks, and :func:`classify` is what a run makes of each status in it.

Every result of the working tree is a task, whichever analysis it belongs
to: the project's, one it declares by path, or one it drives. Each such
analysis is inside the project's folder, so whatever a run reads or
writes of the working tree is there, and nowhere else. A result from a
commit is no task: it is read, never built, as its record at that commit
gives it. Whatever a recipe reads at another commit, a run gives it in
that commit's tree, a clone of the repository that holds the commit
checked out at it under :data:`COMMITS`, and ASTRA's command names it
there, wherever that analysis is. Refused: an analysis read from the
working tree outside the project's folder, one in a repository of its
own, where lc could commit nothing, and a recipe that would run on a
commit's files.

Nothing here schedules anything. ASTRA lists each result after every
result it reads, and the graph keeps that order, which is all ``--check``
needs; at execution time ordering is Dask's job.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lightcone.engine import dataset
from lightcone.engine.content import Digest
from lightcone.engine.project import SPEC_FILENAME, ProjectError

if TYPE_CHECKING:
    from astra.api import Commit, Result, Source, Status, StatusReport

#: A result's id, as ASTRA gives it — ``fit#baseline``, or ``fit`` for an
#: output built once; ``stage.fit#u1`` for one of an analysis the project
#: drives, ``stage:fit#v1`` for one of an analysis it declares — which is
#: also its task's.
ResultId = str

#: Where a run gives the tree of each other commit a recipe reads a file
#: at, from the project root: ``<COMMITS>/<commit id>/`` is a clone of the
#: repository that holds the commit, checked out at it, and ASTRA names
#: each such file at its path in that tree.
COMMITS = ".lightcone/commits"

#: Of what ASTRA notes for an author, the two a run would silently live
#: with: an output nothing plans, and a command that runs code from
#: outside its analysis.
_NOTED = ("UNPLANNED_OUTPUT", "CODE_OUTSIDE_ANALYSIS")


@dataclass(frozen=True)
class Task:
    """One planned result: everything needed to make it."""

    #: The project root, which every place ASTRA gives is measured from.
    root: Path
    #: ASTRA's account of the result: its id, its places, what each entry
    #: of its output's ``inputs`` reads, and its command.
    result: Result
    #: The folder its recipe runs in: its output's analysis's, even when
    #: the project drives that analysis.
    workdir: Path
    #: Each entry of the output's ``inputs``, as written → the file it
    #: reads: an input's own, or the file of the result it reads, in the
    #: working tree or in a commit's tree under :data:`COMMITS`.
    inputs: dict[str, Path]
    #: ASTRA's declaration; executor support is checked only when executing.
    resources: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> ResultId:
        """The result's id, which is this task's."""
        return self.result.id

    @property
    def path(self) -> Path:
        """The single file this result is, at its first place: the one its
        recipe writes, and what reads it reads."""
        return self.root / self.result.places[0].file_path

    @property
    def paths(self) -> tuple[Path, ...]:
        """Its file at each place it has: a result with two names is one
        file, at two places."""
        return tuple(self.root / place.file_path for place in self.result.places)

    @property
    def record_paths(self) -> tuple[Path, ...]:
        """Where ASTRA's record of the result goes: one per place it has."""
        return tuple(self.root / place.record_path for place in self.result.places)

    @property
    def name(self) -> str:
        """Its id without the universe, which a target may give instead:
        ``fit``, ``stage.fit`` or ``stage:fit``."""
        return self.id.partition("#")[0]

    @property
    def command(self) -> str:
        """Its recipe's command as it runs: a shell command, run in
        :attr:`workdir`, which its paths are measured from."""
        assert self.result.command is not None  # `build` refuses a result with none
        return self.result.command

    @property
    def produced_by(self) -> dict[str, ResultId]:
        """The entries of ``inputs`` another task produces, and which one."""
        return {s.entry: s.result for s in self.result.inputs if s.result is not None}

    @property
    def depends_on(self) -> tuple[ResultId, ...]:
        """The tasks that must be made first, deduplicated, in order."""
        return tuple(dict.fromkeys(self.produced_by.values()))

    def owns(self, path: Path) -> bool:
        """Whether *path* is one of this result's own files: its file, or
        its record, at a place it has.

        Nothing else a recipe writes beside its result is the result's:
        no output declares it, and no record names it.

        Args:
            path: A file, from the filesystem root.

        Returns:
            True for a file this result's run writes or replaces.
        """
        return path in self.paths or path in self.record_paths


@dataclass(frozen=True)
class Graph:
    """Every task a run could make, each after the tasks it reads."""

    tasks: dict[ResultId, Task]
    #: ASTRA's checker's report when the graph was read: how each result
    #: stands and where its bytes are, and where each input's are. The
    #: state a run starts from.
    checked: StatusReport
    #: Each other commit a task reads a file at → the repository that holds
    #: it: the URL its analysis is declared with, or None for the project's
    #: own.
    commits: dict[str, str | None] = field(default_factory=dict)
    #: The file of each analysis read from the working tree, from the
    #: project root: what the commit a record names has to hold.
    specs: tuple[str, ...] = (SPEC_FILENAME,)
    #: Each input an analysis read from the working tree declares, by the
    #: name a target gives it, its id, after its analysis's key where
    #: that is not the project's own (``catalog``, ``stage.catalog``) →
    #: ASTRA's ref for it, and its file from the project root.
    inputs: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: What ASTRA notes about the analysis that a run would silently live
    #: with.
    notes: tuple[str, ...] = ()

    def closure(self, targets: list[ResultId]) -> Graph:
        """Narrow the graph to *targets* and what they depend on.

        Asking for a result asks for what it is made of; anything less
        runs a recipe against inputs that were never brought up to date.

        Args:
            targets: The ids of the results asked for.

        Returns:
            A graph holding *targets* plus their transitive dependencies,
            in the same order.
        """
        wanted: set[ResultId] = set()
        pending = list(targets)
        while pending:
            key = pending.pop()
            if key in wanted:
                continue
            wanted.add(key)
            # A result from a commit is read, never made: no task.
            pending.extend(dep for dep in self.tasks[key].depends_on if dep in self.tasks)
        return replace(self, tasks={k: t for k, t in self.tasks.items() if k in wanted})

    def resolve(self, targets: list[str]) -> list[ResultId]:
        """Turn what a user typed into result ids.

        Args:
            targets: Each a result's id (``fit#baseline``), or its name
                alone (``fit``) — matching it in every universe that
                builds it.

        Returns:
            The matching ids, in the order given.

        Raises:
            ProjectError: If a target matches nothing. Quietly making
                nothing is the least useful thing a build tool can do.
        """
        keys: list[ResultId] = []
        for target in targets:
            matched = [key for key, task in self.tasks.items() if target in (key, task.name)]
            if not matched:
                known = ", ".join(sorted(self.tasks)) or "none"
                raise ProjectError(f"no result matches `{target}`. Available: {known}")
            keys.extend(matched)
        return keys


# =============================================================================
# Building the graph
# =============================================================================


def build(root: Path) -> Graph:
    """Read a project's analysis into a graph of tasks.

    Args:
        root: The project root.

    Returns:
        One task per result of the working tree, each after the tasks it
        reads, with ASTRA's checker's report, the other commits the tasks
        read files at, and the notes a run reports.

    Raises:
        ProjectError: If there is no analysis, ASTRA finds it invalid, an
            analysis read from the working tree is outside the project's
            folder or in a repository of its own, a recipe would run on a
            commit's files, or the repository moved between ASTRA's two
            answers.
    """
    from astra import api

    with asking_astra(root):
        analysis = api.resolve(root, commits=COMMITS)
        report = api.status(root)
    if report.head != analysis.head:
        # The statuses are of another commit's results than the ones resolved.
        raise ProjectError(
            f"The repository moved from {analysis.head} to {report.head} while lc was "
            "reading the analysis. Run again."
        )
    if outside := sorted(
        each.folder
        for each in analysis.analyses
        if each.commit is None and (each.folder == ".." or each.folder.startswith("../"))
    ):
        raise ProjectError(
            f"the analysis in {outside[0]} is outside the project's folder, and lc builds "
            "and reads from the working tree only what is inside it. Declare it with a "
            "`commit` to read its results there, or move it into the project."
        )
    # A reference in ASTRA's model is an id: what it names is looked up.
    of_output = {output.ref: each for each in analysis.analyses for output in each.outputs}
    of_input = {
        input_.ref: (each, input_.path) for each in analysis.analyses for input_ in each.inputs
    }
    results = {result.id: result for result in analysis.results}

    def read(source: Source) -> tuple[str, Commit | None]:
        """The file an entry reads, and the commit it is read at, if any."""
        if source.input is not None:
            owner, path = of_input[source.input]
            return path, owner.commit
        upstream = results[source.result or ""]
        return upstream.places[0].file_path, upstream.commit

    tasks: dict[ResultId, Task] = {}
    commits: dict[str, str | None] = {}
    for result in analysis.results:
        if result.commit is not None:
            # A result from a commit is never built: what reads it reads it
            # as its record at that commit gives it (REBUILT_FROM_COMMIT).
            continue
        owner = of_output[result.output]
        if result.command is None:
            # Told where a run gives another commit's files, ASTRA gives
            # every result a command but one whose recipe runs on them.
            assert owner.commit is not None
            raise ProjectError(
                f"`{result.id}` would be made by the recipe of `{owner.key}` at "
                f"{owner.commit.id}, on that commit's files: lc builds nothing from an "
                "analysis declared with a `commit` (NOT_AT_COMMIT). Declare it by `path` "
                "alone to build it from the working tree, or read its results instead."
            )
        inputs = {}
        for source in result.inputs:
            path, commit = read(source)
            if commit is not None:
                commits[commit.id] = commit.repository
                path = f"{COMMITS}/{commit.id}/{path}"
            inputs[source.entry] = root / path
        output = next(each for each in owner.outputs if each.ref == result.output)
        tasks[result.id] = Task(
            root=root,
            result=result,
            workdir=root / owner.folder,
            inputs=inputs,
            resources=(
                {k: v for k, v in asdict(output.resources).items() if v is not None}
                if output.resources
                else {}
            ),
        )
    folders = {of_output[task.result.output].folder for task in tasks.values()} - {"."}
    top = dataset.toplevel(root)
    if apart := sorted(folder for folder in folders if dataset.toplevel(root / folder) != top):
        raise ProjectError(
            f"the analysis in {apart[0]} is a repository of its own, and lc commits "
            "results in the project's repository only. Declare it with a `commit` to read "
            "its results, or run lc in it."
        )
    return Graph(
        tasks=tasks,
        checked=report,
        commits=commits,
        specs=tuple(
            posixpath.normpath(posixpath.join(each.folder, SPEC_FILENAME))
            for each in analysis.analyses
            if each.commit is None
        ),
        inputs={
            # A ref is its analysis's key, its collection and its id.
            ".".join(filter(None, (each.key, ref.rpartition(".")[2]))): (ref, path)
            for ref, (each, path) in of_input.items()
            if each.commit is None
        },
        notes=tuple(note.message for note in analysis.notes if note.code in _NOTED),
    )


def at_commit(root: Path, path: Path) -> tuple[str, str] | None:
    """Name the commit a file a recipe reads is at, and its path there.

    Args:
        root: The project root.
        path: A file a task reads.

    Returns:
        The commit's id and the file's path in its tree, or None for a file
        of the working tree.
    """
    if not path.is_relative_to(root / COMMITS):
        return None
    commit, _, inside = path.relative_to(root / COMMITS).as_posix().partition("/")
    return commit, inside


@contextmanager
def asking_astra(root: Path) -> Iterator[None]:
    """Turn what ASTRA raises about a project into a ``ProjectError``.

    One place, because every question lc puts to ASTRA can fail the same
    ways, and each must reach the user as the same clean refusal rather
    than as another package's traceback.

    Args:
        root: The project root, for naming a missing analysis.

    Raises:
        ProjectError: If there is no analysis, ASTRA finds it invalid, or
            git or ASTRA's cache cannot be used.
    """
    from astra import api

    if not (root / SPEC_FILENAME).is_file():
        raise ProjectError(f"{root}: no {SPEC_FILENAME} — there is no analysis to materialize.")
    try:
        yield
    except api.InvalidAnalysisError as e:
        # In ASTRA's own words: one line per broken rule, placed in its file.
        listed = "\n".join(
            f"  {d.location.file}:{d.location.line or 0}:{d.location.column or 0} "
            f"{d.code} {d.message}"
            for d in e.report.diagnostics
            if d.level == "error"
        )
        raise ProjectError(
            f"{root / SPEC_FILENAME} does not validate, so there is nothing to materialize "
            f"from it:\n{listed}"
        ) from e
    except (api.NoAnalysisError, api.GitError, api.CacheError, OSError) as e:
        raise ProjectError(str(e)) from e


def declared_path(root: Path, path: Path) -> str:
    """Name *path* the way the analysis declared it: from the project root.

    Deliberately **not** resolved. Every declared input under ``data/`` is
    an annex symlink, so resolving would name
    ``.git/annex/objects/SHA256E-…`` — the storage rather than the input,
    and a path no one can fetch.

    Args:
        root: The project root.
        path: A file of the project.

    Returns:
        A POSIX path, relative to *root*.
    """
    return path.relative_to(root).as_posix()


# =============================================================================
# How a result stands — one rule, and it is the only place the rule lives
# =============================================================================
#
# ASTRA's checker says what a result is: ``missing`` where it has no valid
# record, ``stale`` where something it is made from has changed since the
# commit its record names, ``unchanged`` otherwise, which lc calls ``ok``.
# A runner adds one state, ``behind``: ok, but made under another
# environment than the project's. Nothing about it is wrong — its record's commit recreates that
# environment — so it is reported, and remade only when a run asks.
#
# lc also calls ``stale`` a result whose place holds bytes that are neither
# its record's nor a stand-in for them (ASTRA's ``ABSENT_RESULT``): written
# over by hand, or never there. The checker keeps that apart from the
# state, as a warning about the bytes, since nothing the result is made
# from has changed. To whoever reads ``lc status`` the result there is not
# the one its recipe made, and no clone can fetch the one it did, so a run
# makes it again.
#
# Whether the bytes a clone lacks can be fetched is a runner's to say, and
# lc calls ``missing`` a result made, but whose bytes neither this clone
# nor any of its remotes holds (``remotes``): a run can only make it again.
# One a remote holds stands as it is. A run fetches it where a recipe reads
# it, and nowhere else: what is current needs no compute, only a transfer,
# which ``lc fetch`` makes.
#
# A result that is ``stale`` only through what it reads is ok again
# once each of those is made with the bytes its record gives, which only a
# task knows, once they are made (``read``).

#: Where a result stands: the checker's three states, its ``unchanged``
#: called ``ok``, and ``behind``.
State = Literal["ok", "behind", "stale", "missing"]


@dataclass(frozen=True)
class Verdict:
    """One result's state, and the sentence explaining it."""

    state: State
    #: Why, for every state but ``ok``.
    why: str = ""
    #: Stale only through what it reads, and nothing else wrong with it:
    #: made again with the bytes its record gives, those leave it standing.
    through_reads: bool = False

    def calls_for_a_remake(self, *, refresh: bool) -> bool:
        """Whether a run would make this result again.

        ``stale`` and ``missing`` always; ``behind`` only when asked,
        because nothing about it is wrong.

        Args:
            refresh: Whether the caller asked for behind results too.

        Returns:
            Whether to run the recipe.
        """
        return self.state in ("stale", "missing") or (refresh and self.state == "behind")


def classify(
    *,
    found: Status,
    environment: str,
    remotes: Sequence[str] | None = None,
    read: Mapping[str, Digest] | None = None,
) -> Verdict:
    """Decide where a result stands, from what ASTRA's checker found of it.

    The driver and a task differ by one value here, never by logic:
    ``read``, which only a task knows. Without it the checker's
    ``stale_inputs`` are taken as they are.

    Args:
        found: What ASTRA's checker found of the result before the run.
        environment: The IRI of the project's environment as it now is.
        remotes: Where this clone lacks the bytes its record gives, the
            remotes that hold them (``dataset.holders``); none at all
            makes the result missing. None where nothing was asked: the
            bytes are here, or its place holds others.
        read: The bytes of each result this one reads, by entry of its
            output's ``inputs``, once a run has made or kept each of them.

    Returns:
        The state, with the reason for any but ``ok``.
    """
    broken = [diagnostic.message for diagnostic in found.why if diagnostic.level == "error"]
    absent = [diagnostic.message for diagnostic in found.why if diagnostic.code == "ABSENT_RESULT"]
    if found.state == "missing":
        return Verdict("missing", "; ".join(dict.fromkeys(broken)))
    record = found.record
    assert record is not None  # the checker gives one wherever a result is not missing
    if remotes is not None and not remotes:
        return Verdict("missing", dataset.NO_REMOTE)
    if read is not None:
        recorded = {
            item.entry: (item.content_hash, item.size) for item in record.generated_by.inputs
        }
        reads = [
            f"`{entry}` was remade with other bytes than this result read"
            for entry, digest in read.items()
            if recorded.get(entry) != digest
        ]
    else:
        reads = [f"`{entry}` has changed since this result read it" for entry in found.stale_inputs]
    if broken or reads or absent:
        return Verdict(
            "stale",
            "; ".join(dict.fromkeys(broken + reads + absent)),
            through_reads=not (broken or absent),
        )
    if record.generated_by.environment.iri != environment:
        return Verdict("behind", "made under an earlier environment")
    return Verdict("ok")
