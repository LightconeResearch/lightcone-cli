"""Making a whole analysis: what runs, in what order, and what gets committed.

The driver's three jobs, and the order matters.

**It refuses to start on a dirty tree.** Every materialization is
committed together with the code that produced it, so a run that began
with uncommitted changes could not honestly say which code that was.

**It hands the work to Dask and gets out of the way.** Tasks that may run
receive upstream futures or already-current results as arguments, so the
ordering, parallelism, and scheduling are Dask's — there is no ready-set
loop here to get wrong.

**It owns git, alone.** Workers execute and return; the driver commits, in
one thread, as results arrive. That is not a preference: concurrent git
operations on one repository race on the index lock. The same loop
restores what a completed failed task left behind. Unreported tasks may
still be writing, so interruptions retain their partial files.

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

import functools
import json
import posixpath
import re
import shlex
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from lightcone.engine import assets, container, dataset, identity, plan, project, worker
from lightcone.engine.execution_resources import TaskResources, worker_capacities
from lightcone.engine.plan import Graph, Key, Task
from lightcone.engine.project import ProjectError

if TYPE_CHECKING:
    from astra.records import Record

    from lightcone.engine.compute.output import Forwarder


@dataclass
class MaterializeReport:
    """What a run did, or — in check mode — what it would do."""

    #: Each result this run produced and committed, as lc names its task.
    made: list[str] = field(default_factory=list)
    #: Nothing about them changed, and the environment is the one they were
    #: made under.
    current: list[str] = field(default_factory=list)
    #: Still what the spec asks for, but made under an earlier environment
    #: — task → the context. Left alone;
    #: ``refresh`` is what remakes them.
    behind: dict[str, str] = field(default_factory=dict)
    #: The recipe failed, or the environment moved under it.
    failed: list[str] = field(default_factory=list)
    #: Not attempted, because something upstream did not finish.
    blocked: list[str] = field(default_factory=list)
    #: Check mode only: task → why it would run.
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
    before deciding what to commit is what this is for.

    Args:
        root: The project root.
        targets: What to classify; empty means everything.
        refresh: Whether a result that is merely behind would be remade.

    Returns:
        ``planned`` naming each result that would run and why, ``behind``
        those made under an earlier environment, and ``current`` the
        rest.

    Raises:
        ProjectError: If the analysis or the lock cannot be read, or a
            target matches nothing.
    """
    report = MaterializeReport()
    graph, environment, _ = _graph(root, targets, report)
    for found in _classify(root, graph, environment, refresh=refresh):
        verdict = found.decided.verdict
        if verdict.calls_for_a_remake(refresh=refresh):
            report.planned[found.task.key] = verdict.why
        elif found.upstream:
            # Nothing here can know whether a rebuild comes out
            # byte-identical, and assuming it will would under-report.
            report.planned[found.task.key] = (
                f"`{found.upstream}`, which it reads, would be remade"
            )
        elif verdict.status == "behind":
            report.behind[found.task.key] = verdict.why
        else:
            report.current.append(found.task.key)
    return report


@dataclass(frozen=True)
class _Classified:
    """One task, and what the project's state says of its result."""

    task: Task
    decided: worker.Decided
    #: The record at the result's first place, where it has one of its own.
    record: Record | None
    #: The commit that last wrote the result's files, if not its own run.
    foreign: dataset.LastWrite | None
    #: A task upstream of this one that may run, or empty. Only once it
    #: has landed can anything say whether this result still stands.
    upstream: Key
    #: ASTRA finds no valid record, so no upstream outcome can spare it.
    missing: bool

    def may_run(self, *, refresh: bool) -> bool:
        """Whether a run has to hand this task to a worker."""
        return bool(self.upstream) or self.decided.verdict.calls_for_a_remake(refresh=refresh)


def _classify(
    root: Path, graph: Graph, environment: str, *, refresh: bool
) -> list[_Classified]:
    """Classify every task of *graph*, upstream first, reading nothing but
    the project.

    The walk every mode shares, so there is one answer to "what is this
    result" and not one per verb: ASTRA's checker is asked once, and each
    task's findings go through the one classification rule, with the
    history only the driver can read.

    Args:
        root: The project root.
        graph: The tasks to classify, each after the tasks it reads.
        environment: The IRI of the project's environment.
        refresh: Whether behind results count as running, which is what
            decides whether their dependents may run too.

    Returns:
        One entry per task, in the graph's order.
    """
    checked = assets.check(root)
    may_run: set[Key] = set()
    classified = []
    for key, task in graph.tasks.items():
        found = checked[task.places[0].record]
        record = found[0].record
        # History is git's to answer, so it enters the one classification
        # rule as a value — computed here, where git lives.
        foreign = None if record is None else _foreign_write(root, task)
        entry = _Classified(
            task,
            worker.decide(found, environment, foreign),
            record,
            foreign,
            upstream=next((dep for dep in task.depends_on if dep in may_run), ""),
            missing=any(status.record_status == "missing" for status in found),
        )
        if entry.may_run(refresh=refresh):
            may_run.add(key)
        classified.append(entry)
    return classified


# =============================================================================
# Status — what the project holds, and where each of it came from
# =============================================================================


@dataclass(frozen=True)
class OutputStatus:
    """One output: what it is now, and the commit it came from."""

    #: The result's task, as lc names it (``fit#baseline``).
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
    #: Empty when the commit that last touched the result's files is its
    #: own run. Otherwise that foreign commit's sha — the
    #: agent-forged-file fact, which also makes the result read `stale`:
    #: the record no longer describes the bytes, and a skip would
    #: return the recorded digest forever. The sha rather than prose,
    #: so a machine consumer gets what `why`'s sentence cannot carry.
    #: History-based on purpose: it costs no hashing and answers on a
    #: clone that holds none of the bytes. What it leaves to existing
    #: tools: uncommitted edits are a dirty tree, and annex object
    #: corruption is `git annex fsck`.
    foreign_write: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the record as JSON-ready data.

        Returns:
            Every field, in declaration order.
        """
        return asdict(self)


@dataclass
class StatusReport:
    """Every result the analysis plans, in dependency order."""

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
    #: Where the publication view stands — maintained, and if so whether
    #: it still reflects the outputs. Repository facts only, like the
    #: rest of the header.
    crate: str = ""

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
            "crate": self.crate,
            "counts": self.counts,
            "outputs": [output.as_dict() for output in self.outputs],
            "warnings": self.warnings,
        }


def status(root: Path) -> StatusReport:
    """Report what state every planned result is in.

    Reads records and hashes declared inputs; runs nothing, commits
    nothing, and does not care whether the tree is clean. Classified with
    ``refresh=False``, because this says what the project *is* rather than
    what some run would do to it.

    Args:
        root: The project root.

    Returns:
        One record per planned result, upstream first.

    Raises:
        ProjectError: If the analysis or the lock cannot be read.
    """
    report = MaterializeReport()
    result = StatusReport()
    result.mode = project.mode(root)
    state, tag, archive = container.image_state(root)
    if state != "direct":
        result.image = {"tag": tag, "state": state, "archive": archive}
    result.sandbox = _sandbox_line(result.mode)
    stamps = []
    graph, environment, _ = _graph(root, [], report)
    for found in _classify(root, graph, environment, refresh=False):
        if found.record and found.record.finished:
            stamps.append(found.record.finished)
        result.outputs.append(
            OutputStatus(
                output=found.task.key,
                status=found.decided.verdict.status,
                why=found.decided.verdict.why,
                git_sha=found.record.commit if found.record else "",
                data_version=found.record.content_hash if found.record else "",
                foreign_write=found.foreign.sha if found.foreign else "",
            )
        )
    result.crate = _crate_line(root, max(stamps, default=""))
    result.warnings = report.warnings
    return result


def _crate_line(root: Path, newest: str) -> str:
    """One line placing the publication view, from repository facts alone.

    Lag is read off the document itself, not history: the render pins
    ``datePublished`` to the newest record's ``finished``, so a date
    that no longer matches the records — in either direction, a rerun
    adds an output the view predates and a dropped output regresses the
    newest — means the view no longer describes the outputs. That is the
    line's whole claim, and it is worded to it: a crate-affecting edit
    that moves no record (the lock's bytes, the spec) is invisible
    here, and fine — the next materialize converges those anyway, where
    a rerun's lag has no other surface. No rocrate import (the crate is
    the one materialize-only dependency on status's path) and no git:
    the records were already read by the walk.
    """
    spdx = project.license_of(root)
    path = root / project.CRATE_FILENAME
    if not spdx:
        if path.is_file():
            return "no longer maintained — pyproject.toml declares no [project].license"
        return "not maintained — declare [project].license to enable it"
    if not path.is_file():
        return "will be created by the next `lc materialize`"
    try:
        entities = json.loads(path.read_text()).get("@graph", [])
        published = next(
            (str(e.get("datePublished", "")) for e in entities if e.get("@id") == "./"), ""
        )
    except (OSError, ValueError, AttributeError):
        return "unreadable — the next `lc materialize` rewrites it"
    if newest and published != newest:
        return "behind the outputs — `lc materialize` refreshes it"
    return "up to date with the outputs"


def _foreign_write(root: Path, task: Task) -> dataset.LastWrite | None:
    """Find the commit that last touched *task*'s files, unless it is the
    result's own run — then ``None``, the clean answer. The fact only:
    the verdict's prose is `classify`'s, like every other why.

    Both files at every place: a forged *result* is the case the check
    exists for, and a doctored *record* is the other half — while the
    record is also what carries the history across a re-declared format,
    where the payload's own path is new and would read as clean.
    """
    write = dataset.last_writer(root, *_files(task))
    if not write or write.subject == datalad_run_subject(task.key):
        return None
    return write


def _files(task: Task) -> list[str]:
    """The bytes and the record at each of *task*'s places."""
    return [path for place in task.places for path in (place.bytes, place.record)]


def _sandbox_line(mode: str) -> str:
    """One line naming the enforcement a run on this host would get.

    The prose restates each backend's constant attestation, because
    `Backend.attest` needs a built policy and a status header must not
    build one. Keep it in step with the `attest` implementations.
    """
    if mode == "containerized":
        if runtime := container.runtime_hint():
            return f"{runtime} (fs: declared, network: allowed)"
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
    root: Path, targets: Sequence[str], *, cluster_id: str, refresh: bool = False
) -> MaterializeReport:
    """Materialize selected outputs on an explicitly selected, borrowed cluster.

    Args:
        root: The project root.
        targets: Outputs to make, including dependencies; empty means everything.
        cluster_id: The name or immutable ID returned by ``lc compute launch``.
        refresh: Also remake outputs produced under an earlier environment.

    Returns:
        Completed, current, failed and blocked outputs and their diagnostics.

    Raises:
        ProjectError: If the cluster is unavailable or the project cannot be safely prepared.
    """
    project.require_uv()
    project.require_git()
    project.require_git_annex()
    dataset.require_committer(root)
    report = MaterializeReport()
    if warning := project.uv_scrub_warning():
        report.warnings.append(warning)
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
    graph, environment, full = _graph(root, targets, report)
    dsid = dataset.dataset_id(root)
    if not graph.tasks:
        # No tasks is not no project: a spec whose outputs were all dropped
        # still has a crate describing them, and this is its only
        # maintainer. Nothing is submitted, so no allocation is needed.
        _converge_crate(root, report, full, dsid)
        return report
    classified = _classify(root, graph, environment, refresh=refresh)
    with cluster_for_run(cluster_id) as scheduler:
        requirements = scheduler.validate(
            found.task for found in classified if found.may_run(refresh=refresh)
        )
        _fetch_inputs(root, graph, report)
        # Materialize is one of the two verbs allowed to build the image (the
        # other is `lc build`); the probe and the rerun entry point only find
        # one. Resolved once, then handed to every task — the HEAD discipline.
        runtime = container.runtime_for_run(
            root, build=True, use_gpus=any(r.get("GPU", 0) for r in requirements.values()),
        )
        # Converge the environment: workers pass `--no-sync`, so this is the
        # only place on a run's path where it is made to match the lock. (A
        # rerun does not come through here; its entry point converges too.)
        report.warnings.extend(f"uv: {w}" for w in container.converge(runtime))
        # The run's driver-resolved facts, each read once: HEAD because the
        # driver commits as outputs land and a per-task read would stamp
        # later records with a commit this run created.
        commit, _ = dataset.head(root)
        context = worker.RunContext(environment=environment, commit=commit, runtime=runtime)
        pending: dict[Key, Any] = {}
        handles = []
        # Results left alone are values, not Dask tasks: they need no allocation
        # resources. Futures for everything that may run retain dependency order.
        for found in classified:
            task, decided = found.task, found.decided
            if not found.may_run(refresh=refresh):
                result = worker.TaskResult(
                    task.key,
                    decided.verdict.status,  # type: ignore[arg-type]  # stale may run
                    decided.content_hash,
                    decided.size,
                    reason=decided.verdict.why,
                )
                pending[task.key] = result
                _consume(root, task, result, dsid, runtime, report)
                continue
            pending[task.key] = scheduler.submit(
                worker.materialize,
                root,
                task,
                context,
                refresh,
                # Downstream of a remake, what the driver found may not
                # survive it, so the task asks again once its upstreams have
                # landed — unless it has no record, which nothing upstream
                # can spare.
                None if found.upstream and not found.missing else decided,
                found.foreign,
                *[pending[dep] for dep in task.depends_on],
                key=task.key,
                resources=requirements[task.key],
            )
            handles.append(pending[task.key])
        from lightcone.engine.compute import UNSTOPPED

        # An unreported task can still have a running subprocess. Leave its
        # partial files in place on interruption rather than restoring over it.
        outstanding = len(handles)
        for result in scheduler.completed(handles):
            outstanding -= 1
            try:
                _consume(root, graph.tasks[result.key], result, dsid, runtime, report)
            except Exception as exc:
                if not outstanding:
                    raise
                raise ProjectError(f"{exc}. {UNSTOPPED}") from exc
        # The tree was clean at the start-of-run refusal and save/restore
        # keeps results clean, so anything dirty *now* was edited while
        # the graph ran — and every record names the starting commit,
        # which no longer describes that code. A warning, never a record
        # field: the driver does not rewrite files the worker owns.
        if edited := dataset.status(root):
            names = ", ".join(sorted(path for _, path in edited))
            report.warnings.append(
                f"edited while the run was in flight: {names} — the records "
                "name the starting commit, which no longer describes this code"
            )
        _converge_crate(root, report, full, dsid)
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
    name = task.key
    if result.notes:
        # Named on a line of their own rather than prefixed onto each: a
        # prefix would land in the middle of a multi-line remedy and make
        # it uncopyable, which is the whole reason these travel separately.
        report.notes.extend([f"{name}:", *result.notes])

    if result.status == "ok":
        dataset.save(root, _owned(task), run_record(root, task, dsid, runtime))
        report.made.append(name)
        return

    if result.status == "current":
        report.current.append(name)
        return

    if result.status == "behind":
        report.behind[name] = result.reason
        return

    dataset.restore(root, _owned(task))
    getattr(report, result.status).append(name)
    report.warnings.append(f"{name}: {result.reason}")


class Scheduler(Protocol):
    """How the driver talks to whatever is running the graph.

    Hand over tasks with their upstream handles and iterate results as
    they land, keeping the commit logic independent of the provider.
    """

    def validate(self, tasks: Iterable[Task]) -> dict[Key, dict[str, float]]:
        """Validate all requests and return their Dask resource reservations."""
        ...

    def submit(self, fn: Any, *args: Any, key: str, resources: dict[str, float]) -> Any:
        """Schedule a call.

        Args:
            fn: The function to run.
            *args: Its arguments, upstream handles included.
            key: A display name for the task.
            resources: Validated reservations for this task.

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
    """A borrowed Dask client, narrowed to what the graph driver needs."""

    client: Any
    invocation: str
    output: Forwarder
    workers: dict[str, Any]

    def validate(self, tasks: Iterable[Task]) -> dict[Key, dict[str, float]]:
        """Require each selected task to fit a worker before any task starts."""
        capacities = worker_capacities(self.workers)
        requests = {}
        for task in tasks:
            try:
                requests[task.key] = TaskResources.parse(task.resources).requirements(capacities)
            except ProjectError as exc:
                raise ProjectError(f"{task.key}: {exc}") from exc
        return requests

    def submit(self, fn: Any, *args: Any, key: str, resources: dict[str, float]) -> Any:
        """Submit an ordinary Dask task with a unique key and forwarded output."""
        from lightcone.engine.compute.output import call

        return self.client.submit(
            call, fn, self.output.topic, key, *args,
            key=f"lc-{self.invocation}-{key}", pure=False, resources=resources,
        )

    def completed(self, handles: list[Any]) -> Iterator[worker.TaskResult]:
        """Yield each task result as Dask completes it."""
        from distributed import as_completed

        try:
            for _, result in as_completed(  # type: ignore[no-untyped-call]
                handles, with_results=True, loop=self.client.loop
            ):
                if not self.output.wait(result.key):
                    result = replace(
                        result,
                        notes=(*result.notes,
                               "remote output forwarding did not finish before its deadline"),
                    )
                yield result
        except ProjectError:
            raise
        except Exception as exc:
            from lightcone.engine.compute import UNSTOPPED

            raise ProjectError(f"cluster execution failed: {exc}. {UNSTOPPED}") from exc


@contextmanager
def cluster_for_run(cluster_id: str) -> Iterator[Scheduler]:
    """Borrow the explicit allocation without creating any compute.

    Args:
        cluster_id: The selected cluster name or immutable allocation ID.

    Yields:
        A scheduler whose connection is detached when the invocation ends.
    """
    from lightcone.engine import compute
    from lightcone.engine.compute.output import forwarding

    with compute.connect(cluster_id) as client:
        invocation = uuid4().hex
        with forwarding(client, stdout="stderr") as output:
            yield _Dask(client, invocation, output, client.scheduler_info()["workers"])


def _fetch_inputs(root: Path, graph: Graph, report: MaterializeReport) -> None:
    """Bring declared inputs' bytes into this clone before workers hash or execute.

    lc fetches rather than telling anyone to — the storage invariant —
    and only here: ``--check`` and ``status`` are read-only verbs that
    must not start network transfers. Unconditional rather than probed:
    ``git annex get`` on content already present is a fast no-op, and one
    batch invocation beats a detection walk.

    A failed fetch is a *warning*, never a refusal: independent tasks
    still run, and the task whose input is genuinely unreachable reports
    its own failure with the real error — per-task reporting is most of
    what owning the loop buys.
    """
    declared = sorted(
        {
            path
            for task in graph.tasks.values()
            for entry, path in task.inputs.items()
            if entry not in task.produced_by
        }
    )
    if not declared:
        return
    got = project._run(["git", "annex", "get", "--", *declared], cwd=root)
    if got.returncode != 0:
        report.warnings.append(
            "some declared inputs could not be fetched into this clone — tasks "
            f"needing them will report it:\n{got.stderr.strip()}"
        )


# =============================================================================
# The publication view
# =============================================================================


def _converge_crate(root: Path, report: MaterializeReport, full: Graph, dsid: str) -> None:
    """Bring ``ro-crate-metadata.json`` in line with the repository.

    A derived artifact, converged the way ``uv.lock`` is: rendered from
    repository state, compared, and committed only on a difference — so
    an idempotent re-run commits nothing and nobody has to remember a
    verb. Maintenance is derived from publication intent: a declared
    ``[project].license`` turns it on, the same shape as
    ``[tool.lightcone.image]`` deriving containerized mode. Runs after
    the consume loop, driver-side, on the full graph rather than the
    run's targets — the crate describes the project, not one invocation.
    (The rerun entry point does not come through here: it is one task's
    executor, so the crate lags until the next materialize.)

    Contained on purpose: by the time this runs every output is already
    committed, so a failure here becomes a warning and the report still
    reaches the user — the crate is the publication view, not the run.
    An interrupt between the write and its commit restores the file, so
    the tree ends as clean as the loop left it; and the save runs even
    when the text already matches, healing a previously interrupted
    converge whose write survived uncommitted.

    Args:
        root: The project root.
        report: Collects the one maintenance line and any warnings.
        full: The whole-project graph — never a run's narrowed one.
        dsid: The dataset's UUID, already read by the caller.
    """
    # Deferred so `lc status` and `--check` never import rocrate — the
    # crate is the one materialize-only dependency on their shared path.
    from lightcone.engine import crate

    spdx = project.license_of(root)
    path = root / project.CRATE_FILENAME
    if not spdx:
        report.warnings.append(
            f"pyproject.toml no longer declares [project].license, so "
            f"{project.CRATE_FILENAME} is no longer maintained"
            if path.exists()
            else "no [project].license in pyproject.toml, so no RO-Crate "
            "publication view is maintained — declare one to enable it"
        )
        return
    # A set difference rather than a walk driven by the spec: what is on
    # disk is exactly what the *previous* spec planned, so the edit that
    # orphans a record is the same edit that drops it from the graph.
    # Enumerated from git, which answers for the tree as committed.
    expected = {place.record for task in full.tasks.values() for place in task.places}
    for record in sorted(set(dataset.tracked_records(root)) - expected):
        report.warnings.append(
            f"{record} is the record of a result the analysis no longer plans, "
            "so that result is not in the publication view"
        )
    try:
        document = crate.render(
            root,
            full,
            license=spdx,
            dsid=dsid,
            remote=dataset.head(root)[1],
            writer=functools.partial(dataset.last_writer, root),
            keys=dataset.annex_keys(root),
        )
        if not (path.is_file() and path.read_text() == document):
            path.write_text(document)
        try:
            dataset.save(root, [path], "Update the RO-Crate publication view")
        except BaseException:
            # An interrupt or a git failure between the write and its
            # commit must not leave a file lc wrote for the next run's
            # dirty refusal to blame on the user.
            dataset.restore(root, [path])
            raise
    except Exception as e:
        report.warnings.append(f"the RO-Crate publication view could not be updated: {e}")


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
            f"python -m lightcone.engine.worker {shlex.quote(task.key)}"
        ),
        "dsid": dsid,
        "exit": 0,
        "inputs": sorted(set(task.inputs.values())),
        "outputs": _files(task),
        "pwd": ".",
    }
    if runtime.mode == "containerized":
        info["extra_inputs"] = [runtime.archive]
    body = json.dumps(info, indent=1, sort_keys=True, ensure_ascii=False)
    return (
        f"{datalad_run_subject(task.key)}\n\n"
        "=== Do not change lines below ===\n"
        f"{body}\n"
        "^^^ Do not change lines above ^^^"
    )


def datalad_run_subject(key: Key) -> str:
    """The subject line of *key*'s ``[DATALAD RUNCMD]`` commit — datalad's
    format, which its ``rerun`` matches with a regex, not a spelling of
    ours to adjust.

    One function, shared with the foreign-write check: an output is
    cleanly written iff the commit that last touched its files
    carries exactly this subject, so the composer and the comparator must
    not be two strings that can drift apart.
    """
    return f"[DATALAD RUNCMD] {key}"


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


def _graph(
    root: Path, targets: Sequence[str], report: MaterializeReport
) -> tuple[Graph, str, Graph]:
    """The tasks a run covers, and the environment they will be compared to.

    The lock scan runs here, once, for both modes: what it refuses is a
    dependency whose bytes the lock does not pin, which makes every hash
    below it a claim nobody can check. The unnarrowed graph is returned
    beside the narrowed one, because the crate describes the whole
    project and must not pay a second spec validation to see it.
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
    if scan.machine_config:
        report.warnings.append(
            "machine-level uv configuration steers install settings underneath "
            "the project's own, and env_version cannot see it: "
            + "; ".join(scan.machine_config)
        )

    environment = identity.environment(root)
    full = plan.build(root)
    report.warnings.extend(full.notes)
    graph = full.closure(full.resolve(list(targets))) if targets else full
    return graph, environment, full


def _owned(task: Task) -> list[str]:
    """Every path one result owns, as git pathspecs.

    A glob for the payload rather than its literal path: it covers
    whatever format the *previous* run wrote, so a re-declared
    serialization stages the old file's deletion instead of leaving it
    behind untracked.

    Never the parent directory: siblings share it, and under Dask they are
    in flight — ``git clean`` on it would delete a running task's bytes.
    """
    owned = []
    for place in task.places:
        owned += [f":(glob){posixpath.dirname(place.bytes)}/{place.name}.*", place.record]
    return owned


def _dirty(root: Path, changes: Sequence[tuple[str, str]]) -> str:
    """The refusal, split by what the right remedy actually is.

    Two path classes, because they call for opposite actions: work the
    researcher owns has to be committed, and anything under ``results/``
    or ``.astra/`` is lc's to write, so a change there is wreckage to
    discard rather than a contribution to keep.
    """
    written = ("results/", ".astra/")
    theirs = [c for c in changes if not c[1].startswith(written)]
    ours = [c for c in changes if c[1].startswith(written)]

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
        where = " ".join(sorted({path.partition("/")[0] + "/" for _, path in ours}))
        lines += [
            "",
            "  if a cluster run was interrupted, stop its allocation first:",
            "      lc compute down <cluster-id>",
            "  confirm its recipes have stopped, then discard these (lc writes them):",
            f"      git restore --staged --worktree {where} && git clean -fd {where}",
            *(f"      {code.strip() or '??'} {path}" for code, path in ours),
        ]
    return "\n".join(lines)
