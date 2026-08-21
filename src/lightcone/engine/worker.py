"""Making one output — the unit of work, and the only thing that runs a recipe.

Also an entry point:

    python -m lightcone.engine.worker <universe>/<output_id>

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
import shutil
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from lightcone.engine import assets, container, dataset, identity, plan, project, sandbox, venue
from lightcone.engine.plan import Key, Task
from lightcone.engine.project import (
    ProjectError,
    child_env,
    declared_project,
    uv_prefix,
)

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
    #: dependent compares against.
    data_version: str = ""
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

    Returns:
        What happened. Never raises.
    """
    try:
        return _materialize(root, task, context, refresh, foreign, upstream)
    except Exception as e:  # the contract is that this function returns
        return TaskResult(task.key, "failed", reason=f"{type(e).__name__}: {e}")


def _materialize(
    root: Path,
    task: Task,
    context: RunContext,
    refresh: bool,
    foreign: dataset.LastWrite | None,
    upstream: tuple[TaskResult, ...],
) -> TaskResult:
    reported = {u.key: u for u in upstream if u.usable}
    if absent := [dep for dep in task.depends_on if dep not in reported]:
        names = ", ".join(f"{u}/{o}" for u, o in absent)
        return TaskResult(task.key, "blocked", reason=f"upstream did not finish: {names}")

    live = {key: u.data_version for key, u in reported.items()}
    inputs = {
        name: live[key] if (key := task.produced_by.get(name)) else context.versions.of(path)
        for name, path in task.inputs.items()
    }
    manifest = assets.read(task.output_dir)
    verdict = assets.classify(
        definition_version=task.definition_version,
        env_version=context.env_version,
        manifest=manifest,
        inputs=inputs,
        foreign=foreign,
    )
    if verdict.calls_for_a_remake(refresh=refresh):
        return execute(root, task, inputs, context)

    # Left alone, so the bytes on disk stand. Their *recorded* digest,
    # never a recomputed one: on a clone that has fetched no annex content
    # the files are dangling symlinks, and rehashing them would quietly
    # report a different output.
    assert manifest is not None and verdict.status != "stale"  # the branch above
    return TaskResult(
        task.key, verdict.status, data_version=manifest.data_version, reason=verdict.why
    )


# =============================================================================
# Executing one task, unconditionally
# =============================================================================


def execute(
    root: Path,
    task: Task,
    input_versions: Mapping[str, str],
    context: RunContext,
) -> TaskResult:
    """Run *task*'s recipe and record what it produced.

    The output directory is reset first: the recipe owns it, and a file
    left from a previous run would otherwise enter the content hash and be
    committed as part of an output that never produced it. The context's
    ``env_version`` is checked either side of the recipe, so a mid-run
    lock edit cannot be recorded as if it had been in force.

    Args:
        root: The project root.
        task: The output to make.
        input_versions: Each declared input's content identity, recorded
            in the manifest as the chain.
        context: The run's driver-resolved facts.

    Returns:
        ``ok`` with the output's ``data_version``, or ``failed``. Commits
        nothing and never touches git beyond reading HEAD.
    """
    if moved := _gate(root, context.env_version):
        return TaskResult(task.key, "failed", reason=moved)

    # The whole directory, not a list of expected files: a recipe declares
    # an output id rather than filenames, and a previous run that crashed
    # can have left anything in here — which would otherwise survive into
    # this run's `data_version` as though the recipe had written it. The
    # path is `results/<universe>/<output>` and `output_dir` refuses an id
    # that could widen it.
    if task.output_dir.exists():
        shutil.rmtree(task.output_dir)
    task.output_dir.mkdir(parents=True)

    read_paths = [p for p in task.inputs.values() if p.exists()]
    policy = container.policy_for(context.runtime, read_paths, output_dir=task.output_dir)
    started_at = _now()
    with sandbox.scope(policy):
        outcome = sandbox.run(
            container.backend(context.runtime),
            policy,
            [_SHELL, "-c", task.recipe],
            cwd=root,
            prefix=uv_prefix(root, sync=False),
            env=child_env(),
        )
    finished_at = _now()

    if outcome.returncode != 0:
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe exited {outcome.returncode}",
            notes=outcome.notes,
        )
    if moved := _gate(root, context.env_version):
        return TaskResult(task.key, "failed", reason=moved, notes=outcome.notes)

    # Guarded separately from the boundary catch above it, because these
    # two failures deserve different words: "your recipe failed" and "your
    # recipe worked and we could not record it" are different problems.
    try:
        sha, remote = context.head
        data_version = assets.data_version(task.output_dir)
        assets.write(
            task.output_dir,
            assets.Manifest(
                output_id=task.output_id,
                universe_id=task.universe_id,
                recipe=task.recipe,
                definition_version=task.definition_version,
                env_version=context.env_version,
                data_version=data_version,
                decisions=dict(task.decisions),
                input_versions=dict(input_versions),
                git_sha=sha,
                git_remote=remote,
                lc_version=lc_version(),
                uv_version=context.uv_version,
                hermeticity=asdict(outcome.attestation),
                started_at=started_at,
                finished_at=finished_at,
                image=context.runtime.manifest_image(),
            ),
        )
    except (OSError, ProjectError) as e:
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe finished but its output could not be recorded: {e}",
            notes=outcome.notes,
        )
    return TaskResult(task.key, "ok", data_version=data_version, notes=outcome.notes)


def _now() -> str:
    """The current instant, as a manifest timestamp.

    Milliseconds, not microseconds: RO-Crate consumers parse
    ``schema:endTime`` with at most three fractional digits, and the
    manifest is where that string is minted.
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _gate(root: Path, env_version: str) -> str:
    """Empty while the environment is still the one the run started in."""
    if identity.env_version(root) == env_version:
        return ""
    return (
        "the environment changed while the run was in flight — uv.lock, "
        ".python-version, or an install setting was edited. Nothing was "
        "recorded; re-run `lc materialize`."
    )


@functools.cache
def lc_version() -> str:
    """Report the running engine's version, empty for a bare source tree.

    The one lookup behind both places the engine attests itself: the
    manifest's ``lc_version`` and the run record's version pin. One
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

    ``python -m lightcone.engine.worker <universe>/<output_id>`` — what the
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
        argv: One argument, ``<universe>/<output_id>``.

    Returns:
        0 on success, 1 if the task failed, 2 on a bad argument or an
        unreadable project.
    """
    if len(argv) != 1 or "/" not in argv[0]:
        print("usage: python -m lightcone.engine.worker <universe>/<output_id>", file=sys.stderr)
        return 2

    universe_id, _, output_id = argv[0].partition("/")
    try:
        # A rerun executes a recipe, so it is gated the way materialize
        # is: compute nodes, never a NERSC login node.
        venue.require_compute_node("datalad rerun <commit>")
        root = declared_project()
        # The graph — and with it the task lookup — before any converge:
        # a typo'd target must cost nothing and mask nothing, and a
        # failing sync must not bury "no output `x`" under its own error.
        graph = plan.build(root)
        if (task := graph.tasks.get((universe_id, output_id))) is None:
            print(f"no output `{argv[0]}` in this project", file=sys.stderr)
            return 2
        # This one-task run resolves its own runtime and HEAD, because it
        # *is* the driver here — the rule is that each is read once by
        # whoever owns the run, not that a worker never reads them.
        runtime = container.runtime_for_run(root, build=False)
        container.converge(runtime)
        result = execute(
            root,
            task,
            _from_disk(task),
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


def _from_disk(task: Task) -> dict[str, str]:
    """Upstream versions, read from the manifests already in the tree.

    There is no graph in flight to take them from, and a single-task run
    has nothing to share a memo with — the digests are read once each here
    by construction.
    """
    versions: dict[str, str] = {}
    for name, path in task.inputs.items():
        if task.produced_by.get(name) is not None:
            if (manifest := assets.read(path)) is None:
                raise ProjectError(
                    f"the input `{name}` has never been materialized — there is no "
                    f"manifest in {path}. Run `lc materialize` instead."
                )
            versions[name] = manifest.data_version
        else:
            try:
                versions[name] = assets.data_version(path)
            except OSError as e:
                # `data_version` reports an absent or unreadable path with
                # the OS's own exception, and this is the entry point a
                # `datalad rerun` lands on — so it names the declared input
                # rather than unwinding a traceback at whoever reads that.
                raise ProjectError(
                    f"the declared input `{name}` cannot be read: {e}"
                ) from e
    return versions


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
