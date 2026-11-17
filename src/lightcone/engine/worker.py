"""Making one output — the unit of work, and the only thing that runs a recipe.

Also an entry point:

    python -m lightcone.engine.worker <task>

which is what the ``[DATALAD RUNCMD]`` record in every materialization
commit names, behind an engine-pinning ``uv run --no-project --with …`` —
by version for a released engine, by source commit for a dev build. That
is why it is a module rather than an ``lc`` verb: it makes the output
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
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from lightcone.engine import assets, container, dataset, identity, plan, project, sandbox
from lightcone.engine.execution_resources import TaskResources
from lightcone.engine.plan import Key, Task
from lightcone.engine.project import (
    ProjectError,
    child_env,
    declared_project,
    uv_prefix,
)

if TYPE_CHECKING:
    from astra.api import RecordedInput

#: The commit a run is identified against: ``(sha, origin URL)``. Read once
#: by the driver and handed to every task, because the driver commits as
#: outputs land and HEAD therefore moves under the run.
Head = tuple[str, str]

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
    #: The output's content identity. Present for the three states in
    #: which the bytes on disk are what the spec asks for — this is what a
    #: dependent compares against, and records having read.
    data_version: str = ""
    #: The size of those bytes, which a dependent's record gives beside
    #: their hash.
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

    @property
    def digest(self) -> assets.Digest:
        """The hash and size of the output's bytes, as one value."""
        return self.data_version, self.size


@dataclass(frozen=True)
class RunContext:
    """The driver-resolved facts of one run, handed to every task.

    Each field is read or resolved exactly once, by whoever owns the run
    — the driver, or the rerun entry point — because a per-task read
    could answer differently mid-run: HEAD moves as the driver commits,
    a runtime resolved twice could disagree, and a provenance field that
    depends on task timing is worse than either answer. Frozen and
    picklable, so it crosses to workers by value; one object, so the
    next attestation field is one line here rather than an edit to five
    signatures.
    """

    #: The run's environment identity, checked either side of each recipe.
    env_version: str
    #: The run's ``(commit sha, origin URL)``.
    head: Head
    #: The run's content-hash memo for declared inputs.
    versions: assets.Versions
    #: The execution world — the host mechanism, or the project image.
    runtime: container.Runtime
    #: The uv that converges environments this run. Attestation only.
    uv_version: str


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
        task: The output to make.
        context: The run's driver-resolved facts.
        refresh: Whether to remake an output that is merely behind.
        foreign: The commit that last wrote the output's directory in
            place of its own run record, or ``None`` — answered by the
            driver, because history is git's and workers have no git;
            handed to the one classification rule, where it is `stale`.
        *upstream: The results of this task's dependencies, arriving as
            the futures it was given — which is what makes Dask the
            scheduler rather than a loop here.
        output: Optional receiver forwarding recipe stdout and stderr bytes.

    Returns:
        What happened. Never raises.
    """
    try:
        return _materialize(root, task, context, refresh, foreign, upstream, output)
    except Exception as e:  # the contract is that this function returns
        return TaskResult(task.key, "failed", reason=f"{type(e).__name__}: {e}")


def _materialize(
    root: Path,
    task: Task,
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

    # What each result this one reads came out as: the digest its task
    # reported, by the entry that reads it.
    made = {name: reported[key].digest for name, key in task.produced_by.items()}
    inputs = {
        name: made[name][0] if name in made else context.versions.of(path)[0]
        for name, path in task.inputs.items()
    }
    manifest = assets.read(task.manifest_path)
    verdict = assets.classify(
        definition_version=task.definition_version,
        env_version=context.env_version,
        manifest=manifest,
        inputs=inputs,
        foreign=foreign,
    )
    if verdict.calls_for_a_remake(refresh=refresh):
        return execute(root, task, made, context, output=output)

    # Left alone, so the bytes on disk stand. Their *recorded* digest,
    # never a recomputed one: on a clone that has fetched no annex content
    # the files are dangling symlinks, and rehashing them would quietly
    # report a different output.
    assert manifest is not None and verdict.status != "stale"  # the branch above
    return TaskResult(
        task.key,
        verdict.status,
        data_version=manifest.data_version,
        size=manifest.size,
        reason=verdict.why,
    )


# =============================================================================
# Executing one task, unconditionally
# =============================================================================


def execute(
    root: Path,
    task: Task,
    made: Mapping[str, assets.Digest],
    context: RunContext,
    *,
    output: Callable[[str, bytes], None] | None = None,
) -> TaskResult:
    """Run *task*'s recipe, and record what it produced and what it read.

    What the result owns is removed first: a file left from a previous run
    would otherwise be committed as part of a result that never produced
    it. Every file the recipe reads is looked at before it runs, and again
    after: each has to hold its content, and has to be the very file it
    was when it was hashed, or the record would name bytes the recipe did
    not read. The context's ``env_version`` is checked either side of the
    recipe, so a mid-run lock edit cannot be recorded as if it had been in
    force.

    Args:
        root: The project root.
        task: The output to make.
        made: The digest of each result this one reads, by the entry that
            reads it, as the task that made or kept it reported: what was
            hashed once, by that task, is never hashed again here.
        context: The run's driver-resolved facts.
        output: Optional receiver forwarding recipe stdout and stderr bytes.

    Returns:
        ``ok`` with the output's digest, or ``failed``. Commits nothing and
        never touches git beyond reading HEAD.
    """
    from astra import api

    resources = TaskResources.parse(task.resources)
    if moved := _gate(root, context.env_version):
        return TaskResult(task.key, "failed", reason=moved)

    # What the recipe is about to read, looked at before it runs: a file
    # this clone does not hold the content of is a refusal that should
    # cost no compute, and a recipe handed a pointer would read a few
    # lines of text for its data, under a record naming the real bytes.
    read: list[RecordedInput] = []
    watched: dict[str, tuple[Path, tuple[int, int, int]]] = {}
    for name, path in task.inputs.items():
        try:
            assets.require_fetched(path)
            stamp = assets.stamp(path)
            digest = made[name] if name in made else context.versions.of(path)
        except (OSError, ProjectError) as e:
            return TaskResult(
                task.key,
                "failed",
                reason=(
                    f"the input `{name}` ({plan.declared_path(root, path)}) cannot be "
                    f"read: {str(e).removeprefix(f'{path}: ')}"
                ),
            )
        watched[name] = path, stamp
        read.append(api.RecordedInput(name, *digest))

    # Never the parent directory: siblings share it now, and under Dask
    # they are being written concurrently. What this output owns is its
    # manifest, its record and any file named after it — the glob rather
    # than the one declared path, so a spec that re-declares the output in
    # another format leaves no stale payload behind (`Task.owns`).
    task.output_path.parent.mkdir(parents=True, exist_ok=True)
    read_paths = [p for p in task.inputs.values() if p.exists()]
    policy = container.policy_for(
        context.runtime, read_paths, write_dir=task.output_path.parent,
        use_gpus=resources.gpus > 0,
    )
    with sandbox.scope(policy):
        # Validate device visibility and container support before removing outputs.
        for own in (task.manifest_path, *task.records):
            own.unlink(missing_ok=True)
        for stale in task.output_path.parent.glob(f"{task.name}.*"):
            if stale.is_file() or stale.is_symlink():
                stale.unlink()
        started = datetime.now(UTC)
        outcome = sandbox.run(
            container.backend(context.runtime),
            policy,
            [_SHELL, "-c", task.recipe],
            cwd=root,
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
    if moved := _gate(root, context.env_version):
        return TaskResult(task.key, "failed", reason=moved, notes=outcome.notes)
    # A declared input is a file in the project's tree, which only a
    # sandbox makes read-only: written in place, the record would name
    # bytes that no longer exist anywhere.
    if rewritten := [
        name
        for name, (path, stamp) in watched.items()
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
    if not task.output_path.is_file():
        # A recipe that ran `mkdir` on its output, or wrote some other
        # name, made something that is not the output at all.
        found = "a directory" if task.output_path.is_dir() else "nothing"
        return TaskResult(
            task.key,
            "failed",
            reason=(
                f"the recipe exited 0 but left {found} at "
                f"{plan.declared_path(root, task.output_path)}"
            ),
            notes=outcome.notes,
        )

    try:
        sha, remote = context.head
        content_hash, size = assets.digest(task.output_path)
        # ASTRA's record, at each place the result has: what a checker
        # compares with the analysis as it then is.
        api.write_records(
            root,
            task.result,
            content_hash,
            size,
            api.Generation(
                commit=sha,
                # The environment the gate either side of the recipe held
                # it to, not one read again from files that may move.
                environment=api.Environment(identity.environment(context.env_version)),
                runner=api.Runner("lightcone-cli", lc_version()),
                inputs=tuple(read),
                started=_instant(started),
                finished=_instant(finished),
            ),
        )
        assets.write(
            task.manifest_path,
            assets.Manifest(
                output_id=task.name,
                universe_id=task.universe,
                recipe=task.recipe,
                definition_version=task.definition_version,
                env_version=context.env_version,
                data_version=content_hash,
                size=size,
                decisions=dict(task.decisions),
                input_versions={item.entry: item.content_hash for item in read},
                git_sha=sha,
                git_remote=remote,
                lc_version=lc_version(),
                uv_version=context.uv_version,
                hermeticity=asdict(outcome.attestation),
                # Milliseconds, not microseconds: RO-Crate consumers parse
                # `schema:endTime` with at most three fractional digits.
                started_at=started.isoformat(timespec="milliseconds"),
                finished_at=finished.isoformat(timespec="milliseconds"),
                image=context.runtime.manifest_image(),
            ),
        )
    except (OSError, ValueError, ProjectError) as e:
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe finished but its output could not be recorded: {e}",
            notes=outcome.notes,
        )
    return TaskResult(
        task.key, "ok", data_version=content_hash, size=size, notes=outcome.notes
    )


def _instant(moment: datetime) -> str:
    """A moment as a record gives one: in UTC, to the millisecond."""
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _gate(root: Path, env_version: str) -> str:
    """Empty while the environment is still the one the run started in."""
    if identity.env_version(root) == env_version:
        return ""
    return (
        "the environment changed while the run was in flight — uv.lock, "
        ".python-version, or an install setting was edited. Nothing was "
        "recorded; re-run `lc materialize <cluster>`."
    )


@functools.cache
def lc_version() -> str:
    """Report the running engine's version, empty for a bare source tree.

    The one lookup behind every place the engine attests itself: the
    record's ``runner``, the manifest's ``lc_version`` and the run
    record's version pin. One function, so they cannot disagree about
    which engine ran.

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
    would silently hand it an *empty* environment while the manifest
    recorded the lock's ``env_version``. The sync also carries
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
        # failing sync must not bury "no output `x`" under its own error.
        graph = plan.build(root)
        if (task := graph.tasks.get(argv[0])) is None:
            print(f"no output `{argv[0]}` in this project", file=sys.stderr)
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
                "record names would not say what its recipe ran on. Commit them first.",
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
            _from_disk(root, task),
            RunContext(
                env_version=identity.env_version(root),
                head=dataset.head(root),
                versions=assets.Versions(),
                runtime=runtime,
                uv_version=project.uv_version(root),
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


def _from_disk(root: Path, task: Task) -> dict[str, assets.Digest]:
    """What *task* reads of other results, from the tree as it stands.

    There is no graph in flight to take them from. Each upstream has to
    have been materialized, and its bytes are hashed where they lie: this
    run reads what is in the tree, and its record must say so.
    """
    made: dict[str, assets.Digest] = {}
    for name in task.produced_by:
        path = task.inputs[name]
        if assets.read(assets.manifest_path(path)) is None:
            raise ProjectError(
                f"the input `{name}` has never been materialized — there is no "
                f"manifest beside {plan.declared_path(root, path)}. Run "
                "`lc materialize <cluster>` instead."
            )
        try:
            made[name] = assets.digest(path)
        except OSError as e:
            # `digest` reports an unreadable path with the OS's own
            # exception, and this is the entry point a `datalad rerun`
            # lands on — so it names the input rather than unwinding a
            # traceback at whoever reads that.
            raise ProjectError(f"the input `{name}` cannot be read: {e}") from e
    return made


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
