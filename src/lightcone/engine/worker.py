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

Nothing here writes to git, and nothing here raises. A task that fails
returns a result saying so, because Dask propagates an exception to every
dependent and "who actually failed" would stop being answerable —
reporting every independent failure in one run is most of the point of
owning the loop.
"""

from __future__ import annotations

import functools
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
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
    from astra.check import Status

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
    — the driver, or the rerun entry point — because a per-task read
    could answer differently mid-run: HEAD moves as the driver commits,
    and a runtime resolved twice could disagree. Frozen and picklable, so
    it crosses to workers by value.
    """

    #: The IRI of the run's environment, checked either side of each recipe.
    environment: str
    #: The commit the run started from, which every record it writes names.
    commit: str
    #: The execution world — the host mechanism, or the project image.
    runtime: container.Runtime


# =============================================================================
# The Dask unit: decide, then execute
# =============================================================================


def materialize(
    root: Path,
    task: Task,
    context: RunContext,
    refresh: bool,
    decided: Decided | None,
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
        context: The run's driver-resolved facts.
        refresh: Whether to remake a result that is merely behind.
        decided: What the driver found the result to be before the run,
            or ``None`` where something upstream of it may be remade —
            then only this task, once its upstreams have landed, can ask.
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
        return _materialize(root, task, context, refresh, decided, foreign, upstream, output)
    except Exception as e:  # the contract is that this function returns
        return TaskResult(task.key, "failed", reason=f"{type(e).__name__}: {e}")


@dataclass(frozen=True)
class Decided:
    """What a result is, and the bytes its record gives if it is left alone."""

    verdict: assets.Verdict
    content_hash: str = ""
    size: int = 0


def decide(
    found: tuple[Status, ...], environment: str, foreign: dataset.LastWrite | None
) -> Decided:
    """Classify one result from what ASTRA's checker found at its places.

    Shared by the driver, which asks before a run, and a task downstream
    of a remake, which asks again — so the two cannot word, or weigh, the
    same finding differently.

    Args:
        found: The result's statuses, one per place.
        environment: The IRI of the run's environment.
        foreign: The commit that last wrote the result's files, if not
            its own run.

    Returns:
        The verdict, with the recorded bytes of a result that has a
        record.
    """
    verdict = assets.classify(found=found, environment=environment, foreign=foreign)
    if (record := found[0].record) is None:
        return Decided(verdict)
    return Decided(verdict, record.content_hash, record.size)


def _materialize(
    root: Path,
    task: Task,
    context: RunContext,
    refresh: bool,
    decided: Decided | None,
    foreign: dataset.LastWrite | None,
    upstream: tuple[TaskResult, ...],
    output: Callable[[str, bytes], None] | None,
) -> TaskResult:
    reported = {u.key: u for u in upstream if u.usable}
    if absent := [dep for dep in task.depends_on if dep not in reported]:
        return TaskResult(
            task.key, "blocked", reason=f"upstream did not finish: {', '.join(absent)}"
        )

    if decided is None:
        # Something upstream may have been remade, and only ASTRA can say
        # whether it came out as the bytes this result recorded reading —
        # a byte-identical rebuild stops the cascade here.
        found = assets.check(root)[task.places[0].record]
        decided = decide(found, context.environment, foreign)
    if decided.verdict.calls_for_a_remake(refresh=refresh):
        return execute(root, task, reported, context, output=output)

    # Left alone, so the bytes on disk stand. Their *recorded* digest,
    # never a recomputed one: on a clone that has fetched no annex content
    # the files are pointers, and rehashing them would quietly report a
    # different output.
    return TaskResult(
        task.key,
        decided.verdict.status,  # type: ignore[arg-type]  # stale was remade above
        content_hash=decided.content_hash,
        size=decided.size,
        reason=decided.verdict.why,
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
    from astra import records
    from astra.hashing import digest_file

    resources = TaskResources.parse(task.resources)
    if moved := _gate(root, context.environment):
        return TaskResult(task.key, "failed", reason=moved)

    # What the recipe is about to read, hashed before it runs: an input
    # this clone does not hold is a refusal that should cost no compute.
    read = []
    for entry, name in task.inputs.items():
        if (key := task.produced_by.get(entry)) is not None:
            # What the upstream task reported, never a rehash: one left
            # alone on a clone without its content is a pointer here.
            read.append(
                records.RecordedInput(entry, upstream[key].content_hash, upstream[key].size)
            )
        else:
            try:
                assets.require_fetched(root / name)
                hashed = digest_file(root / name)
            except (OSError, ProjectError) as e:
                return TaskResult(
                    task.key, "failed", reason=f"the input `{entry}` cannot be read: {e}"
                )
            read.append(records.RecordedInput(entry, hashed.content_hash, hashed.size))

    made = root / task.places[0].bytes
    # Never the parent directory: siblings share it, and under Dask they
    # are being written concurrently. What this result owns, at each of
    # its places, is its record and any file named after it
    # — the glob rather than the one declared path, so a spec that
    # re-declares the output in another format leaves no stale payload
    # behind. `<name>.` cannot reach a sibling: ASTRA refuses an output
    # that shares its id with a declaration, which is the only way one
    # result's name could begin another's.
    for place in task.places:
        (root / place.bytes).parent.mkdir(parents=True, exist_ok=True)
    policy = container.policy_for(
        context.runtime,
        [path for name in task.inputs.values() if (path := root / name).exists()],
        write_dir=made.parent,
        use_gpus=resources.gpus > 0,
    )
    with sandbox.scope(policy):
        # Validate device visibility and container support before removing outputs.
        for place in task.places:
            (root / place.record).unlink(missing_ok=True)
            for stale in (root / place.bytes).parent.glob(f"{place.name}.*"):
                if stale.is_file() or stale.is_symlink():
                    stale.unlink()
        started = datetime.now(UTC)
        outcome = sandbox.run(
            container.backend(context.runtime),
            policy,
            [_SHELL, "-c", task.command],
            cwd=root / task.workdir,
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

    # Guarded separately from the boundary catch above it, because these
    # two failures deserve different words: "your recipe failed" and "your
    # recipe worked and we could not record it" are different problems.
    if not made.is_file():
        found = "a directory" if made.is_dir() else "nothing"
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe exited 0 but left {found} at {task.places[0].bytes}",
            notes=outcome.notes,
        )

    try:
        digest = digest_file(made)
        for place in task.places:
            if place is not task.places[0]:
                shutil.copyfile(made, root / place.bytes)
            records.write_record(
                root / place.record,
                records.Record(
                    address=place.address,
                    content_hash=digest.content_hash,
                    size=digest.size,
                    commit=context.commit,
                    environment=context.environment,
                    runner=records.Runner("lightcone-cli", lc_version()),
                    inputs=tuple(read),
                    started=records.timestamp(started),
                    finished=records.timestamp(finished),
                ),
            )
    except (OSError, ProjectError) as e:
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe finished but its output could not be recorded: {e}",
            notes=outcome.notes,
        )
    return TaskResult(
        task.key, "ok", content_hash=digest.content_hash, size=digest.size, notes=outcome.notes
    )


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
                commit=dataset.head(root)[0],
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
    """What *task* reads of other results, from the records already in the tree.

    There is no graph in flight to take them from, so each upstream's
    record stands in for the result its task would have reported.
    """
    found = assets.check(root) if task.depends_on else {}
    upstream = {}
    for key in task.depends_on:
        if (record := found[graph.tasks[key].places[0].record][0].record) is None:
            raise ProjectError(
                f"`{key}`, which `{task.key}` reads, has no valid record. "
                "Run `lc materialize <cluster>` instead."
            )
        upstream[key] = TaskResult(key, "current", record.content_hash, record.size)
    return upstream


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
