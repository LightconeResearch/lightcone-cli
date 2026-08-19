"""Making a whole analysis: what runs, in what order, and what gets committed.

The driver's three jobs, and the order matters.

**It refuses to start on a dirty tree.** Every materialization is
committed together with the code that produced it, so a run that began
with uncommitted changes could not honestly say which code that was.

**It hands the graph to Dask and gets out of the way.** Every task is
submitted with its upstream futures as arguments, so the ordering, the
parallelism, and the scheduling are Dask's — there is no ready-set loop
here to get wrong.

**It owns git, alone.** Workers execute and return; the driver commits, in
one thread, as results arrive. That is not a preference: concurrent git
operations on one repository race on the index lock. The same loop
restores what a failed or interrupted task left behind, so the tree ends
exactly as clean as it started — which is what makes the refusal above
survivable rather than a trap.

One consequence, checked rather than assumed: a dependent starts as soon
as its upstream's *worker* returns, which is milliseconds before the
driver finishes annexing that upstream — so a recipe does read an input
directory while ``git annex add`` is replacing its files with symlinks.
That is safe, because git-annex hard-links the content into the object
store first and then renames the symlink over the file: the path never
stops existing and never holds partial bytes. Measured, on a run of
concurrent full-content reads across 24 MB: no missing paths, no short
reads, no wrong bytes. Do not "fix" this by moving the save into the
task — that is what puts git back in the workers.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from lightcone.engine import assets, dataset, identity, plan, project, worker
from lightcone.engine.plan import Graph, Key, Task
from lightcone.engine.project import ProjectError, require_uv


@dataclass
class MaterializeReport:
    """What a run did, or — in check mode — what it would do."""

    #: ``universe/output`` for each output this run produced and committed.
    made: list[str] = field(default_factory=list)
    #: Already current: nothing about them changed.
    skipped: list[str] = field(default_factory=list)
    #: The recipe failed, or the environment moved under it.
    failed: list[str] = field(default_factory=list)
    #: Not attempted, because something upstream did not finish.
    blocked: list[str] = field(default_factory=list)
    #: Check mode only: ``universe/output`` → why it would run.
    planned: dict[str, str] = field(default_factory=dict)
    #: lc's own prose: what the lock scan found, why a task did not finish.
    warnings: list[str] = field(default_factory=list)
    #: Console lines from the boundary, verbatim — a downgrade notice, a
    #: denial and its remedies. Kept apart from ``warnings`` because they
    #: are built to be *pasted*: reflowing a `uv add numpy` to the terminal
    #: width breaks the one thing a denial message is for. The caller
    #: prints these unwrapped, exactly as ``lc run`` does.
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether everything that was attempted finished."""
        return not self.failed and not self.blocked

    @property
    def up_to_date(self) -> bool:
        """Whether the analysis needed nothing done to it."""
        return not self.made and not self.planned

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "up_to_date": self.up_to_date, **asdict(self)}


# =============================================================================
# Check mode
# =============================================================================


def check(root: Path, targets: Sequence[str]) -> MaterializeReport:
    """Classify every task without executing or committing anything.

    Deliberately *not* subject to the dirty-tree refusal: reading the
    state of a project before deciding what to commit is exactly what this
    is for.

    The walk is topological because a task can only be classified after
    everything upstream of it. An upstream already classified as
    would-run is passed down as ``None``, meaning "this is going to
    change" — check mode cannot know whether a rebuild will come out
    byte-identical, and assuming it will would under-report. That single
    value is the whole difference between this and what a worker does;
    the rule itself lives in one place, in :func:`assets.staleness`.
    """
    report = MaterializeReport()
    graph, _ = _graph(root, targets, report)
    if drift := project.environment_drift(root):
        report.warnings.append(drift)

    versions = assets.Versions()
    stale: set[Key] = set()
    for key in graph.order():
        task = graph.tasks[key]
        reason = assets.staleness(
            code_version=task.code_version,
            manifest=assets.read(task.output_dir),
            inputs=_predicted(task, stale, versions),
        )
        name = _name(key)
        if reason is None:
            report.skipped.append(name)
        else:
            stale.add(key)
            report.planned[name] = str(reason)
    return report


def _predicted(task: Task, stale: set[Key], versions: assets.Versions) -> dict[str, str | None]:
    """Each input's version as check mode can know it.

    ``None`` for anything that will be rebuilt; the bytes on disk for
    everything else. A declared input that is not there at all is also
    ``None`` — it cannot be read now and running the recipe is what would
    be attempted, which is the honest classification.
    """
    predicted: dict[str, str | None] = {}
    for name, path in task.inputs.items():
        upstream = task.produced_by.get(name)
        if upstream is not None:
            # The upstream's *recorded* digest, for the same reason a worker
            # returns it rather than rehashing: on a clone that has fetched
            # no annex content the files are dangling symlinks, and hashing
            # them would report a different output and cascade a rebuild
            # over a project that is perfectly up to date.
            manifest = None if upstream in stale else assets.read(path)
            predicted[name] = manifest.data_version if manifest else None
        elif not path.exists():
            predicted[name] = None
        else:
            predicted[name] = versions.of(path)
    return predicted


# =============================================================================
# Executing
# =============================================================================


def materialize(
    root: Path, targets: Sequence[str], *, jobs: int | None = None
) -> MaterializeReport:
    """Make everything *targets* names, and commit each output as it lands."""
    require_uv()
    dataset.require_git()
    dataset.require_git_annex()
    if drift := project.environment_drift(root):
        raise ProjectError(drift)
    if changes := dataset.status(root):
        raise ProjectError(_dirty(root, changes))

    report = MaterializeReport()
    graph, env_version = _graph(root, targets, report)
    if not graph.tasks:
        return report

    dsid = dataset.dataset_id(root)
    # Read once, for every task: the driver commits each output as it lands,
    # so HEAD moves during the run, and a per-task read would give later
    # manifests a commit this run created — nondeterministically, depending
    # on whether a recipe finished before or after the previous save.
    head = dataset.head(root)
    # One memo for the run, for the same reason as one HEAD read: a
    # declared input shared by several outputs — or by one output across
    # several universes — is the same bytes every time it is asked for.
    versions = assets.Versions()
    outstanding: dict[Key, Task] = dict(graph.tasks)
    try:
        with cluster_for_run(jobs) as scheduler:
            pending: dict[Key, Any] = {}
            # Submitted in dependency order so a task's upstream futures
            # exist to be passed to it. Dask still derives the *execution*
            # order — from those arguments, not from this loop.
            for key in graph.order():
                task = graph.tasks[key]
                pending[key] = scheduler.submit(
                    worker.materialize,
                    root,
                    task,
                    env_version,
                    head,
                    versions,
                    *[pending[dep] for dep in task.depends_on],
                    key=_name(key),
                )
            for result in scheduler.completed(list(pending.values())):
                _consume(root, graph.tasks[result.key], result, dsid, report)
                outstanding.pop(result.key, None)
    finally:
        # Whatever never reported — an interrupt, a dead cluster — left a
        # reset output directory behind. Scoped to this run's outputs and
        # never to the whole tree, so edits made while the graph ran
        # survive.
        for task in outstanding.values():
            dataset.restore(root, [task.output_dir])
    return report


def _consume(
    root: Path, task: Task, result: worker.TaskResult, dsid: str, report: MaterializeReport
) -> None:
    """Record one finished task, and commit or undo what it left on disk."""
    name = _name(task.key)
    if lines := [note for note in result.notes if note]:
        # Named on a line of their own rather than prefixed onto each: a
        # prefix would land in the middle of a multi-line remedy and make
        # it uncopyable, which is the whole reason these travel separately.
        report.notes.extend([f"{name}:", *lines])

    if result.status == "ok":
        dataset.save(root, [task.output_dir], run_record(root, task, dsid))
        report.made.append(name)
        return

    if result.status == "skipped":
        report.skipped.append(name)
        return

    dataset.restore(root, [task.output_dir])
    getattr(report, result.status).append(name)
    report.warnings.append(f"{name}: {result.reason}")


class Scheduler(Protocol):
    """How the driver talks to whatever is running the graph.

    Two methods, because that is all the driver needs and all a venue has
    to supply: hand over a task with its upstream handles, and iterate the
    results as they land. Keeping it this narrow is what lets the suite
    run the graph inline — and what will let a venue larger than a laptop
    land behind :func:`cluster_for_run` without the driver noticing.
    """

    def submit(self, fn: Any, *args: Any, key: str) -> Any:
        """Schedule the call, returning a handle to pass downstream."""
        ...

    def completed(self, handles: list[Any]) -> Iterator[worker.TaskResult]:
        """The results, in the order they finish."""
        ...


@dataclass(frozen=True)
class _Dask:
    """A Dask client, narrowed to what the driver asks of it."""

    client: Any

    def submit(self, fn: Any, *args: Any, key: str) -> Any:
        return self.client.submit(fn, *args, key=key)

    def completed(self, handles: list[Any]) -> Iterator[worker.TaskResult]:
        # distributed ships no type information, so this one call is
        # annotated rather than the module exempted.
        from distributed import as_completed

        for _, result in as_completed(handles, with_results=True):  # type: ignore[no-untyped-call]
            yield result


@contextmanager
def cluster_for_run(jobs: int | None) -> Iterator[Scheduler]:
    """A scheduler for one run. The seam venues will land behind.

    Threads rather than processes: every task's real work happens in a
    subprocess behind the exec boundary, so the worker itself spends its
    time in ``wait()`` with the GIL released, and a threaded cluster costs
    no interpreter startup and no pickling of results.
    """
    from distributed import Client, LocalCluster

    with LocalCluster(  # type: ignore[no-untyped-call]
        n_workers=1,
        threads_per_worker=jobs or os.cpu_count() or 1,
        processes=False,
        dashboard_address=None,
    ) as cluster:
        with Client(cluster) as client:  # type: ignore[no-untyped-call]
            yield _Dask(client)


# =============================================================================
# The commit
# =============================================================================


def run_record(root: Path, task: Task, dsid: str) -> str:
    """The commit message for one materialized output.

    A ``[DATALAD RUNCMD]`` record, which is datalad's format rather than
    ours — so all of it is written, ``chain`` and ``dsid`` included, and
    none of it is abbreviated. ``datalad rerun`` reads it out of the
    message with a regex and reports "no command; skipping" on any
    mismatch, so the shape is not a matter of taste.

    ``cmd`` is the worker module, not the bare recipe and not
    ``lc materialize``. The bare recipe would reconstruct nothing lc adds
    — no locked environment, no boundary, no environment gates, no
    manifest — and would commit bytes the identity model never produced.
    ``lc materialize`` cannot be it either: a rerun removes the declared
    outputs first, which dirties the tree that materialize refuses to
    start from.
    """
    info = {
        "chain": [],
        "cmd": (
            "uv run --locked --project . -- python -m lightcone.engine.worker "
            f"{task.universe_id}/{task.output_id}"
        ),
        "dsid": dsid,
        "exit": 0,
        "inputs": sorted(_inside(root, path) for path in task.inputs.values()),
        "outputs": [_inside(root, task.output_dir)],
        "pwd": ".",
    }
    body = json.dumps(info, indent=1, sort_keys=True, ensure_ascii=False)
    return (
        f"[DATALAD RUNCMD] {task.output_id} [{task.universe_id}]\n\n"
        "=== Do not change lines below ===\n"
        f"{body}\n"
        "^^^ Do not change lines above ^^^"
    )


def _inside(root: Path, path: Path) -> str:
    """*path* relative to *root*, or absolute when it is somewhere else.

    Deliberately **not** resolved. Every declared input under ``data/`` is
    an annex symlink, so resolving would record
    ``.git/annex/objects/SHA256E-…`` — a path no one can ``datalad get``,
    naming the storage rather than the input. The record has to say what
    the analysis declared.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# =============================================================================
# Reading the project
# =============================================================================


def _graph(root: Path, targets: Sequence[str], report: MaterializeReport) -> tuple[Graph, str]:
    """The tasks a run covers, and the environment they are identified against.

    The lock scan runs here, once, for both modes: what it refuses is a
    dependency whose bytes the lock does not pin, which makes every hash
    below it a claim nobody can check.
    """
    scan = identity.scan_lock(root)
    if scan.refusals:
        raise ProjectError(
            "the lock has dependencies that cannot be audited, so an output's "
            "identity would not mean anything:\n  "
            + "\n  ".join(scan.refusals)
            + "\nPublish them, or vendor them into the project."
        )
    if scan.sdist_built:
        report.warnings.append(
            "built from source at sync time, so identity covers the sdist and "
            f"not the build of it: {', '.join(scan.sdist_built)}"
        )
    if scan.non_default_groups:
        report.warnings.append(
            "dependency groups outside uv's default set are installable states "
            f"the environment's identity does not distinguish: {', '.join(scan.non_default_groups)}"
        )

    env_version = identity.env_version(root)
    graph = plan.build(root, env_version=env_version)
    if targets:
        graph = graph.closure(graph.resolve(list(targets)))
    return graph, env_version


def _name(key: Key) -> str:
    return f"{key[0]}/{key[1]}"


def _dirty(root: Path, changes: Sequence[tuple[str, str]]) -> str:
    """The refusal, split by what the right remedy actually is.

    Two path classes, because they call for opposite actions: work the
    researcher owns has to be committed, and anything under ``results/``
    is lc's to write, so a change there is wreckage to discard rather than
    a contribution to keep.
    """
    theirs = [c for c in changes if not c[1].startswith("results/")]
    ours = [c for c in changes if c[1].startswith("results/")]

    lines = [
        f"uncommitted changes in {root} — every materialization is committed "
        "with the code that produced it, so a run cannot start from a tree "
        "that does not say what that code is.",
    ]
    if theirs:
        lines += [
            "",
            '  commit these:   git annex add . && git add -A . && git commit -m "…"',
            *(f"      {code.strip() or '??'} {path}" for code, path in theirs),
        ]
    if ours:
        lines += [
            "",
            "  discard these (lc writes results/):",
            "      git restore --staged --worktree results/ && git clean -fd results/",
            *(f"      {code.strip() or '??'} {path}" for code, path in ours),
        ]
    return "\n".join(lines)
