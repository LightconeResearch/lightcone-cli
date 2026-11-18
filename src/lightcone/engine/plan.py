"""The analysis, read as a graph of tasks.

What an analysis *means* is ASTRA's to say, in two answers:
``astra.api.resolve`` gives every result the analysis plans, each with
where its bytes and its record go, what each entry of its output's
``inputs`` reads, and its command as it runs; ``astra.api.status`` says
how each result's record stands. Each names the commit it was read at,
and the two must agree. None of that is re-derived here, and every path
is already measured from the project root.

A task is ASTRA's account of one result, with the few facts a run needs
looked up once: the file each entry reads, the options it is made with
and what its recipe asks for. Which tasks a run actually makes is decided
from what the checker found, kept beside them.

lc builds the results of one analysis. One that declares others is
refused: their results would be made with recipes that run in another
folder, under names this engine does not place.

Nothing here schedules anything. ASTRA lists each result after every
result it reads, and the graph keeps that order, which is all ``--check``
needs; at execution time ordering is Dask's job.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lightcone.engine.project import SPEC_FILENAME, ProjectError

if TYPE_CHECKING:
    from astra.api import Result, Status

#: A task's identity within a run: its result's id, as ASTRA gives it —
#: ``fit#baseline``, or ``fit`` for an output built once.
Key = str

#: Of what ASTRA notes for an author, the two a run would silently live
#: with: an output nothing plans, and a command that runs code from
#: outside its analysis.
_NOTED = ("UNPLANNED_OUTPUT", "CODE_OUTSIDE_ANALYSIS")


@dataclass(frozen=True)
class Task:
    """One planned result: everything needed to make it."""

    #: ASTRA's account of the result: its id, the place its bytes go, what
    #: each entry of its output's ``inputs`` reads, and its command.
    result: Result
    #: The single file this result is.
    output_path: Path
    #: Where ASTRA's record of the result goes: one per place it has.
    records: tuple[Path, ...]
    #: Each entry of the output's ``inputs``, as written → the file it
    #: reads: an input's own, or the file of the result it reads.
    inputs: dict[str, Path]
    #: Each decision the result is made with, by ASTRA's ref → the option
    #: its universe selects.
    decisions: dict[str, str]
    #: ASTRA's declaration; executor support is checked only when executing.
    resources: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> Key:
        """This task's identity within a run."""
        return self.result.id

    @property
    def name(self) -> str:
        """The result's name — its output's id — which its files are
        named after."""
        return self.result.places[0].address.partition("#")[0]

    @property
    def universe(self) -> str:
        """The id of the universe the result is built in; empty for a
        result built once."""
        return self.result.places[0].address.partition("#")[2]

    @property
    def recipe(self) -> str:
        """The recipe as it runs — a shell command, run in the project
        root."""
        assert self.result.command is not None  # `build` refuses a result with none
        return self.result.command

    @property
    def produced_by(self) -> dict[str, Key]:
        """The entries of ``inputs`` another task produces, and which one."""
        return {s.entry: s.result for s in self.result.inputs if s.result is not None}

    @property
    def depends_on(self) -> tuple[Key, ...]:
        """The tasks that must be made first, deduplicated, in order."""
        return tuple(dict.fromkeys(self.produced_by.values()))

    def owns(self, path: Path) -> bool:
        """Whether *path* is one of this result's own files.

        Its record, or a file named after it beside its bytes — the name
        and not the one declared path, so the payload a previous format
        left behind is the result's too. ``<name>.`` cannot reach a
        sibling: a name is an output's id, which holds no dot.

        Args:
            path: A file, from the filesystem root.

        Returns:
            True for a file this result's run writes or replaces.
        """
        return path in self.records or (
            path.parent == self.output_path.parent and path.name.startswith(f"{self.name}.")
        )


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
            matched = [key for key, task in self.tasks.items() if target in (key, task.name)]
            if not matched:
                known = ", ".join(sorted(self.tasks)) or "none"
                raise ProjectError(f"no output matches `{target}`. Available: {known}")
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
        reads, with what ASTRA's checker found of it and the notes a run
        reports.

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
    selections = {each.ref: each.selection for each in driving.universes}
    made_at = {result.id: result.places[0].file_path for result in analysis.results}

    tasks: dict[Key, Task] = {}
    for result in analysis.results:
        # Only a result of an analysis read at a commit has no command, and
        # there is no other analysis here.
        assert result.command is not None
        output = outputs[result.output]
        selection = selections[result.universe] if result.universe else {}
        decisions = {ref: selection[ref] for ref in output.decisions}
        tasks[result.id] = Task(
            result=result,
            output_path=root / result.places[0].file_path,
            records=tuple(root / place.record_path for place in result.places),
            inputs={
                source.entry: root / made_at[source.result]
                if source.result is not None
                else root / inputs[source.input or ""]
                for source in result.inputs
            },
            decisions=decisions,
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
