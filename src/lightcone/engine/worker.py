"""Making one result — the unit of work, and the only thing that runs a recipe.

Also an entry point:

    python -m lightcone.engine.worker <task>

which is what the ``[DATALAD RUNCMD]`` record in every materialization
commit names, behind an engine-pinning ``uv run --no-project --with …`` —
by version for a released engine, by source commit for a dev build. That
is why it is a module rather than an ``lc`` verb: it makes the result
unconditionally, commits nothing, and leaves the tree dirty by design —
precisely the state ``lc materialize`` refuses to start from — so
advertising it in ``lc --help`` would hand people a footgun.
:func:`main` converges the project environment from the rerun commit's
own lock before anything executes, so the record holds on a clone that
has never built one.

Keep this module cheap to import: no click, no rich. It is on the
``python -m`` path of every rerun, and of every task in every run.

Nothing here writes to git, and a task never raises. One that fails
returns a result saying so, because Dask propagates an exception to every
dependent and "who actually failed" would stop being answerable —
reporting every independent failure in one run is most of the point of
owning the loop.
"""

from __future__ import annotations

import functools
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lightcone.engine import assets, container, dataset, identity, plan, sandbox
from lightcone.engine.execution_resources import TaskResources
from lightcone.engine.plan import Key, Task
from lightcone.engine.project import (
    ProjectError,
    child_env,
    declared_project,
    uv_prefix,
)

if TYPE_CHECKING:
    from astra.api import Status

#: The shell a recipe's command is handed to. A recipe is a command line,
#: not an argv — redirects and pipes are part of what people write — and
#: bash is in the exec allowlist, so it is granted by the same rule that
#: grants everything else the boundary lets a recipe run.
_SHELL = "bash"


@dataclass(frozen=True)
class TaskResult:
    """What one task did. Returned, never raised, and handed to dependents."""

    key: Key
    status: Literal["ok", "current", "behind", "failed", "blocked"]
    #: The hash and size of the result's bytes, as a record gives them.
    #: Present for the three states in which the bytes on disk are what
    #: the spec asks for — this is what a dependent records having read.
    content_hash: str = ""
    size: int = 0
    #: Why it did not finish. Shown to the user verbatim.
    reason: str = ""
    #: Console lines from the boundary: a downgrade notice, a denial.
    notes: tuple[str, ...] = ()

    @property
    def digest(self) -> assets.Digest:
        """The hash and size of the result's bytes, as one value."""
        return self.content_hash, self.size

    @property
    def usable(self) -> bool:
        """Whether a dependent may proceed on this result.

        Returns:
            True for ``ok``, ``current`` and ``behind`` — the states in
            which the bytes on disk are what the spec asks for. ``behind``
            is among them deliberately: it says the environment moved, not
            that the artifact is wrong.
        """
        return self.status in ("ok", "current", "behind")


@dataclass(frozen=True)
class RunContext:
    """The driver-resolved facts of one run, handed to every task.

    Each field is read or resolved exactly once, by whoever owns the run
    — the driver, or the rerun entry point — because a task has no git
    to ask, and a runtime resolved twice could disagree. Frozen and
    picklable, so it crosses to workers by value.
    """

    #: The IRI of the run's environment, checked either side of each recipe.
    environment: str
    #: The commit the run's checkout is at, which every record it writes
    #: names.
    commit: str
    #: The execution world — the host mechanism, or the project image.
    runtime: container.Runtime
    #: Each declared input more than one task of the run reads, by its
    #: path from the project root → the stamp of the file the driver
    #: hashed, and its digest. A task that finds that very file takes the
    #: digest; one that finds any other hashes what it reads.
    hashed: Mapping[str, tuple[tuple[int, int, int], assets.Digest]] = field(
        default_factory=dict
    )


def require_recreatable(root: Path) -> None:
    """Refuse a run whose commit could not recreate its environment.

    A clean tree does not say the environment is in the commit: an
    ignored file is clean. A record names its environment by the files
    of its commit, so each has to be there for lc to recreate it. Asked
    by whoever owns a run, before it writes a record: the driver, and
    the rerun entry point.

    Args:
        root: The project root.

    Raises:
        ProjectError: Naming each file of the environment git does not
            track.
    """
    if missing := dataset.untracked(root, identity.files(root)):
        raise ProjectError(
            f"{', '.join(missing)} — not tracked by git, so the commit a record names "
            "could not recreate the environment its recipe ran in "
            "(UNRECREATABLE_ENVIRONMENT). Commit the environment's files, and remove "
            "whatever .gitignore rule keeps them out."
        )


# =============================================================================
# The Dask unit: decide, then execute
# =============================================================================


def materialize(
    root: Path,
    task: Task,
    found: Status,
    context: RunContext,
    refresh: bool,
    foreign: dataset.LastWrite | None,
    *upstream: TaskResult,
    output: Callable[[str, bytes], None] | None = None,
) -> TaskResult:
    """Make *task* if it needs making. What Dask submits, once per task.

    Where "the worker never raises" is enforced. Dask re-raises a task's
    exception in the driver, which would abort every other task in flight,
    so the contract is absolute — and one assembled from individually
    guarded call sites is only as true as the last person to add one.

    Args:
        root: The project root.
        task: The result to make.
        found: What ASTRA's checker found of the result before the run.
            Read by the driver, where git is; a worker asks nothing of
            ASTRA.
        context: The run's driver-resolved facts.
        refresh: Whether to remake a result that is merely behind.
        foreign: The commit that last wrote the result's files in place
            of its own run, or ``None`` — answered by the driver, because
            history is git's; handed to the one classification rule,
            where it is `stale`.
        *upstream: The results of this task's dependencies, arriving as
            the futures it was given — which is what makes Dask the
            scheduler rather than a loop here.
        output: Optional receiver forwarding recipe stdout and stderr bytes.

    Returns:
        What happened. Never raises.
    """
    try:
        return _materialize(root, task, found, context, refresh, foreign, upstream, output)
    except Exception as e:  # the contract is that this function returns
        return TaskResult(task.key, "failed", reason=f"{type(e).__name__}: {e}")


def _materialize(
    root: Path,
    task: Task,
    found: Status,
    context: RunContext,
    refresh: bool,
    foreign: dataset.LastWrite | None,
    upstream: tuple[TaskResult, ...],
    output: Callable[[str, bytes], None] | None,
) -> TaskResult:
    reported = {u.key: u for u in upstream if u.usable}
    if absent := [dep for dep in task.depends_on if dep not in reported]:
        return TaskResult(
            task.key, "blocked", reason=f"upstream did not finish: {', '.join(absent)}"
        )

    # Every upstream has landed, so the bytes this result would read are
    # known: a rebuild that came out byte-identical leaves it standing.
    verdict = assets.classify(
        found=found,
        environment=context.environment,
        read={
            source.entry: reported[source.result].digest
            for source in task.result.inputs
            if source.result is not None
        },
        foreign=foreign,
    )
    if verdict.calls_for_a_remake(refresh=refresh):
        return execute(root, task, reported, context, output=output)

    # Left alone, so the bytes on disk stand. Their *recorded* digest,
    # never a recomputed one: on a clone that has fetched no annex content
    # the files are pointers, and rehashing them would quietly report a
    # different output.
    record = found.record
    assert record is not None and verdict.status != "stale"  # the branch above
    return TaskResult(
        task.key,
        verdict.status,
        content_hash=record.content_hash,
        size=record.size,
        reason=verdict.why,
    )


# =============================================================================
# Executing one task, unconditionally
# =============================================================================


def execute(
    root: Path,
    task: Task,
    upstream: dict[Key, TaskResult],
    context: RunContext,
    *,
    output: Callable[[str, bytes], None] | None = None,
) -> TaskResult:
    """Run *task*'s recipe and record what it produced.

    What the result owns is removed first: a file left from a previous
    run would otherwise be committed as part of a result that never
    produced it. The context's environment is checked either side of the
    recipe, so a mid-run lock edit cannot be recorded as if it had been
    in force.

    Args:
        root: The project root.
        task: The result to make.
        upstream: The result of each task this one reads, by key. Their
            hashes are what the record says this run read.
        context: The run's driver-resolved facts.
        output: Optional receiver forwarding recipe stdout and stderr bytes.

    Returns:
        ``ok`` with the hash and size of the result's bytes, or
        ``failed``. Commits nothing and never touches git.
    """
    from astra import api

    resources = TaskResources.parse(task.resources)
    if moved := _gate(root, context.environment):
        return TaskResult(task.key, "failed", reason=moved)

    # What the recipe is about to read, looked at before it runs: a file
    # this checkout does not hold the content of is a refusal that should
    # cost no compute — and a recipe handed a stand-in would read a few
    # lines of text for its data, under a record naming the real bytes.
    read = []
    watched: dict[str, tuple[Path, tuple[int, int, int]]] = {}
    for source in task.result.inputs:
        path = root / task.reads[source.entry]
        try:
            assets.require_fetched(path)
            stamp = assets.stamp(path)
            known = context.hashed.get(task.reads[source.entry])
            if source.result is not None:
                # What an upstream task reported, never a rehash: its bytes
                # were hashed once, by the task that made or kept them.
                digest = upstream[source.result].digest
            elif known is not None and known[0] == stamp:
                # What the driver read of this very file, for every task
                # of the run that reads it.
                digest = known[1]
            else:
                digest = assets.digest_file(path)
            watched[source.entry] = path, stamp
        except (OSError, ProjectError) as e:
            # Named as the analysis names it: `path` is a file of the
            # run's checkout, which is nobody's to act on.
            why = str(e).removeprefix(f"{path}: ")
            return TaskResult(
                task.key,
                "failed",
                reason=(
                    f"the input `{source.entry}` ({task.reads[source.entry]}) cannot be "
                    f"read: {why}"
                ),
            )
        read.append(api.RecordedInput(source.entry, *digest))

    places = task.result.places
    made = root / places[0].file_path
    # Never the parent directory: siblings share it, and under Dask they
    # are being written concurrently. What this result owns there
    # (`Task.owns`) is its record and any file named after it, so a spec
    # that re-declares the output in another format leaves no stale
    # payload behind.
    folders = {(root / path).parent for path in task.files}
    for place in places:
        (root / place.file_path).parent.mkdir(parents=True, exist_ok=True)
    policy = container.policy_for(
        context.runtime,
        [path for name in task.reads.values() if (path := root / name).exists()],
        write_dir=made.parent,
        use_gpus=resources.gpus > 0,
    )
    with sandbox.scope(policy):
        # Validate device visibility and container support before removing outputs.
        for folder in folders:
            for stale in folder.iterdir() if folder.is_dir() else ():
                # The name first: it costs no stat of a sibling's file.
                if task.owns(stale.relative_to(root).as_posix()) and (
                    not stale.is_dir() or stale.is_symlink()
                ):
                    stale.unlink()
        started = datetime.now(UTC)
        cwd = root / task.workdir
        outcome = sandbox.run(
            container.backend(context.runtime, cwd),
            policy,
            [_SHELL, "-c", task.command],
            cwd=cwd,
            prefix=uv_prefix(root),
            env=child_env(),
            output=output,
        )
    finished = datetime.now(UTC)

    if outcome.returncode != 0:
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe exited {outcome.returncode}",
            notes=outcome.notes,
        )
    if moved := _gate(root, context.environment):
        return TaskResult(task.key, "failed", reason=moved, notes=outcome.notes)
    # A file a recipe reads is a hard link to the bytes git-annex keeps,
    # and only a sandbox makes it read-only: written in place, the record
    # would name bytes that no longer exist anywhere.
    if rewritten := [
        entry
        for entry, (path, stamp) in watched.items()
        if not path.is_file() or assets.stamp(path) != stamp
    ]:
        return TaskResult(
            task.key,
            "failed",
            reason=(
                f"the recipe changed what it only reads: {', '.join(rewritten)}. "
                "Nothing was recorded."
            ),
            notes=outcome.notes,
        )

    # Guarded separately from the boundary catch above it, because these
    # two failures deserve different words: "your recipe failed" and "your
    # recipe worked and we could not record it" are different problems.
    if made.is_symlink() or not made.is_file():
        found = (
            "a symlink" if made.is_symlink() else "a directory" if made.is_dir() else "nothing"
        )
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe exited 0 but left {found} at {places[0].file_path}",
            notes=outcome.notes,
        )

    try:
        content_hash, size = assets.digest_file(made)
        # Its bytes at each default place, and one record at each place
        # ASTRA gives the result: a result with two names is made once.
        for place in places[1:]:
            shutil.copyfile(made, root / place.file_path)
        api.write_records(
            root,
            task.result,
            content_hash,
            size,
            api.Generation(
                commit=context.commit,
                environment=api.Environment(context.environment),
                runner=api.Runner("lightcone-cli", lc_version()),
                inputs=tuple(read),
                started=_instant(started),
                finished=_instant(finished),
            ),
        )
    except (OSError, ValueError, ProjectError) as e:
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe finished but its output could not be recorded: {e}",
            notes=outcome.notes,
        )
    return TaskResult(task.key, "ok", content_hash=content_hash, size=size, notes=outcome.notes)


def _instant(moment: datetime) -> str:
    """A moment as a record gives one: in UTC, to the millisecond."""
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _gate(root: Path, environment: str) -> str:
    """Empty while the environment is still the one the run started in."""
    if identity.environment(root) == environment:
        return ""
    return (
        "the environment changed while the run was in flight — uv.lock, "
        ".python-version, or an install setting was edited. Nothing was "
        "recorded; re-run `lc materialize <cluster>`."
    )


@functools.cache
def lc_version() -> str:
    """Report the running engine's version, empty for a bare source tree.

    The one lookup behind both places the engine attests itself: the
    record's ``runner`` and the run record's version pin. One
    function, so the two cannot disagree about which engine ran.

    Cached: the metadata scan walks ``sys.path``, both callers are once
    per output, and an installed version cannot change under a running
    process.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("lightcone-cli")
    except PackageNotFoundError:  # pragma: no cover - only in a source tree
        return ""


# =============================================================================
# The entry point the run record names
# =============================================================================


def main(argv: list[str]) -> int:
    """Run one task from the command line, unconditionally.

    ``python -m lightcone.engine.worker <task>`` — what the
    ``[DATALAD RUNCMD]`` record in every materialization commit names. A
    thin wrapper over :func:`execute`, so this is an entry point rather
    than a second implementation. Nothing is classified: a rerun is a rerun.

    The environment is converged here, not assumed: a rerun checks out the
    lock but never the ``.venv``, and the recipe's own ``uv run --no-sync``
    would silently hand it an *empty* environment while the record named
    the lock's. The sync also carries ``--locked``, so a lock that no
    longer matches ``pyproject.toml`` is a loud refusal rather than a
    quiet relock.

    The image, by contrast, is *found*, never built: the run record lists
    the committed archive in ``extra_inputs``, so ``datalad rerun`` has
    fetched the exact bytes before this runs — and a build would be a
    commit, which nothing here makes.

    Args:
        argv: One argument, the task as lc names it (``fit#baseline``).

    Returns:
        0 on success, 1 if the task failed, 2 on a bad argument or an
        unreadable project.
    """
    if len(argv) != 1:
        print("usage: python -m lightcone.engine.worker <task>", file=sys.stderr)
        return 2

    try:
        root = declared_project()
        # The graph — and with it the task lookup — before any converge:
        # a typo'd target must cost nothing and mask nothing, and a
        # failing sync must not bury "no output `x`" under its own error.
        graph = plan.build(root)
        if (task := graph.tasks.get(argv[0])) is None:
            print(f"no output `{argv[0]}` in this project", file=sys.stderr)
            return 2
        # The record names HEAD, so the tree has to be HEAD's: all a rerun
        # has changed by now is this result's own files, which it removed.
        if edited := sorted(
            path
            for _, path in dataset.status(root, untracked_files=True, also=graph.beside)
            if not task.owns(path)
        ):
            print(
                f"error: uncommitted changes — {', '.join(edited)} — so the commit a "
                "record names would not say what its recipe ran on (UNCOMMITTED_CHANGES). "
                "Commit them first.",
                file=sys.stderr,
            )
            return 2
        require_recreatable(root)
        # This one-task run resolves its own runtime and HEAD, because it
        # *is* the driver here — the rule is that each is read once by
        # whoever owns the run, not that a worker never reads them.
        runtime = container.runtime_for_run(
            root, build=False, use_gpus=TaskResources.parse(task.resources).gpus > 0,
        )
        container.converge(runtime)
        result = execute(
            root,
            task,
            _from_disk(root, graph, task),
            RunContext(
                environment=identity.environment(root),
                commit=dataset.head(root),
                runtime=runtime,
            ),
        )
    except ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for note in result.notes:
        print(note, file=sys.stderr)
    if result.status != "ok":
        print(f"error: {result.reason}", file=sys.stderr)
        return 1
    return 0


def _from_disk(root: Path, graph: plan.Graph, task: Task) -> dict[Key, TaskResult]:
    """What *task* reads of other results, from the tree as it stands.

    There is no graph in flight to take them from. Each upstream has to
    have a record of its own, and its bytes are hashed where they lie:
    this run reads what is in the tree, and its record must say so even
    where that is not what the upstream's record gives. A result from a
    commit is given to the tree as a run gives it to its checkout, and
    reads as its record names it.
    """
    upstream = {}
    if pinned := [key for key in task.depends_on if key in graph.pinned]:
        # A run's driver does this; here the rerun is its own driver.
        from lightcone.engine import checkout

        for key, why in checkout.pin(root, root, graph, pinned).items():
            if why:
                raise ProjectError(f"`{key}`, which `{task.key}` reads: {why}")
            record = graph.found[key].record
            assert record is not None  # one with no record is never given
            upstream[key] = TaskResult(key, "current", record.content_hash, record.size)
    for key in task.depends_on:
        if key in upstream:
            continue
        if graph.found[key].record is None:
            raise ProjectError(
                f"`{key}`, which `{task.key}` reads, has no valid record. "
                "Run `lc materialize <cluster>` instead."
            )
        path = root / graph.tasks[key].result.places[0].file_path
        try:
            assets.require_fetched(path)
            upstream[key] = TaskResult(key, "current", *assets.digest_file(path))
        except OSError as e:
            raise ProjectError(f"`{key}`, which `{task.key}` reads, cannot be read: {e}") from e
    return upstream


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
