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
import hashlib
import json
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lightcone.engine import (
    container,
    content,
    dataset,
    environments,
    identity,
    plan,
    sandbox,
    staging,
)
from lightcone.engine.execution_resources import TaskResources
from lightcone.engine.plan import ResultId, Task
from lightcone.engine.project import (
    ProjectError,
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
class TaskOutcome:
    """What one task did. Returned, never raised, and handed to dependents."""

    id: ResultId
    #: ``made`` by this task, left alone as ``ok`` or ``behind``, or not
    #: made at all.
    status: Literal["made", "ok", "behind", "failed", "blocked"]
    #: The hash and size of the result's bytes, as a record gives them.
    #: Present for the three states in which the bytes on disk are what
    #: the spec asks for — this is what a dependent records having read.
    content_hash: str = ""
    size: int = 0
    #: The stamp of the file those bytes are (`content.stamp`), or None
    #: where there was none to look at. What reads the result takes the
    #: digest for that very file only, and hashes any other it finds.
    stamp: tuple[int, int, int] | None = None
    #: Why it did not finish. Shown to the user verbatim.
    reason: str = ""
    #: Console lines from the boundary: a downgrade notice, a denial.
    notes: tuple[str, ...] = ()

    @property
    def digest(self) -> content.Digest:
        """The hash and size of the result's bytes, as one value."""
        return self.content_hash, self.size

    @property
    def usable(self) -> bool:
        """Whether a dependent may proceed on this result.

        Returns:
            True for ``made``, ``ok`` and ``behind`` — the states in
            which the bytes on disk are what the spec asks for. ``behind``
            is among them deliberately: it says the environment moved, not
            that the result is wrong.
        """
        return self.status in ("made", "ok", "behind")


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
    #: Each declared input more than one task of the run reads → the stamp
    #: of the file the driver hashed, and its digest. A task that finds
    #: that very file takes the digest; one that finds any other hashes
    #: what it reads.
    hashed: Mapping[Path, tuple[tuple[int, int, int], content.Digest]] = field(
        default_factory=dict
    )


def require_in_commit(root: Path, specs: Sequence[str]) -> None:
    """Refuse a run whose commit would not hold what its records name it for.

    A record names a commit, and two things are read back from it: the
    analysis, and each it declares from the working tree, which ASTRA's
    checker compares the result with, and the files lc recreates the
    environment from, which its IRI names. A clean
    tree does not say either is there: an ignored file is clean, a symlink
    is committed as the path it points to, and an annexed file as a
    pointer, not as the bytes read through either. Asked by whoever owns a
    run, before it writes a record: the driver, and the rerun entry point.

    Args:
        root: The project root.
        specs: The file of each analysis read from the working tree, from
            the project root (`Graph.specs`).

    Raises:
        ProjectError: Naming each file the commit would not hold.
    """
    environment = identity.files(root)
    missing = dataset.not_in_git(root, [*specs, *environment])
    if analyses := [path for path in missing if path in specs]:
        raise ProjectError(
            f"{', '.join(analyses)} — not in git as files of their own (untracked, a "
            "symlink or annexed), so the commit a record names would hold no analysis to "
            "check its results against. Commit each file itself, in git, and remove "
            "whatever .gitignore rule keeps it out."
        )
    if missing:
        raise ProjectError(
            f"{', '.join(missing)} — not in git as files of their own (untracked, a "
            "symlink or annexed), so the commit a record names could not recreate the "
            "environment its recipe ran in (UNRECREATABLE_ENVIRONMENT). Commit each file "
            "itself, in git, and remove whatever .gitignore rule keeps it out."
        )


# =============================================================================
# The Dask unit: decide, then execute
# =============================================================================


def materialize(
    root: Path,
    task: Task,
    found: Status,
    remotes: tuple[str, ...] | None,
    context: RunContext,
    refresh: bool,
    *upstream: TaskOutcome,
    output: Callable[[str, bytes], None] | None = None,
) -> TaskOutcome:
    """Make *task* if it needs making. What Dask submits, once per task.

    Where "the worker never raises" is enforced. Dask re-raises a task's
    exception in the driver, which would abort every other task in flight,
    so the contract is absolute — and one assembled from individually
    guarded call sites is only as true as the last person to add one.

    Args:
        root: The project root.
        task: The result to make.
        found: What ASTRA's checker found of the result before the run.
            Read by the driver, where git is; a task asks nothing of
            ASTRA.
        remotes: Where this clone lacks the result's bytes, the remotes
            that hold them, as the driver found them: whether a run can
            fetch them is git-annex's to say, and a task asks git nothing.
        context: The run's driver-resolved facts.
        refresh: Whether this result was explicitly requested to be remade.
        *upstream: The results of this task's dependencies, arriving as
            the futures it was given — which is what makes Dask the
            scheduler rather than a loop here.
        output: Optional receiver forwarding recipe stdout and stderr bytes.

    Returns:
        What happened. Never raises.
    """
    receipt: Path | None = None
    try:
        if context.runtime.staged:
            # Dask may resubmit after losing a worker's reply. A completed
            # attempt is reused; a possibly live attempt must never be raced.
            attempts = root / ".lightcone/tasks"
            attempts.mkdir(parents=True, exist_ok=True)
            claim = attempts / hashlib.sha256(task.id.encode()).hexdigest()
            try:
                claim.mkdir()
            except FileExistsError:
                done = claim / "outcome.json"
                if not done.is_file():
                    return TaskOutcome(task.id, "failed", reason=(
                        "an earlier attempt is still running or was interrupted in this run"
                    ))
                saved = json.loads(done.read_text())
                saved["stamp"] = tuple(saved["stamp"]) if saved["stamp"] is not None else None
                saved["notes"] = tuple(saved["notes"])
                return TaskOutcome(**saved)
            receipt = claim / "outcome.json"
        outcome = _materialize(root, task, found, remotes, context, refresh, upstream, output)
    except Exception as e:  # the contract is that this function returns
        outcome = TaskOutcome(task.id, "failed", reason=f"{type(e).__name__}: {e}")
    if receipt is not None:
        try:
            pending = receipt.with_suffix(".partial")
            pending.write_text(json.dumps(asdict(outcome)))
            pending.replace(receipt)
        except OSError as exc:
            return TaskOutcome(task.id, "failed", reason=f"cannot retain task outcome: {exc}")
    return outcome


def _materialize(
    root: Path,
    task: Task,
    found: Status,
    remotes: tuple[str, ...] | None,
    context: RunContext,
    refresh: bool,
    upstream: tuple[TaskOutcome, ...],
    output: Callable[[str, bytes], None] | None,
) -> TaskOutcome:
    reported = {u.id: u for u in upstream if u.usable}
    if absent := [dep for dep in task.depends_on if dep not in reported]:
        return TaskOutcome(
            task.id, "blocked", reason=f"upstream did not finish: {', '.join(absent)}"
        )

    # Every upstream has been made or kept, so the bytes this result would
    # read are known: a rebuild that came out byte-identical leaves it
    # standing.
    verdict = plan.classify(
        found=found,
        environment=context.environment,
        remotes=remotes,
        read={entry: reported[key].digest for entry, key in task.produced_by.items()},
    )
    if verdict.calls_for_a_remake(refresh=refresh):
        return execute(root, task, reported, context, output=output)

    # Left alone, so the bytes on disk stand. Their *recorded* digest,
    # never a recomputed one: on a clone that has fetched no annex content
    # the files are stand-ins, and rehashing them would quietly report a
    # different result.
    record = found.record
    assert record is not None and verdict.state in ("ok", "behind")  # the branch above
    return TaskOutcome(
        task.id,
        verdict.state,
        content_hash=record.content_hash,
        size=record.size,
        stamp=content.stamp(task.path) if task.path.exists() else None,
        reason=verdict.why,
    )


# =============================================================================
# Executing one task, unconditionally
# =============================================================================


def execute(
    root: Path,
    task: Task,
    upstream: Mapping[ResultId, TaskOutcome],
    context: RunContext,
    *,
    output: Callable[[str, bytes], None] | None = None,
) -> TaskOutcome:
    """Run *task*'s recipe, and record what it produced and what it read.

    What the result owns is removed first: a file left from a previous run
    would otherwise be committed as part of a result that never produced
    it. Every file the recipe reads is looked at before it runs, and again
    after: each has to hold its content, and has to be the very file it
    was when it was hashed, or the record would name bytes the recipe did
    not read. The context's environment is checked either side of the
    recipe, so a mid-run lock edit cannot be recorded as if it had been in
    force.

    Args:
        root: The project root.
        task: The result to make.
        upstream: The outcome of each task this one reads, by id: what
            was hashed once, by the task that made or kept it, is never
            hashed again here.
        context: The run's driver-resolved facts.
        output: Optional receiver forwarding recipe stdout and stderr bytes.

    Returns:
        ``made`` with the hash and size of the result's bytes, or
        ``failed``. Commits nothing and never touches git.
    """
    from astra import api

    resources = TaskResources.parse(task.resources)
    if moved := _gate(root, context.environment):
        return TaskOutcome(task.id, "failed", reason=moved)

    # What the recipe is about to read, looked at before it runs: a file
    # this clone does not hold the content of is a refusal that should
    # cost no compute, and a recipe handed a stand-in would read a few
    # lines of text for its data, under a record naming the real bytes.
    read = []
    watched: dict[str, tuple[Path, tuple[int, int, int]]] = {}
    for entry, path in task.inputs.items():
        try:
            content.require_fetched(path)
            stamp = content.stamp(path)
            # What was read of this very file already is taken: by the task
            # that made or kept the result, or by the driver, for every task
            # of the run that reads it. Any other file is hashed here.
            known: tuple[tuple[int, int, int] | None, content.Digest] | None = (
                context.hashed.get(path)
            )
            if (producer := task.produced_by.get(entry)) is not None:
                known = upstream[producer].stamp, upstream[producer].digest
            digest = (
                known[1] if known is not None and known[0] == stamp else content.digest_file(path)
            )
        except (OSError, ProjectError) as e:
            return TaskOutcome(
                task.id,
                "failed",
                reason=(
                    f"the input `{entry}` ({plan.declared_path(root, path)}) cannot be "
                    f"read: {str(e).removeprefix(f'{path}: ')}"
                ),
            )
        watched[entry] = path, stamp
        read.append(api.RecordedInput(entry, *digest))

    # Never the parent directory: siblings share it, and under Dask they
    # are being written concurrently. What this result owns there is its
    # file and its records (`Task.owns`).
    task.path.parent.mkdir(parents=True, exist_ok=True)
    policy = container.policy_for(
        context.runtime,
        [path for path in task.inputs.values() if path.exists()],
        write_dir=task.path.parent,
        use_gpus=resources.gpus > 0,
    )
    with sandbox.scope(policy):
        # Validate device visibility and container support before removing what it owns.
        for owned in (*task.paths, *task.record_paths):
            if owned.is_file() or owned.is_symlink():
                owned.unlink()
        started = datetime.now(UTC)
        # In its own analysis's folder, even where the project drives it
        # (WRONG_WORKING_DIRECTORY).
        outcome = sandbox.run(
            container.backend(context.runtime, workdir=task.workdir),
            policy,
            [_SHELL, "-c", task.command],
            cwd=task.workdir,
            prefix=uv_prefix(root),
            env=context.runtime.env,
            output=output,
        )
    finished = datetime.now(UTC)

    if outcome.returncode != 0:
        return TaskOutcome(
            task.id,
            "failed",
            reason=f"the recipe exited {outcome.returncode}",
            notes=outcome.notes,
        )
    if moved := _gate(root, context.environment):
        return TaskOutcome(task.id, "failed", reason=moved, notes=outcome.notes)
    # A declared input is a file in the project's tree, which only a
    # sandbox makes read-only: written in place, the record would name
    # bytes that no longer exist anywhere.
    if rewritten := [
        entry
        for entry, (path, stamp) in watched.items()
        if not path.is_file() or content.stamp(path) != stamp
    ]:
        return TaskOutcome(
            task.id,
            "failed",
            reason=(
                f"the recipe changed what it only reads: {', '.join(rewritten)}. "
                "Nothing was recorded."
            ),
            notes=outcome.notes,
        )

    # Guarded separately from the boundary catch above it, because these
    # two failures deserve different words: "your recipe failed" and "your
    # recipe worked and we could not record it" are different problems. A
    # result is one file's bytes: a symlink would commit a link to
    # something no record describes, and a folder is not one file.
    made = task.path
    if made.is_symlink() or not made.is_file():
        found = (
            "a symlink" if made.is_symlink() else "a directory" if made.is_dir() else "nothing"
        )
        return TaskOutcome(
            task.id,
            "failed",
            reason=(
                f"the recipe exited 0 but left {found} at {plan.declared_path(root, made)}"
            ),
            notes=outcome.notes,
        )

    # ASTRA reads a file shaped like a stand-in as one, for content its
    # record does not give: a record of these bytes could never be checked,
    # and every check would plan the result again.
    if tool := content.standin(made):
        return TaskOutcome(
            task.id,
            "failed",
            reason=(
                f"the recipe exited 0 but left what reads as a {tool} stand-in at "
                f"{plan.declared_path(root, made)}, not content a record can name"
            ),
            notes=outcome.notes,
        )

    try:
        # A result with two names is one file: the recipe writes the first
        # place, and the others are given its bytes.
        for other in (() if context.runtime.staged else task.paths[1:]):
            other.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(made, other)
        stamp = content.stamp(made)
        content_hash, size = content.digest_file(made)
        # ASTRA's record, at each place the result has: what a checker
        # compares with the analysis as it then is.
        api.write_records(
            root,
            task.result,
            content_hash,
            size,
            api.Generation(
                commit=context.commit,
                # The environment the gate either side of the recipe held
                # it to, not one read again from files that may move.
                environment=api.Environment(context.environment),
                runner=api.Runner("lightcone-cli", lc_version()),
                inputs=tuple(read),
                started=_instant(started),
                finished=_instant(finished),
            ),
        )
    except (OSError, ValueError, ProjectError) as e:
        return TaskOutcome(
            task.id,
            "failed",
            reason=f"the recipe finished but its result could not be recorded: {e}",
            notes=outcome.notes,
        )
    if context.runtime.staged:
        staging.seal(task)
    return TaskOutcome(
        task.id, "made", content_hash=content_hash, size=size, stamp=stamp, notes=outcome.notes
    )


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
    record's ``runner`` and the run record's version pin. One function,
    so the two cannot disagree about which engine ran.

    Cached: the metadata scan walks ``sys.path``, both callers are once
    per result, and an installed version cannot change under a running
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
    the lock's. The sync also carries
    ``--locked``, so a lock that no longer matches ``pyproject.toml`` is a
    loud refusal rather than a quiet relock.

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
        # failing sync must not bury "no result `x`" under its own error.
        graph = plan.build(root)
        if (task := graph.tasks.get(argv[0])) is None:
            print(f"no result `{argv[0]}` in this project", file=sys.stderr)
            return 2
        # The record names HEAD, so the tree has to be HEAD's: all a rerun
        # has changed by now is this result's own files, which it removed.
        if edited := sorted(
            path
            for _, path in dataset.status(root, untracked_files=True)
            if not task.owns(root / path)
        ):
            print(
                f"error: uncommitted changes — {', '.join(edited)} — so the commit a "
                "record names would not say what its recipe ran on (UNCOMMITTED_CHANGES). "
                "Commit them first.",
                file=sys.stderr,
            )
            return 2
        require_in_commit(root, graph.specs)
        original = task
        runtime = container.runtime_for_run(
            root, build=False, use_gpus=TaskResources.parse(task.resources).gpus > 0,
        )
        stage = staging.take(root, dataset.head(root))
        cleanup = True
        try:
            task = task.at(stage.root)
            trees: dict[str, list[str]] = {}
            paths = []
            for path in original.inputs.values():
                if (at := plan.at_commit(root, path)) is not None:
                    trees.setdefault(at[0], []).append(at[1])
                else:
                    paths.append(plan.declared_path(root, path))
            staging.fill(root, stage.top, stage.root, stage.commit, paths, fetch=True)
            for commit, paths in trees.items():
                stage.give(commit, graph.commits[commit], paths)
            runtime, _ = environments.prepare(
                root, runtime.at(stage.root), identity.environment(stage.root),
            )
            # This entry point is a one-task driver; DataLad owns the final commit.
            cleanup = False
            made = execute(
                stage.root, task, _from_disk(stage.root, graph, task),
                RunContext(identity.environment(stage.root), stage.commit, runtime),
            )
            cleanup = True
            if made.status == "made":
                # DataLad removed/unlocked this task's destinations before calling us.
                # Its earlier dirty-tree check deliberately allowed exactly those paths.
                cleanup = False
                with staging.collect(task, original):
                    pass
                cleanup = True
        except BaseException as exc:
            if not cleanup and isinstance(exc, Exception):
                raise ProjectError(
                    f"{exc}. Run files retained at {stage.root}; "
                    "remove only after its tasks stop."
                ) from exc
            raise
        finally:
            if cleanup:
                stage.close()
    except ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for note in made.notes:
        print(note, file=sys.stderr)
    if made.status != "made":
        print(f"error: {made.reason}", file=sys.stderr)
        return 1
    return 0


def _from_disk(root: Path, graph: plan.Graph, task: Task) -> dict[ResultId, TaskOutcome]:
    """What *task* reads of other results, from the tree as it stands.

    There is no graph in flight to take them from. Each upstream has to
    have a record of its own, and its bytes are hashed where they lie:
    this run reads what is in the tree, and its record must say so even
    where that is not what the upstream's record gives. A result from a
    commit is read as a run reads it (:func:`from_commit`).
    """
    upstream = {}
    for entry, key in task.produced_by.items():
        path = task.inputs[entry]
        if key not in graph.tasks:
            upstream[key] = from_commit(key, graph.checked.statuses[key], path)
            if not upstream[key].usable:
                raise ProjectError(f"`{key}`, which `{task.id}` reads, {upstream[key].reason}")
            continue
        if graph.checked.statuses[key].record is None:
            raise ProjectError(
                f"`{key}`, which `{task.id}` reads, has no valid record. "
                "Run `lc materialize <cluster>` instead."
            )
        try:
            content.require_fetched(path)
            upstream[key] = TaskOutcome(
                key, "ok", *content.digest_file(path), stamp=content.stamp(path)
            )
        except OSError as e:
            # This is the entry point a `datalad rerun` lands on, so it
            # names what it could not read rather than unwinding a
            # traceback at whoever reads that.
            raise ProjectError(f"`{key}`, which `{task.id}` reads, cannot be read: {e}") from e
    return upstream


def unreadable(found: Status) -> str:
    """Say why nothing is built from a result from a commit, from what
    ASTRA's checker found of it there; empty where something may be.

    Args:
        found: What ASTRA's checker found of the result at its commit.

    Returns:
        The reason, or empty.
    """
    # Bytes there that are neither its record's nor a stand-in for them:
    # unfetched content a stand-in vouches for is no diagnostic at all.
    absent = [d.message for d in found.why if d.code == "ABSENT_RESULT"]
    if found.state == "unchanged" and found.record is not None and not absent:
        return ""
    if found.state == "unchanged":
        return (
            "lacks its recorded bytes at its commit, so nothing is built from it "
            f"(STALE_INPUT): {'; '.join(dict.fromkeys(absent))}"
        )
    broken = "; ".join(dict.fromkeys(d.message for d in found.why if d.level == "error"))
    return (
        f"is {found.state} at its commit, so nothing is built from it (STALE_INPUT)"
        + (f": {broken}" if broken else "")
    )


def from_commit(key: ResultId, found: Status, path: Path) -> TaskOutcome:
    """A result from a commit, as what reads it takes it.

    Never built, and read through its record at its commit
    (``REBUILT_FROM_COMMIT``): its bytes, where a run gives that commit's
    tree, are hashed once and have to be the ones its record gives, or
    nothing is built from it (``STALE_INPUT``).

    Args:
        key: The result's id.
        found: What ASTRA's checker found of it at its commit.
        path: Where a run gives its bytes.

    Returns:
        ``ok``, with its digest and the stamp of the file it is of,
        or ``failed`` saying why it cannot be read.
    """
    if why := unreadable(found):
        return TaskOutcome(key, "failed", reason=why)
    record = found.record
    assert record is not None  # unreadable() said
    try:
        content.require_fetched(path)
        stamp, digest = content.stamp(path), content.digest_file(path)
    except (OSError, ProjectError) as e:
        return TaskOutcome(key, "failed", reason=f"cannot be read: {e}")
    if digest != (record.content_hash, record.size):
        return TaskOutcome(
            key,
            "failed",
            reason="lacks its recorded bytes at its commit, so nothing is built from it "
            "(STALE_INPUT)",
        )
    return TaskOutcome(key, "ok", *digest, stamp=stamp)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
