"""The analysis, read as a graph of tasks.

What an analysis *means* is ASTRA's to say, in two answers:
``astra.api.resolve`` gives every result the analysis plans, each with
where its bytes and its record go, what each entry of its output's
``inputs`` reads, and its command as it runs; ``astra.api.status`` says
how each result's record stands. Each names the commit it was read at,
and the two must agree. None of that is re-derived here, and every path
is already measured from the project root.

A task is ASTRA's account of one result, with the few facts a run needs
looked up once: the file each entry reads and what its recipe asks for.
The checker's report is kept beside the tasks, and :func:`classify` is
what a run makes of each status in it.

lc builds the results of one analysis. One that declares others is
refused: their results would be made with recipes that run in another
folder, under names this engine does not place.

Nothing here schedules anything. ASTRA lists each result after every
result it reads, and the graph keeps that order, which is all ``--check``
needs; at execution time ordering is Dask's job.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lightcone.engine.content import Digest
from lightcone.engine.project import SPEC_FILENAME, ProjectError

if TYPE_CHECKING:
    from astra.api import Result, Status, StatusReport

#: A result's id, as ASTRA gives it — ``fit#baseline``, or ``fit`` for an
#: output built once — which is also its task's.
ResultId = str

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
    #: Each entry of the output's ``inputs``, as written → the file it
    #: reads: an input's own, or the file of the result it reads.
    inputs: dict[str, Path]
    #: ASTRA's declaration; executor support is checked only when executing.
    resources: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> ResultId:
        """The result's id, which is this task's."""
        return self.result.id

    @property
    def path(self) -> Path:
        """The single file this result is, at the place ASTRA gives it."""
        return self.root / self.result.places[0].file_path

    @property
    def record_paths(self) -> tuple[Path, ...]:
        """Where ASTRA's record of the result goes: one per place it has."""
        return tuple(self.root / place.record_path for place in self.result.places)

    @property
    def name(self) -> str:
        """The result's name — its output's id — which its files are
        named after."""
        return self.result.places[0].address.partition("#")[0]

    @property
    def command(self) -> str:
        """Its recipe's command as it runs: a shell command, run in the
        project root."""
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
        """Whether *path* is one of this result's own files: its file, at
        the place ASTRA gives it, or one of its records.

        Nothing else a recipe writes beside its result is the result's:
        no output declares it, and no record names it.

        Args:
            path: A file, from the filesystem root.

        Returns:
            True for a file this result's run writes or replaces.
        """
        return path == self.path or path in self.record_paths


@dataclass(frozen=True)
class Graph:
    """Every task a run could make, each after the tasks it reads."""

    tasks: dict[ResultId, Task]
    #: ASTRA's checker's report when the graph was read: how each result
    #: stands and where its bytes are, and where each input's are. The
    #: state a run starts from.
    checked: StatusReport
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
            pending.extend(self.tasks[key].depends_on)
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
        One task per result the analysis plans, each after the tasks it
        reads, with ASTRA's checker's report and the notes a run reports.

    Raises:
        ProjectError: If there is no analysis, ASTRA finds it invalid, it
            declares another analysis, or the repository moved between
            ASTRA's two answers.
    """
    from astra import api

    with asking_astra(root):
        analysis = api.resolve(root)
        report = api.status(root)
    if report.head != analysis.head:
        # The statuses are of another commit's results than the ones resolved.
        raise ProjectError(
            f"The repository moved from {analysis.head} to {report.head} while lc was "
            "reading the analysis. Run again."
        )
    driving = analysis.analyses[0]
    if driving.declares:
        declared = ", ".join(f"`{each}`" for each in driving.declares)
        raise ProjectError(
            f"{root / SPEC_FILENAME} declares another analysis ({declared}), and lc builds "
            "the results of a single analysis. Remove the declaration to build this one."
        )
    inputs = {each.ref: each.path for each in driving.inputs}
    outputs = {each.ref: each for each in driving.outputs}
    made_at = {result.id: result.places[0].file_path for result in analysis.results}

    tasks: dict[ResultId, Task] = {}
    for result in analysis.results:
        # Only a result of an analysis read at a commit has no command, and
        # there is no other analysis here.
        assert result.command is not None
        output = outputs[result.output]
        tasks[result.id] = Task(
            root=root,
            result=result,
            inputs={
                source.entry: root / made_at[source.result]
                if source.result is not None
                else root / inputs[source.input or ""]
                for source in result.inputs
            },
            resources=(
                {k: v for k, v in asdict(output.resources).items() if v is not None}
                if output.resources
                else {}
            ),
        )
    return Graph(
        tasks=tasks,
        checked=report,
        notes=tuple(note.message for note in analysis.notes if note.code in _NOTED),
    )


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
# commit its record names, ``unchanged`` otherwise. A runner adds one state,
# ``behind``: unchanged, but made under another environment than the
# project's. Nothing about it is wrong — its record's commit recreates that
# environment — so it is reported, and remade only when a run asks.
#
# Two things refine the checker's answer for a run. A result that is
# ``stale`` only through what it reads is unchanged again once each of
# those is made with the bytes its record gives, which only a task knows,
# once they are made (``read``). And bytes at a result's place that are
# neither its record's nor a stand-in for them (ASTRA's ``ABSENT_RESULT``)
# are bytes no clone can fetch: the result stands as the checker says, and
# a run makes it again.

#: Where a result stands: the checker's three states, and ``behind``.
State = Literal["unchanged", "behind", "stale", "missing"]


@dataclass(frozen=True)
class Verdict:
    """One result's state, and the sentence explaining it."""

    state: State
    #: Why, for every state but an ``unchanged`` result with its bytes.
    why: str = ""
    #: The bytes at its place are neither its record's nor a stand-in for
    #: them, so nothing can fetch them.
    unfetchable: bool = False
    #: Stale only through what it reads, and nothing else wrong with it:
    #: made again with the bytes its record gives, those leave it standing.
    through_reads: bool = False

    def calls_for_a_remake(self, *, refresh: bool) -> bool:
        """Whether a run would make this result again.

        ``stale`` and ``missing`` always, and a result whose bytes no clone
        can fetch; ``behind`` only when asked, because nothing about it is
        wrong.

        Args:
            refresh: Whether the caller asked for behind results too.

        Returns:
            Whether to run the recipe.
        """
        return (
            self.state in ("stale", "missing")
            or self.unfetchable
            or (refresh and self.state == "behind")
        )


def classify(
    *, found: Status, environment: str, read: Mapping[str, Digest] | None = None
) -> Verdict:
    """Decide where a result stands, from what ASTRA's checker found of it.

    The driver and a task differ by one value here, never by logic:
    ``read``, which only a task knows. Without it the checker's state is
    taken as it is, ``stale_inputs`` included.

    Args:
        found: What ASTRA's checker found of the result before the run.
        environment: The IRI of the project's environment as it now is.
        read: The bytes of each result this one reads, by entry of its
            output's ``inputs``, once a run has made or kept each of them.

    Returns:
        The state, with the reason for any but a plain ``unchanged``.
    """
    broken = [diagnostic.message for diagnostic in found.why if diagnostic.level == "error"]
    absent = [diagnostic.message for diagnostic in found.why if diagnostic.code == "ABSENT_RESULT"]
    if found.state == "missing":
        return Verdict("missing", "; ".join(dict.fromkeys(broken)))
    record = found.record
    assert record is not None  # the checker gives one wherever a result is not missing
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
    if broken or reads:
        return Verdict(
            "stale",
            "; ".join(dict.fromkeys(broken + reads + absent)),
            unfetchable=bool(absent),
            through_reads=not (broken or absent),
        )
    behind = record.generated_by.environment.iri != environment
    reasons = ["made under an earlier environment"] if behind else []
    return Verdict(
        "behind" if behind else "unchanged",
        "; ".join(reasons + absent),
        unfetchable=bool(absent),
    )
