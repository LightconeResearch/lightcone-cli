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

import json
import re
import shlex
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from lightcone.engine import assets, container, dataset, identity, plan, project, worker
from lightcone.engine.execution_resources import TaskResources, worker_capacities
from lightcone.engine.plan import Graph, Key, Task
from lightcone.engine.project import ProjectError

if TYPE_CHECKING:
    from astra.api import ProvenanceRecord

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
    #: — task → the context, naming the commit. Left alone;
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
    graph, environment = _graph(root, targets, report)
    for found in _classify(root, graph, environment, refresh=refresh):
        verdict = found.verdict
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
    #: What the result is. Where something upstream may be remade, what
    #: it is in itself: only the task, once that has been made, can say
    #: more.
    verdict: assets.Verdict
    #: The result's record, where it has one of its own.
    record: ProvenanceRecord | None
    #: The commit that last wrote the result's files, if not its own run.
    foreign: dataset.LastWrite | None
    #: A task upstream of this one that may run, or empty.
    upstream: Key

    def may_run(self, *, refresh: bool) -> bool:
        """Whether a run has to hand this task to a worker."""
        return bool(self.upstream) or self.verdict.calls_for_a_remake(refresh=refresh)


def _classify(
    root: Path, graph: Graph, environment: str, *, refresh: bool
) -> list[_Classified]:
    """Classify every task of *graph*, upstream first, reading nothing but
    the project.

    The walk every mode shares, so there is one answer to "what is this
    result" and not one per verb: what ASTRA's checker found of each task
    goes through the one classification rule, with the history only the
    driver can read.

    Args:
        root: The project root.
        graph: The tasks to classify, each after the tasks it reads.
        environment: The IRI of the project's environment.
        refresh: Whether behind results count as running, which is what
            decides whether their dependents may run too.

    Returns:
        One entry per task, in the graph's order.
    """

    def recorded(key: Key) -> assets.Digest:
        record = graph.found[key].record
        assert record is not None  # a result left alone has a record of its own
        return record.content_hash, record.size

    may_run: set[Key] = set()
    classified = []
    for key, task in graph.tasks.items():
        found = graph.found[key]
        record = found.record
        # History is git's to answer, so it enters the one classification
        # rule as a value — computed here, where git lives.
        foreign = None if record is None else _foreign_write(root, task)
        upstream = next((dep for dep in task.depends_on if dep in may_run), "")
        entry = _Classified(
            task,
            assets.classify(
                found=found,
                environment=environment,
                # A result left alone gives the bytes its record does. One
                # that may be remade gives bytes nobody knows yet.
                read=(
                    None
                    if upstream
                    else {entry: recorded(made) for entry, made in task.produced_by.items()}
                ),
                foreign=foreign,
            ),
            record,
            foreign,
            upstream,
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
    """One result: what it is now, and the commit it came from."""

    #: The result's task, as lc names it (``fit#baseline``).
    output: str
    status: assets.Status
    #: Why, for ``stale`` and ``behind``. Empty for ``current``.
    why: str
    #: The commit its record names, or empty if it has no record. This is
    #: the whole point of the verb: an artifact that is behind is not
    #: wrong, and this is where the code and environment that produced
    #: it can be read back.
    git_sha: str
    #: The hash of its bytes, as its record gives it, or empty.
    content_hash: str
    #: The environment its recipe ran in, as its record names it, or
    #: empty. lc minted the name, so lc is who says what it holds: the
    #: lock, the interpreter pin, uv's install settings and the image
    #: declaration of the commit in ``git_sha``.
    environment: str
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
    #: The lock scan's prose, and what ASTRA notes for the author.
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
    #: The project's environment as a record made now would name it. A
    #: result whose own ``environment`` differs is the one that is behind.
    environment: str = ""

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
            "environment": self.environment,
            "counts": self.counts,
            "outputs": [output.as_dict() for output in self.outputs],
            "warnings": self.warnings,
        }


def status(root: Path) -> StatusReport:
    """Report what state every planned result is in.

    Reads records through ASTRA's checker; runs nothing, commits nothing,
    and does not care whether the tree is clean. Classified with
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
    graph, environment = _graph(root, [], report)
    result.environment = environment
    for found in _classify(root, graph, environment, refresh=False):
        verdict = found.verdict
        if found.upstream and verdict.status != "stale":
            # A result made from a stale one is stale until that one is
            # made again, whatever it is in itself.
            verdict = assets.Verdict("stale", f"`{found.upstream}`, which it reads, is stale")
        run = found.record.generated_by if found.record else None
        result.outputs.append(
            OutputStatus(
                output=found.task.key,
                status=verdict.status,
                why=verdict.why,
                git_sha=run.commit if run else "",
                content_hash=found.record.content_hash if found.record else "",
                environment=run.environment.iri if run else "",
                foreign_write=found.foreign.sha if found.foreign else "",
            )
        )
    result.warnings = report.warnings
    return result


def _foreign_write(root: Path, task: Task) -> dataset.LastWrite | None:
    """Find the commit that last touched *task*'s files, unless it is the
    result's own run — then ``None``, the clean answer. The fact only:
    the verdict's prose is `classify`'s, like every other why.

    Its bytes and its record: a forged *result* is the case the check
    exists for, and a doctored *record* is the other half — while the
    record is also what carries the history across a re-declared format,
    where the payload's own path is new and would read as clean.
    """
    write = dataset.last_writer(root, task.output_path, *task.records)
    if not write or write.subject == datalad_run_subject(task.key):
        return None
    return write


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
        return f"none{detail} (fs: open, network: allowed)"
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
    dataset.require_free_index(root)
    # The dirty check comes before anything that writes: the image
    # converge below *commits*, and `dataset.save` stages scoped but
    # commits the whole index — on a dirty tree the user's staged edits
    # would be swept into the image commit.
    if changes := dataset.status(root):
        raise ProjectError(_dirty(root, changes))
    worker.require_recreatable(root)
    # The graph — and with it the spec validation and the lock scan —
    # before the image: a refusal here must not cost a minutes-long
    # build, and must not leave an archive commit behind a run that
    # "failed" on a typo in the spec.
    graph, environment = _graph(root, targets, report)
    classified = _classify(root, graph, environment, refresh=refresh)
    running = [found.task for found in classified if found.may_run(refresh=refresh)]
    if not running:
        # Nothing is submitted, so no allocation is needed.
        for found in classified:
            _left_alone(found, report)
        return report
    _require_storable(root, graph)
    dsid = dataset.dataset_id(root)
    with cluster_for_run(cluster_id) as scheduler:
        requirements = scheduler.validate(running)
        # A result certain to be remade is the one thing a run need not
        # fetch: one that may run only because something it reads may is
        # left alone when that comes out as recorded, and its readers then
        # read its bytes.
        _fetch_inputs(
            root,
            running,
            {
                found.task.key
                for found in classified
                if found.verdict.calls_for_a_remake(refresh=refresh)
            },
            report,
        )
        # Materialize is one of the two verbs allowed to build the image (the
        # other is `lc build`); the probe and the rerun entry point only find
        # one. Resolved once, then handed to every task.
        runtime = container.runtime_for_run(
            root, build=True, use_gpus=any(r.get("GPU", 0) for r in requirements.values()),
        )
        # Converge the environment: workers pass `--no-sync`, so this is the
        # only place on a run's path where it is made to match the lock. (A
        # rerun does not come through here; its entry point converges too.)
        report.warnings.extend(f"uv: {w}" for w in container.converge(runtime))
        # A declared input more than one task reads is hashed here, once:
        # each task would otherwise read all of it to name it in its
        # record, and a multiverse reads one catalog in every universe.
        # One that cannot be read is left for the task that reads it to
        # report.
        readers = Counter(
            path
            for task in running
            for path in {
                path for entry, path in task.inputs.items() if entry not in task.produced_by
            }
        )
        hashed: dict[Path, tuple[tuple[int, int, int], assets.Digest]] = {}
        for path in sorted(path for path, count in readers.items() if count > 1):
            with suppress(OSError, ProjectError):
                assets.require_fetched(path)
                hashed[path] = assets.stamp(path), assets.digest_file(path)
        # HEAD is read once, after the image converge that may have
        # committed an archive: the driver commits as results land, and a
        # per-task read would name a commit this run created.
        context = worker.RunContext(
            environment=environment,
            commit=dataset.head(root),
            runtime=runtime,
            hashed=hashed,
        )
        pending: dict[Key, Any] = {}
        handles = []
        # Results left alone are values, not Dask tasks: they need no
        # allocation resources. Futures for everything that may run retain
        # dependency order.
        for found in classified:
            task = found.task
            if not found.may_run(refresh=refresh):
                pending[task.key] = _left_alone(found, report)
                continue
            # What the checker found goes with the task: downstream of a
            # remake, whether the result still stands is known only once
            # its upstreams are made, and the task answers that from these
            # findings and the bytes they gave, without git.
            pending[task.key] = scheduler.submit(
                worker.materialize,
                root,
                task,
                graph.found[task.key],
                context,
                refresh,
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
        # keeps `results/` clean, so anything dirty *now* was edited while
        # the graph ran — and every record names the starting commit,
        # which no longer describes that code. A warning: the driver does
        # not rewrite records the worker wrote.
        if edited := dataset.status(root):
            names = ", ".join(sorted(path for _, path in edited))
            report.warnings.append(
                f"edited while the run was in flight: {names} — the records name "
                "the starting commit, which no longer describes this code"
            )
        return report


def _left_alone(found: _Classified, report: MaterializeReport) -> worker.TaskResult:
    """Report a result no run has to make, as the value its dependents read."""
    assert found.record is not None and found.verdict.status != "stale"
    if found.verdict.status == "behind":
        report.behind[found.task.key] = found.verdict.why
    else:
        report.current.append(found.task.key)
    return worker.TaskResult(
        found.task.key,
        found.verdict.status,
        found.record.content_hash,
        found.record.size,
        reason=found.verdict.why,
    )


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
        dataset.save(root, _owned(root, task), run_record(root, task, dsid, runtime))
        report.made.append(name)
        return

    if result.status == "current":
        report.current.append(name)
        return

    if result.status == "behind":
        report.behind[name] = result.reason
        return

    dataset.restore(root, _owned(root, task))
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


def _fetch_inputs(
    root: Path, running: Sequence[Task], certain: set[Key], report: MaterializeReport
) -> None:
    """Bring what recipes read into this clone before workers hash or execute.

    Every file a task that may run reads, bar a result certain to be
    remade: its declared inputs, and each result it reads that may be
    left alone, whose bytes are then what the recipe reads. A clone that
    has fetched no content holds only a stand-in for either. A run that
    makes nothing fetches nothing.

    lc fetches rather than telling anyone to — the storage invariant —
    and only here: ``--check`` and ``status`` are read-only verbs that
    must not start network transfers, and ASTRA's checker reads a
    stand-in for the content it stands for. Unconditional rather than
    probed: ``git annex get`` on content already present is a fast
    no-op, and one batch invocation beats a detection walk.

    A failed fetch is a *warning*, never a refusal: independent tasks
    still run, and the task whose input is genuinely unreachable reports
    its own failure with the real error — per-task reporting is most of
    what owning the loop buys.

    Args:
        root: The project root.
        running: The tasks that may run.
        certain: The keys of those certain to be remade.
        report: Collects the warning a failed fetch gives.
    """
    wanted = sorted(
        {
            plan.declared_path(root, path)
            for task in running
            for entry, path in task.inputs.items()
            if task.produced_by.get(entry) not in certain
        }
    )
    if not wanted:
        return
    got = project._run(["git", "annex", "get", "--", *wanted], cwd=root)
    if got.returncode != 0:
        report.warnings.append(
            "some of what this run reads could not be fetched into this clone — "
            f"tasks needing it will report it:\n{got.stderr.strip()}"
        )


def _require_storable(root: Path, graph: Graph) -> None:
    """Refuse a project whose results git would not store as lc means them.

    Asked of git for the very paths a run would commit, because the
    rules are files the researcher owns and convergence only appends to:
    an ignored result is committed by nobody and reported made; one that
    no line sends to the annex lands in git itself, in every clone
    forever; one another filter takes (Git LFS) is stored where lc reads
    and fetches nothing; and a record that leaves git cannot be read on a
    clone that holds no content.

    Raises:
        ProjectError: Naming each such path.
    """
    files = [plan.declared_path(root, task.output_path) for task in graph.tasks.values()]
    records = [
        plan.declared_path(root, record) for task in graph.tasks.values() for record in task.records
    ]
    # NUL-separated both ways, as `check-attr` is below: git otherwise
    # quotes a path holding an accent, and a space would end one. For
    # `check-ignore` that means paths on stdin, the only way it takes `-z`.
    asked = project._run(
        ["git", "check-ignore", "-z", "--no-index", "--stdin"],
        cwd=root,
        stdin="".join(f"{path}\0" for path in (*files, *records)),
    )
    ignored = [path for path in asked.stdout.split("\0") if path]
    listed = dataset._git(
        ["check-attr", "-z", "annex.largefiles", "filter", "--", *files, *records], cwd=root
    ).split("\0")
    attributes = {
        (path, name): value
        for path, name, value in zip(listed[::3], listed[1::3], listed[2::3], strict=False)
    }

    def stored(path: str) -> str:
        """Where a `git add` of *path* puts its bytes."""
        taken_by = attributes.get((path, "filter"), "unspecified")
        if taken_by not in ("annex", "unspecified", "unset"):
            return taken_by
        if taken_by == "annex" and attributes.get((path, "annex.largefiles")) == "anything":
            return "annex"
        return "git"

    refusals = [
        f"{path} is git-ignored, so it would never be committed — remove the "
        ".gitignore rule that covers it"
        for path in ignored
    ]
    for path in files:
        if (where := stored(path)) == "git":
            refusals.append(
                f"{path} would be committed to git itself — the last .gitattributes line "
                "that matches it does not send it to the annex"
            )
        elif where != "annex":
            refusals.append(
                f"{path} would be stored by `filter={where}`, not git-annex — a "
                ".gitattributes line below lightcone's sets its filter"
            )
    for path in records:
        if (where := stored(path)) != "git":
            how = "annexed" if where == "annex" else f"stored by `filter={where}`"
            refusals.append(
                f"{path} would be {how} — the last .gitattributes line that matches it "
                "says so, and a record has to stay in git, where a clone with no content "
                "can read it"
            )
    if refusals:
        raise ProjectError(
            "this project's results would not be stored as lc stores them:\n  "
            + "\n  ".join(refusals)
            + "\n`lc init` adds any of lightcone's .gitattributes lines that is missing. "
            "Where it adds none, a later line overrides them: remove it, or move "
            "lightcone's lines below it."
        )


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
        "inputs": sorted(plan.declared_path(root, path) for path in task.inputs.values()),
        "outputs": [
            plan.declared_path(root, path) for path in (task.output_path, *task.records)
        ],
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

    One function, shared with the foreign-write check: a result is
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
) -> tuple[Graph, str]:
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
    if scan.machine_config:
        report.warnings.append(
            "machine-level uv configuration steers install settings underneath "
            "the project's own, and env_version cannot see it: "
            + "; ".join(scan.machine_config)
        )

    environment = identity.environment(root)
    graph = plan.build(root)
    report.warnings.extend(graph.notes)
    if targets:
        graph = graph.closure(graph.resolve(list(targets)))
    return graph, environment


def _owned(root: Path, task: Task) -> list[str]:
    """Every path one result owns, as git pathspecs.

    A glob for the bytes, so it covers whatever format the *previous*
    run wrote and a re-declared serialization stages the old file's
    deletion instead of leaving it behind untracked. A record's path is
    taken to the letter.

    Never the parent directory: siblings share it, and under Dask they are
    in flight — ``git clean`` on it would delete a running task's bytes.
    """
    parent = plan.declared_path(root, task.output_path.parent)
    return [
        f":(glob){parent}/{task.name}.*",
        *(f":(literal){plan.declared_path(root, record)}" for record in task.records),
    ]


#: The folders a run writes in: results' bytes, and their records. A
#: change there is what a run left, not the researcher's work.
_WRITTEN = ("results/", ".astra/")


def _dirty(root: Path, changes: Sequence[tuple[str, str]]) -> str:
    """The refusal, split by what the right remedy actually is.

    Two path classes, because they call for opposite actions: work the
    researcher owns has to be committed, and anything under ``results/``
    or ``.astra/`` is lc's to write, so a change there is wreckage to
    discard rather than a contribution to keep — an interrupted run
    leaves part of a result, and the record it removed before its recipe
    ran.

    The discard command names a folder to ``git restore`` only where git
    knows a file of it, which refuses one it knows none of.
    """
    theirs = [c for c in changes if not c[1].startswith(_WRITTEN)]
    ours = [c for c in changes if c[1].startswith(_WRITTEN)]

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
        known = sorted({f"{path.split('/')[0]}/" for code, path in ours if code != "??"})
        new = sorted({f"{path.split('/')[0]}/" for code, path in ours if code == "??"})
        discard = [
            *([f"git restore --staged --worktree {' '.join(known)}"] if known else []),
            *([f"git clean -fd {' '.join(new)}"] if new else []),
        ]
        lines += [
            "",
            "  if a cluster run was interrupted, stop its allocation first:",
            "      lc compute down <cluster-id>",
            "  confirm its recipes have stopped, then discard these "
            "(lc writes results/ and .astra/):",
            f"      {' && '.join(discard)}",
            *(f"      {code.strip() or '??'} {path}" for code, path in ours),
        ]
    return "\n".join(lines)
