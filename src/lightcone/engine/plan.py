"""The run's plan: every result there is to make, and what making it needs.

What an analysis *means* is ASTRA's to say, in two answers:
``astra.api.resolve`` gives the analysis — its results, each with where
its bytes and its record go, what it reads and its command as it runs —
and ``astra.api.status`` says how each result's record stands. Each names
the commit it was read at, and the two must agree. None of that is
re-derived here, and every path is already measured from the project
root.

What to *do* about it is lc's. This module turns each planned result lc
builds into a task: ASTRA's own account of it, with the few facts a run
needs looked up once — the folder its recipe runs in, the file each entry
reads, the options it is made with, what its recipe asks for — so that
one task crosses to a worker by itself. It refuses what this engine
cannot execute. Which tasks a run actually makes is decided from what the
checker found, kept beside them.

Nothing here schedules anything. ASTRA lists each result after every
result it reads, and that order is kept; at execution time ordering is
Dask's job.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lightcone.engine.project import SPEC_FILENAME, ProjectError

if TYPE_CHECKING:
    from astra.api import Analysis, Diagnostic, Place, Result, Source, Status

#: A task's identity within a run: its result's id, as ASTRA gives it —
#: its address, after the key of the analysis the result belongs to and a
#: colon when that is not the project's own (``fit#baseline``,
#: ``stage:fit#v1``).
Key = str

#: Of what ASTRA notes for an author, the two a run would silently live
#: with: an output nothing plans, and a command that runs code from
#: outside its analysis. A pin that falls back and an output a switch
#: leaves unbuilt are how an analysis is written, not something to repeat
#: at every run.
_NOTED = ("UNPLANNED_OUTPUT", "CODE_OUTSIDE_ANALYSIS")


@dataclass(frozen=True)
class Task:
    """One planned result: everything needed to make it."""

    #: ASTRA's account of the result: its id, where its record and bytes
    #: go, one place per name it has, what each entry of its output's
    #: ``inputs`` reads, and its command. The recipe writes the first
    #: place; the others receive copies.
    result: Result
    #: The folder the recipe runs in, from the project root: its output's
    #: analysis's.
    workdir: str
    #: Each entry of the output's ``inputs``, as written and in order → the
    #: file it reads, from the project root.
    reads: dict[str, str]
    #: Each decision the result is made with, by ASTRA's ref → the option
    #: its universe selects.
    decisions: dict[str, str] = field(default_factory=dict)
    #: ASTRA's declaration; executor support is checked only when executing.
    resources: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> Key:
        """The task's identity: its result's id."""
        return self.result.id

    @property
    def command(self) -> str:
        """The recipe as it runs — a shell command, its paths relative to
        ``workdir``."""
        assert self.result.command is not None  # `build` refuses a result with none
        return self.result.command

    @property
    def depends_on(self) -> tuple[Key, ...]:
        """The tasks that must be made first, deduplicated, in order."""
        read = (source.result for source in self.result.inputs)
        return tuple(dict.fromkeys(key for key in read if key is not None))


@dataclass(frozen=True)
class Graph:
    """Every task a run could make, each after the tasks it reads."""

    tasks: dict[Key, Task]
    #: What ASTRA's checker found of each task's result when the graph was
    #: read. The state a run starts from.
    found: dict[Key, Status]
    #: What ASTRA notes about the analysis that a run would silently live
    #: with.
    notes: tuple[str, ...] = ()
    #: The analysis's ``label`` and ``description``, as its file writes
    #: them: what a publication of its results is titled with.
    label: str = ""
    description: str = ""

    def closure(self, targets: list[Key]) -> Graph:
        """Narrow the graph to *targets* and what they depend on.

        Asking for an output asks for what it is made of; anything less
        runs a recipe against inputs that were never brought up to date.

        Args:
            targets: The task keys asked for.

        Returns:
            A graph holding *targets* plus their transitive dependencies,
            in the same order.
        """
        wanted: set[Key] = set()
        pending = list(targets)
        while pending:
            key = pending.pop()
            if key in wanted:
                continue
            wanted.add(key)
            pending.extend(self.tasks[key].depends_on)
        return replace(
            self,
            tasks={k: t for k, t in self.tasks.items() if k in wanted},
            found={k: f for k, f in self.found.items() if k in wanted},
        )

    def resolve(self, targets: list[str]) -> list[Key]:
        """Turn what a user typed into task keys.

        Args:
            targets: Each a task as lc names it (``fit#baseline``), or a
                result's name alone (``fit``) — matching it in every
                universe that builds it.

        Returns:
            The matching task keys, in the order given.

        Raises:
            ProjectError: If a target matches nothing. Quietly making
                nothing is the least useful thing a build tool can do.
        """
        keys: list[Key] = []
        for target in targets:
            matched = [
                key
                for key, task in self.tasks.items()
                if target == key or any(target == name(place) for place in task.result.places)
            ]
            if not matched:
                known = ", ".join(self.tasks) or "none"
                raise ProjectError(f"no output matches `{target}`. Available: {known}")
            keys.extend(matched)
        return keys


def name(place: Place) -> str:
    """A result's name at one of its places: its address without the
    universe. What its files there are named after."""
    return place.address.partition("#")[0]


# =============================================================================
# Building the graph
# =============================================================================


def build(root: Path) -> Graph:
    """Read a project's analysis into a graph of tasks.

    A result that belongs to an analysis declared with a ``commit`` is no
    task: it is never built again, only read through its record
    (``REBUILT_FROM_COMMIT``). Reading one, and driving an analysis on a
    commit's files (``NOT_AT_COMMIT``), are what lc does not do at all,
    so a project that needs either is refused whole rather than run in
    part.

    Args:
        root: The project root.

    Returns:
        One task per planned result this engine builds, each after the
        tasks it reads, with what ASTRA's checker found of it.

    Raises:
        ProjectError: If there is no analysis, ASTRA finds it invalid, or
            it plans a result this engine cannot build.
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
    # A reference is an id in ASTRA's model: what it names is looked up.
    outputs = {each.ref: (found, each) for found in analysis.analyses for each in found.outputs}
    inputs = {each.ref: (found, each) for found in analysis.analyses for each in found.inputs}
    universes = {
        each.ref: (found, each) for found in analysis.analyses for each in found.universes
    }
    owners = {
        result.id: universes[result.universe][0] if result.universe else outputs[result.output][0]
        for result in analysis.results
    }
    made_at = {result.id: result.places[0].bytes_path for result in analysis.results}

    def read_in(source: Source) -> Analysis:
        """The analysis that holds what an entry reads: an input's own, or
        the one the result it reads belongs to."""
        if source.input is not None:
            return inputs[source.input][0]
        assert source.result is not None  # an entry reads one or the other
        return owners[source.result]

    tasks: dict[Key, Task] = {}
    for result in analysis.results:
        owner = owners[result.id]
        if owner.commit is not None:
            continue
        runs_in, output = outputs[result.output]
        if result.command is None:
            read = [source.entry for source in result.inputs if read_in(source).commit]
            what = f"reads `{read[0]}` from" if read else "belongs to"
            raise ProjectError(
                f"`{result.id}` {what} an analysis declared with a `commit`, which lc does "
                "not build from. Declare that analysis by `path` alone to build it from the "
                "working tree."
            )
        if any((root / parent / ".git").exists() for parent in _folders(owner.folder)):
            raise ProjectError(
                f"`{result.id}` belongs to the analysis in {owner.folder}, which is a "
                "repository of its own — lc commits results in the project's repository "
                "only. Run lc from that analysis instead."
            )
        selection = universes[result.universe][1].selection if result.universe else {}
        tasks[result.id] = Task(
            result=result,
            workdir=runs_in.folder,
            reads={
                source.entry: inputs[source.input][1].path
                if source.input is not None
                else made_at[source.result or ""]
                for source in result.inputs
            },
            decisions={ref: selection[ref] for ref in output.decisions},
            resources=(
                {k: v for k, v in asdict(output.resources).items() if v is not None}
                if output.resources
                else {}
            ),
        )
    return Graph(
        tasks=tasks,
        found={key: report.statuses[key] for key in tasks},
        notes=tuple(note.message for note in analysis.notes if note.code in _NOTED),
        label=analysis.analyses[0].label or "",
        description=analysis.analyses[0].description or "",
    )


def _folders(folder: str) -> Iterator[str]:
    """*folder* and each folder above it, below the project root."""
    while folder not in ("", ".") and not folder.startswith(".."):
        yield folder
        folder = posixpath.dirname(folder)


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
        raise ProjectError(invalid(root, e.report.diagnostics)) from e
    except (api.NoAnalysisError, api.GitError, api.CacheError, OSError) as e:
        raise ProjectError(str(e)) from e


def invalid(root: Path, diagnostics: tuple[Diagnostic, ...]) -> str:
    """Word the refusal of an analysis ASTRA rejects, in ASTRA's own words.

    Args:
        root: The project root.
        diagnostics: What ASTRA reported; the errors are listed.

    Returns:
        The message, one line per broken rule, each placed in its file.
    """
    listed = "\n".join(
        f"  {d.location.file}:{d.location.line or 0}:{d.location.column or 0} "
        f"{d.code} {d.message}"
        for d in diagnostics
        if d.level == "error"
    )
    return (
        f"{root / SPEC_FILENAME} does not validate, so there is nothing to materialize "
        f"from it:\n{listed}"
    )
