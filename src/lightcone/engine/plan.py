"""The spec, read as a graph of tasks.

ASTRA plans the results of an analysis — one per output built once, one
per universe for an output built per universe — and says of each where
its bytes and its record go, what it reads, the options its decisions
select and the command that makes it. A task is one planned result with
everything executing it needs and nothing about *how* it will be
executed.

What the spec *means* is ASTRA's to say: ``astra.api.plan`` validates the
stack, settles every universe's selection, follows declared analyses and
fills the recipe grammar, so none of it is re-derived here. This module
only carries each result across to the workers as plain data, and
refuses what this engine cannot execute.

Nothing here schedules anything. The plan lists each result after every
result it reads, and that order is kept; at execution time ordering is
Dask's job.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lightcone.engine.project import SPEC_FILENAME, ProjectError

if TYPE_CHECKING:
    from astra.diagnostics import Diagnostic
    from astra.plan import Result
    from astra.universes import Place

#: A task's identity within a run: its result's address, after the folder
#: of the analysis the result belongs to when that is not the project's own
#: (``fit#baseline``, ``stage/fit#v1``).
Key = str


@dataclass(frozen=True)
class Task:
    """One planned result: everything needed to make it."""

    key: Key
    #: Where the result's record and bytes go, from the project root, one
    #: per name ASTRA gives it. The recipe writes the first; the others
    #: receive copies.
    places: tuple[Place, ...]
    #: The recipe with its placeholders filled — a shell command, its paths
    #: relative to ``workdir``.
    command: str
    #: The folder the recipe runs in, from the project root: its output's
    #: analysis's.
    workdir: str
    #: Each entry of the output's ``inputs``, as written and in order → the
    #: file it reads, from the project root.
    inputs: dict[str, str]
    #: The entries of ``inputs`` another task produces, and which one.
    produced_by: dict[str, Key]
    #: Each entry of the output's ``decisions`` → the option selected.
    decisions: dict[str, str]
    #: ASTRA's declaration; executor support is checked only when executing.
    resources: dict[str, Any] = field(default_factory=dict)

    @property
    def depends_on(self) -> tuple[Key, ...]:
        """The tasks that must be made first, deduplicated, in order."""
        return tuple(dict.fromkeys(self.produced_by.values()))


@dataclass(frozen=True)
class Graph:
    """Every task a run could make, each after the tasks it reads."""

    tasks: dict[Key, Task]
    #: What ASTRA notes about the plan that a run would silently live with:
    #: an output nothing plans, a command reaching outside its analysis.
    notes: tuple[str, ...] = ()

    def closure(self, targets: list[Key]) -> Graph:
        """Narrow the graph to *targets* and what they depend on.

        Asking for an output asks for what it is made of; anything less
        runs a recipe against inputs that were never brought up to date.

        Args:
            targets: The task keys asked for.

        Returns:
            A graph holding *targets* plus their transitive dependencies,
            in the plan's order.
        """
        wanted: set[Key] = set()
        pending = list(targets)
        while pending:
            key = pending.pop()
            if key in wanted:
                continue
            wanted.add(key)
            pending.extend(self.tasks[key].depends_on)
        return Graph(tasks={k: t for k, t in self.tasks.items() if k in wanted})

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
                if target == key or any(target == place.name for place in task.places)
            ]
            if not matched:
                known = ", ".join(self.tasks) or "none"
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
        One task per planned result this engine builds, each after the
        tasks it reads.

    Raises:
        ProjectError: If there is no analysis, ASTRA finds it invalid, or
            it plans a result this engine cannot build.
    """
    from astra import api
    from astra.plan import render_command
    from astra.universes import Place

    with asking_astra(root):
        planned = api.plan(root)
    # ASTRA measures every path from the top of the repository; everything
    # lc does is measured from the project root, which may be below it.
    above = posixpath.dirname(planned.root)

    def key_of(result: Result) -> Key:
        return posixpath.join(relative(result.owner.folder, above), result.places[0].address)

    keys = {(result.output, result.universe): key_of(result) for result in planned.results}
    tasks: dict[Key, Task] = {}
    for result in planned.results:
        if result.read_only:
            continue
        key = keys[result.output, result.universe]
        from_commit = [read.entry for read in result.inputs if read.commit is not None]
        if result.run_at is not None or from_commit:
            what = f"reads `{from_commit[0]}` from" if from_commit else "belongs to"
            raise ProjectError(
                f"`{key}` {what} an analysis declared with a `commit`, which lc does not "
                "build from. Declare that analysis by `path` alone to build it from the "
                "working tree."
            )
        owner = relative(result.owner.folder, above)
        if any((root / parent / ".git").exists() for parent in _folders(owner)):
            raise ProjectError(
                f"`{key}` belongs to the analysis in {owner}, which is a "
                "repository of its own — lc commits results in the project's repository "
                "only. Run lc from that analysis instead."
            )
        tasks[key] = Task(
            key=key,
            places=tuple(
                Place(
                    place.name,
                    place.address,
                    relative(place.record, above),
                    relative(place.bytes, above),
                )
                for place in result.places
            ),
            command=render_command(result, root / relative(result.workdir, above)),
            workdir=relative(result.workdir, above),
            inputs={read.entry: relative(read.path, above) for read in result.inputs},
            produced_by={
                read.entry: keys[read.result.output, read.result.universe]
                for read in result.inputs
                if read.result is not None
            },
            decisions={k: v for k, v in result.decisions.items() if v is not None},
            resources=(
                {k: v for k, v in asdict(result.resources).items() if v is not None}
                if result.resources
                else {}
            ),
        )
    notes = tuple(
        note.message
        for note in planned.notes
        if note.built and note.kind in ("unplanned", "outside_folder")
    )
    return Graph(tasks=tasks, notes=notes)


def relative(path: str, above: str) -> str:
    """Measure a path ASTRA gives from the project root.

    Args:
        path: A path from the top of the repository; empty for the top.
        above: The project root's own path from that top; empty when the
            project is the repository.

    Returns:
        The same file's path from the project root; empty for the root.
    """
    found = posixpath.relpath(path or ".", above or ".")
    return "" if found == "." else found


def _folders(folder: str) -> Iterator[str]:
    """*folder* and each folder above it, below the project root."""
    while folder and not folder.startswith(".."):
        yield folder
        folder = posixpath.dirname(folder)


@contextmanager
def asking_astra(root: Path) -> Iterator[None]:
    """Turn what ASTRA raises about a project into a ``ProjectError``.

    One place, because every question lc puts to ASTRA — the plan, the
    check — can fail the same ways, and each must reach the user as the
    same clean refusal rather than as another package's traceback.

    Args:
        root: The project root, for naming a missing analysis.

    Raises:
        ProjectError: If there is no analysis, it breaks one of ASTRA's
            rules, or git or ASTRA's cache cannot be used.
    """
    from astra import api
    from astra.cache import CacheError
    from astra.sources.git import GitError

    if not (root / SPEC_FILENAME).is_file():
        raise ProjectError(f"{root}: no {SPEC_FILENAME} — there is no analysis to materialize.")
    try:
        yield
    except api.InvalidAnalysisError as e:
        raise ProjectError(invalid(root, e.report.diagnostics)) from e
    except (api.NoAnalysisError, GitError, CacheError, OSError) as e:
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
