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
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from lightcone.engine import assets, container, dataset, identity, plan, project, worker
from lightcone.engine.plan import Graph, Key, Task
from lightcone.engine.project import ProjectError


@dataclass
class MaterializeReport:
    """What a run did, or — in check mode — what it would do."""

    #: ``universe/output`` for each output this run produced and committed.
    made: list[str] = field(default_factory=list)
    #: Nothing about them changed, and the environment is the one they were
    #: made under.
    current: list[str] = field(default_factory=list)
    #: Still what the spec asks for, but made under an earlier environment
    #: — ``universe/output`` → the context, naming the commit. Left alone;
    #: ``refresh`` is what remakes them.
    behind: dict[str, str] = field(default_factory=dict)
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
        """Whether the analysis needed nothing done to it.

        ``behind`` does not count against it. An output made under an
        earlier environment is not out of date — it is what the spec asks
        for, and saying otherwise would put ``--check`` back in the
        business of demanding compute.

        A run that failed is not up to date either, however little it
        managed to produce: ``made`` stays empty when every recipe fails,
        so without ``ok`` here the first two keys of the JSON report would
        read "nothing to do" over a list of failures.
        """
        return self.ok and not self.made and not self.planned

    def as_dict(self) -> dict[str, Any]:
        """Return the report as JSON-ready data.

        Returns:
            Every field, with ``ok`` and ``up_to_date`` first.
        """
        return {"ok": self.ok, "up_to_date": self.up_to_date, **asdict(self)}


# =============================================================================
# Check mode
# =============================================================================


def check(root: Path, targets: Sequence[str], *, refresh: bool = False) -> MaterializeReport:
    """Classify every task without executing or committing anything.

    Not subject to the dirty-tree refusal: reading the state of a project
    before deciding what to commit is what this is for. The walk is
    topological because a task can only be classified after everything
    upstream of it, and an upstream already classified as would-run is
    passed down as ``None`` — check mode cannot know whether a rebuild
    comes out byte-identical, and assuming it will would under-report.

    Args:
        root: The project root.
        targets: What to classify; empty means everything.
        refresh: Whether an output that is merely behind would be remade.

    Returns:
        ``planned`` naming each output that would run and why, ``behind``
        those made under an earlier environment, and ``current`` the
        rest.

    Raises:
        ProjectError: If the spec, the universes or the lock cannot be
            read, or a target matches nothing.
    """
    report = MaterializeReport()
    for key, verdict, _ in _classified(root, targets, report, refresh=refresh):
        name = _name(key)
        if verdict.calls_for_a_remake(refresh=refresh):
            report.planned[name] = verdict.why
        elif verdict.status == "behind":
            report.behind[name] = verdict.why
        else:
            report.current.append(name)
    return report


def _classified(
    root: Path, targets: Sequence[str], report: MaterializeReport, *, refresh: bool
) -> list[tuple[Key, assets.Verdict, assets.Manifest | None]]:
    """Classify every task in topological order, reading nothing but disk.

    The walk both read-only modes share, so there is one answer to "what
    is this output" and not one per verb. Topological because a task can
    only be classified after everything upstream of it, and an upstream
    already decided to run is passed down as ``None`` — nothing here can
    know whether a rebuild comes out byte-identical, and assuming it will
    would under-report.

    Args:
        root: The project root.
        targets: What to classify; empty means everything.
        report: Collects the lock scan's warnings, and the note about
            inputs this clone has not fetched.
        refresh: Whether behind outputs count as running, which is what
            decides whether their dependents see the sentinel.

    Returns:
        One ``(key, verdict, manifest)`` per task, upstream first.
    """
    graph, env_version = _graph(root, targets, report)
    versions = assets.Versions()
    would_run: set[Key] = set()
    unfetched: set[str] = set()

    classified = []
    for key in graph.order():
        task = graph.tasks[key]
        manifest = assets.read(task.output_dir)
        verdict = assets.classify(
            definition_version=task.definition_version,
            env_version=env_version,
            manifest=manifest,
            inputs=_predicted(root, task, would_run, versions, unfetched),
        )
        if verdict.calls_for_a_remake(refresh=refresh):
            would_run.add(key)
        classified.append((key, verdict, manifest))

    if unfetched:
        report.warnings.append(
            "reported as out of date because their content is not in this "
            f"clone, not because they changed: {', '.join(sorted(unfetched))}. "
            "Fetch with `git annex get <path>`."
        )
    return classified


def _predicted(
    root: Path,
    task: Task,
    would_run: set[Key],
    versions: assets.Versions,
    unfetched: set[str],
) -> dict[str, str | None]:
    """Each input's version as check mode can know it.

    Args:
        root: The project root, for naming a path in the report.
        task: The output being classified.
        would_run: Task keys already decided to be remade.
        versions: The run's content-hash memo.
        unfetched: Collects declared inputs whose content is not local, so
            the report can say why they read as out of date.

    Returns:
        Each input's version as check mode can know it: ``None`` for
        anything that will be rebuilt, is absent, or cannot be read.
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
            manifest = None if upstream in would_run else assets.read(path)
            predicted[name] = manifest.data_version if manifest else None
        elif not path.exists():
            predicted[name] = None
        else:
            try:
                predicted[name] = versions.of(path)
            except assets.ContentNotFetchedError:
                # Not "it changed" — "I cannot tell". Conservative, and
                # said out loud, because reporting a rebuild for a clone
                # that simply has not fetched its inputs is misleading on
                # its own.
                unfetched.add(plan.declared_path(root, path))
                predicted[name] = None
            except OSError:
                # Unreadable for any other reason — a broken symlink inside
                # the directory, a permission wall. Same answer as an input
                # that is not there at all: it will be remade, and the
                # recipe is where that failure belongs, with a real error.
                predicted[name] = None
    return predicted


# =============================================================================
# Status — what the project holds, and where each of it came from
# =============================================================================


@dataclass(frozen=True)
class OutputStatus:
    """One output: what it is now, and the commit it came from."""

    #: ``universe/output_id``.
    output: str
    status: assets.Status
    #: Why, for ``stale`` and ``behind``. Empty for ``current``.
    why: str
    #: The commit the output was materialized at, or empty if it never was.
    #: This is the whole point of the verb: an artifact that is behind is
    #: not wrong, and this is where the code and environment that produced
    #: it can be read back.
    git_sha: str
    #: Its content identity, or empty if it was never materialized.
    data_version: str

    def as_dict(self) -> dict[str, Any]:
        """Return the record as JSON-ready data.

        Returns:
            Every field, in declaration order.
        """
        return asdict(self)


@dataclass
class StatusReport:
    """Every output the spec declares, in dependency order."""

    outputs: list[OutputStatus] = field(default_factory=list)
    #: The lock scan's prose, and inputs this clone has not fetched.
    warnings: list[str] = field(default_factory=list)
    #: The three header facts nothing else surfaces: which world this
    #: project executes in, where its image stands, and what would
    #: enforce a run on this host. This is where the denial note and the
    #: runtime-missing refusal point.
    mode: str = "direct"
    #: ``{tag, state}`` for a containerized project; ``None`` in direct
    #: mode. ``state`` is repository fact only — ``present``, ``absent``
    #: or ``unfetched`` — so status needs no runtime and no network.
    image: dict[str, str] | None = None
    #: One line naming the enforcement a run here would get.
    sandbox: str = ""

    @property
    def counts(self) -> dict[str, int]:
        """How many outputs are in each state, states with none included."""
        tally = {"current": 0, "behind": 0, "stale": 0}
        for output in self.outputs:
            tally[output.status] += 1
        return tally

    def as_dict(self) -> dict[str, Any]:
        """Return the report as JSON-ready data.

        Returns:
            The counts, then every output, then the warnings.
        """
        return {
            "mode": self.mode,
            "image": self.image,
            "sandbox": self.sandbox,
            "counts": self.counts,
            "outputs": [output.as_dict() for output in self.outputs],
            "warnings": self.warnings,
        }


def status(root: Path) -> StatusReport:
    """Report what state every declared output is in.

    Reads manifests and hashes declared inputs; runs nothing, commits
    nothing, and does not care whether the tree is clean. Classified with
    ``refresh=False``, because this says what the project *is* rather than
    what some run would do to it.

    Args:
        root: The project root.

    Returns:
        One record per declared output, upstream first.

    Raises:
        ProjectError: If the spec, the universes or the lock cannot be
            read.
    """
    report = MaterializeReport()
    result = StatusReport()
    result.mode = project.mode(root)
    state, tag, archive = container.image_state(root)
    if state != "direct":
        result.image = {"tag": tag, "state": state, "archive": archive}
    result.sandbox = _sandbox_line(result.mode)
    for key, verdict, manifest in _classified(root, [], report, refresh=False):
        result.outputs.append(
            OutputStatus(
                output=_name(key),
                status=verdict.status,
                why=verdict.why,
                git_sha=manifest.git_sha if manifest else "",
                data_version=manifest.data_version if manifest else "",
            )
        )
    result.warnings = report.warnings
    return result


def _sandbox_line(mode: str) -> str:
    """One line naming the enforcement a run on this host would get.

    The prose restates each backend's constant attestation, because
    `Backend.attest` needs a built policy and a status header must not
    build one. Keep it in step with the `attest` implementations — the
    manifests, which record the real thing, are always authoritative.
    """
    if mode == "containerized":
        if runtime := container.runtime_hint():
            return f"{runtime} (fs: declared, network: denied)"
        return "no container runtime — install podman (or docker)"
    from lightcone.engine import sandbox

    found = sandbox.detect().capability
    if found.kind == "none":
        detail = f" — {found.detail}" if found.detail else ""
        return f"none{detail}; runs record `fs: open`"
    return f"{found.kind} (fs: declared, network: allowed)"


# =============================================================================
# Executing
# =============================================================================


def materialize(
    root: Path, targets: Sequence[str], *, refresh: bool = False
) -> MaterializeReport:
    """Make everything *targets* names, committing each output as it lands.

    Args:
        root: The project root.
        targets: What to make; empty means everything. Asking for an
            output asks for what it is made of.
        refresh: Also remake outputs that are merely behind — still what
            the spec asks for, but made under an earlier environment.

    Returns:
        What was made, what was current or behind, what failed or was
        blocked, plus the boundary's notes and the lock scan's warnings.

    Raises:
        ProjectError: If a required tool or git's committer identity is
            missing, the tree has uncommitted changes, or the lock cannot
            be audited.
    """
    project.require_uv()
    project.require_git()
    project.require_git_annex()
    dataset.require_committer(root)
    report = MaterializeReport()
    # The dirty check comes before anything that writes: the image
    # converge below *commits*, and `dataset.save` stages scoped but
    # commits the whole index — on a dirty tree the user's staged edits
    # would be swept into the image commit.
    if changes := dataset.status(root):
        raise ProjectError(_dirty(root, changes))
    # The graph — and with it the spec validation and the lock scan —
    # before the image: a refusal here must not cost a minutes-long
    # build, and must not leave an archive commit behind a run that
    # "failed" on a typo in the spec.
    graph, env_version = _graph(root, targets, report)
    if not graph.tasks:
        return report
    # Materialize is one of the two verbs allowed to build the image (the
    # other is `lc build`); the probe and the rerun entry point only find
    # one. Resolved once, then handed to every task — the HEAD discipline.
    runtime = container.runtime_for_run(root, build=True)
    # Converge the environment: workers pass `--no-sync`, so this is the
    # only place on a run's path where it is made to match the lock. (A
    # rerun does not come through here; its entry point converges too.)
    report.warnings.extend(f"uv: {w}" for w in container.converge(runtime))

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
        with cluster_for_run() as scheduler:
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
                    refresh,
                    runtime,
                    *[pending[dep] for dep in task.depends_on],
                    key=_name(key),
                )
            for result in scheduler.completed(list(pending.values())):
                _consume(root, graph.tasks[result.key], result, dsid, runtime, report)
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
    root: Path,
    task: Task,
    result: worker.TaskResult,
    dsid: str,
    runtime: container.Runtime,
    report: MaterializeReport,
) -> None:
    """Record one finished task, and commit or undo what it left on disk."""
    name = _name(task.key)
    if lines := [note for note in result.notes if note]:
        # Named on a line of their own rather than prefixed onto each: a
        # prefix would land in the middle of a multi-line remedy and make
        # it uncopyable, which is the whole reason these travel separately.
        report.notes.extend([f"{name}:", *lines])

    if result.status == "ok":
        dataset.save(root, [task.output_dir], run_record(root, task, dsid, runtime))
        report.made.append(name)
        return

    if result.status == "current":
        report.current.append(name)
        return

    if result.status == "behind":
        report.behind[name] = result.reason
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
        """Schedule a call.

        Args:
            fn: The function to run.
            *args: Its arguments, upstream handles included.
            key: A display name for the task.

        Returns:
            A handle to pass to dependents.
        """
        ...

    def completed(self, handles: list[Any]) -> Iterator[worker.TaskResult]:
        """Yield results as they land.

        Args:
            handles: Everything submitted.

        Yields:
            Each task's result, in completion order.
        """
        ...


@dataclass(frozen=True)
class _Dask:
    """A Dask client, narrowed to what the driver asks of it."""

    client: Any

    def submit(self, fn: Any, *args: Any, key: str) -> Any:
        """Schedule a call on the Dask client. See :class:`Scheduler`."""
        return self.client.submit(fn, *args, key=key)

    def completed(self, handles: list[Any]) -> Iterator[worker.TaskResult]:
        """Yield results as Dask completes them. See :class:`Scheduler`."""
        # distributed ships no type information, so this one call is
        # annotated rather than the module exempted.
        from distributed import as_completed

        for _, result in as_completed(handles, with_results=True):  # type: ignore[no-untyped-call]
            yield result


@contextmanager
def cluster_for_run() -> Iterator[Scheduler]:
    """Open a scheduler for one run. The seam venues will land behind.

    Every core, with no knob to say otherwise: how much of a machine a run
    may use, and which machine, is one question and it belongs to the
    declaration of an execution backend.

    Threads rather than processes — every task's real work happens in a
    subprocess behind the exec boundary, so a worker spends its time in
    ``wait()`` with the GIL released, and a threaded cluster costs no
    interpreter startup and no pickling of results.

    Yields:
        A scheduler bound to a local Dask cluster, closed on exit.
    """
    from distributed import Client, LocalCluster

    with LocalCluster(  # type: ignore[no-untyped-call]
        n_workers=1,
        threads_per_worker=os.cpu_count() or 1,
        processes=False,
        dashboard_address=None,
    ) as cluster:
        with Client(cluster) as client:  # type: ignore[no-untyped-call]
            yield _Dask(client)


# =============================================================================
# The commit
# =============================================================================


def run_record(root: Path, task: Task, dsid: str, runtime: container.Runtime) -> str:
    """Build the commit message for one materialized output.

    A ``[DATALAD RUNCMD]`` record — datalad's format, not ours, so all of
    it is written and none of it abbreviated. ``datalad rerun`` reads it
    with a regex and reports "no command; skipping" on any mismatch.

    ``cmd`` is the worker module, never a console script, behind an
    ephemeral ``uv run`` pinning the engine that made the output
    (:func:`_engine_requirement`) — so the rerun executes that engine
    rather than whatever the host has grown into. The bare recipe would
    reconstruct nothing lc adds, and ``lc materialize`` cannot be it
    because a rerun removes the declared outputs first, dirtying the tree
    materialize refuses to start from. The worker rebuilds the *project*
    environment itself from the lock of the commit being rerun.

    Args:
        root: The project root.
        task: The output that was made.
        dsid: The dataset's UUID, which ``rerun`` reads.
        runtime: The run's execution world. A containerized run lists its
            committed image archive under ``extra_inputs``, which
            ``datalad rerun`` fetches before executing — so the worker
            enters the exact bytes that made the output, on whatever
            runtime the rerun host has. The ``cmd`` itself stays
            runtime-neutral for the same reason: the worker is the
            executor that resolves the container at rerun time.

    Returns:
        The full commit message, subject and record.
    """
    info = {
        "chain": [],
        # Single-quoted because datalad hands cmd to a shell and the git
        # form of the requirement contains spaces.
        "cmd": (
            f"uv run --no-project --with '{_engine_requirement()}' -- "
            f"python -m lightcone.engine.worker {task.universe_id}/{task.output_id}"
        ),
        "dsid": dsid,
        "exit": 0,
        "inputs": sorted(plan.declared_path(root, path) for path in task.inputs.values()),
        "outputs": [plan.declared_path(root, task.output_dir)],
        "pwd": ".",
    }
    if runtime.mode == "containerized":
        info["extra_inputs"] = [runtime.archive]
    body = json.dumps(info, indent=1, sort_keys=True, ensure_ascii=False)
    return (
        f"[DATALAD RUNCMD] {task.output_id} [{task.universe_id}]\n\n"
        "=== Do not change lines below ===\n"
        f"{body}\n"
        "^^^ Do not change lines above ^^^"
    )


def _engine_requirement() -> str:
    """Build the requirement that reconstructs the running engine.

    A release pins by version, resolvable from an index. A dev build's
    version is not published, but hatch-vcs embeds its source commit —
    so the pin becomes that commit at the engine's own repository, and a
    rerun during development still reconstructs the engine that ran. An
    unpushed commit fails a rerun loudly at resolution, which beats
    silently finding another engine. A dirty tree is the one
    approximation: the commit names the code as last committed, and the
    version's own dirty marker is what keeps that visible.

    Returns:
        A PEP 508 requirement for ``uv run --with``.
    """
    v = worker.lc_version()
    commit = re.search(r"\+g([0-9a-f]+)", v)
    if "dev" not in v or commit is None:
        return f"lightcone-cli=={v}"
    return f"lightcone-cli @ git+{_repository_url()}@{commit.group(1)}"


def _repository_url() -> str:
    """Read the engine's repository URL out of its own metadata.

    From ``[project.urls]`` rather than a constant here, so the one place
    the URL lives is the package metadata every install carries.
    """
    from importlib.metadata import metadata

    for entry in metadata("lightcone-cli").get_all("Project-URL") or []:
        label, _, url = str(entry).partition(",")
        if label.strip().lower() == "repository":
            return url.strip()
    raise ProjectError(
        "lightcone-cli's own metadata names no Repository URL, so a dev "
        "engine cannot be pinned by commit — reinstall the engine."
    )


# =============================================================================
# Reading the project
# =============================================================================


def _graph(root: Path, targets: Sequence[str], report: MaterializeReport) -> tuple[Graph, str]:
    """The tasks a run covers, and the environment they will be compared to.

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
    graph = plan.build(root)
    if targets:
        graph = graph.closure(graph.resolve(list(targets)))

    # A declared input outside the project is hashed into the manifest like
    # any other, so a change to it still cascades — but it is not in the
    # repository, so the commit that records the output cannot bring it
    # back. That is a weaker promise than the rest of the layer makes, and
    # the only honest thing to do about it is say so.
    outside = {
        plan.declared_path(root, path)
        for task in graph.tasks.values()
        for name, path in task.inputs.items()
        if name not in task.produced_by and root not in path.parents
    }
    if outside:
        report.warnings.append(
            "declared inputs outside the project are recorded by content but "
            "not stored in it, so a commit cannot restore them: "
            + ", ".join(sorted(outside))
        )
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
            '  commit these:   git add -A . && git commit -m "…"',
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
