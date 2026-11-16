"""Making a whole analysis: what runs, in what order, and what gets committed.

The driver's jobs, and the order matters.

**It builds a commit, in a checkout of its own.** A run starts from a
tree with no uncommitted change, so that what the researcher sees is
what runs; then every recipe runs in the run's own checkout of that
commit (:mod:`~lightcone.engine.checkout`), and the project's tree is
the researcher's again. Editing it, staging in it or committing to it
while the run is in flight changes nothing a recipe sees and nothing a
record says.

**It hands the work to Dask and gets out of the way.** Tasks that may run
receive upstream futures or already-current results as arguments, so the
ordering, parallelism, and scheduling are Dask's — there is no ready-set
loop here to get wrong.

**It owns git, alone.** Workers execute and return; the driver lands each
result in the project and commits it there, in one thread, as results
arrive. That is not a preference: concurrent git operations on one
repository race on the index lock. A result that cannot be landed yet —
the project is mid-merge, or on another branch for a moment — waits, and
is tried again as each later one arrives; one still waiting when the
last has arrived is reported failed.

A dependent starts as soon as its upstream's *worker* returns, and reads
the upstream's file in the checkout, where the recipe left it: landing a
result gives its bytes a second name in the project and takes nothing
away from the checkout.
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

from lightcone.engine import (
    assets,
    checkout,
    container,
    dataset,
    identity,
    plan,
    project,
    worker,
)
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
    #: it is in itself: only the task, once that has landed, can say more.
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
                    else {
                        source.entry: recorded(source.result)
                        for source in task.result.inputs
                        if source.result is not None
                    }
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
    #: The environment its recipe ran in, as its record names it, or empty
    #: if it was never materialized. lc minted the name, so lc is who says
    #: what it holds: the lock, the interpreter pin, uv's install settings
    #: and the image definition of the commit in ``git_sha``.
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
    #: The project's environment as a record made now would name it. An
    #: output whose own ``environment`` differs is the one that is behind.
    environment: str = ""
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
            "environment": self.environment,
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
    result.environment = environment
    for found in _classify(root, graph, environment, refresh=False):
        run = found.record.generated_by if found.record else None
        if run and run.finished:
            stamps.append(run.finished)
        result.outputs.append(
            OutputStatus(
                output=found.task.key,
                status=found.verdict.status,
                why=found.verdict.why,
                git_sha=run.commit if run else "",
                data_version=found.record.content_hash if found.record else "",
                environment=run.environment.iri if run else "",
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
    write = dataset.last_writer(root, *task.files)
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
    if held := dataset.index_held(root):
        raise ProjectError(
            f"another git command holds the project's index ({held}), so no result "
            "could be committed. Wait for it to end; if none is running, a git that "
            "was killed left that file, and removing it is the remedy."
        )
    # A run builds a commit, and an uncommitted change would be no part
    # of it. Refused rather than left out: what the researcher sees in
    # the tree when a run starts is what the run builds. Read while no
    # other run is landing a result, which would look like an edit.
    with checkout.landing(root):
        changes = dataset.status(root)
    if changes:
        raise ProjectError(_dirty(root, changes))
    # A clean tree does not say the environment is in the commit: an
    # ignored file is clean. A record names its environment by the files
    # of its commit, so each has to be there for lc to recreate it.
    if missing := dataset.untracked(root, identity.files(root)):
        raise ProjectError(
            f"{', '.join(missing)} — not tracked by git, so the commit a record names "
            "could not recreate the environment its recipe ran in "
            "(UNRECREATABLE_ENVIRONMENT). Commit the environment's files, and remove "
            "whatever .gitignore rule keeps them out."
        )
    # The graph — and with it the spec validation and the lock scan —
    # before the image: a refusal here must not cost a minutes-long
    # build, and must not leave an archive commit behind a run that
    # "failed" on a typo in the spec.
    graph, environment, full = _graph(root, targets, report)
    # ASTRA read every analysis of the stack in the working tree, and one
    # the project declares beside itself is outside the folder asked
    # about above.
    if graph.beside:
        with checkout.landing(root):
            changes = dataset.status(root, also=graph.beside)
        if changes:
            raise ProjectError(_dirty(root, changes))
    dsid = dataset.dataset_id(root)
    classified = _classify(root, graph, environment, refresh=refresh)
    if not any(found.may_run(refresh=refresh) for found in classified):
        # Nothing to make is not nothing to do: the crate describes the
        # project, and this is its only maintainer. Nothing is submitted,
        # so no allocation and no checkout are needed.
        for found in classified:
            _left_alone(found, report)
        _converge_crate(root, report, full, dsid)
        return report
    _require_storable(root, graph)
    with cluster_for_run(cluster_id) as scheduler:
        requirements = scheduler.validate(
            found.task for found in classified if found.may_run(refresh=refresh)
        )
        # Materialize is one of the two verbs allowed to build the image (the
        # other is `lc build`); the probe and the rerun entry point only find
        # one. Resolved once, then handed to every task.
        runtime = container.runtime_for_run(
            root, build=True, use_gpus=any(r.get("GPU", 0) for r in requirements.values()),
        )
        if dataset.head(root) != graph.head:
            # The image was built and committed. That commit is the one
            # the run builds, so its plan is the one to follow.
            graph, environment, full = _graph(root, targets, MaterializeReport())
            classified = _classify(root, graph, environment, refresh=refresh)
            requirements = scheduler.validate(
                found.task for found in classified if found.may_run(refresh=refresh)
            )
        # ASTRA read the working tree and named the commit it was on.
        # The two are the same thing only while nothing is uncommitted,
        # and from here on it is the commit that is built.
        with checkout.landing(root):
            moved = (
                dataset.status(root, also=graph.beside)
                or dataset.head(root) != graph.head
            )
        if moved:
            raise ProjectError(
                "the project changed while lc was preparing the run — a file was "
                "edited, or a commit made. Run again."
            )
        tree = checkout.take(root, graph.head, scheduler.allocation)
        handles: list[Any] = []
        reported = 0
        try:
            running = [found.task for found in classified if found.may_run(refresh=refresh)]
            # Every file a task that may run reads, bar a result certain
            # to be remade. One that may run only because something it
            # reads may is left alone when that comes out as recorded,
            # and its bytes are then what its own readers read.
            certain = {
                found.task.key
                for found in classified
                if found.verdict.calls_for_a_remake(refresh=refresh)
            }
            unfetched = checkout.fill(
                tree,
                sorted(
                    {
                        task.reads[source.entry]
                        for task in running
                        for source in task.result.inputs
                        if source.result not in certain
                    }
                ),
                replaceable={place.file_path for task in running for place in task.result.places},
            )
            if unfetched:
                # A *warning*, never a refusal: independent tasks still
                # run, and the task whose input is unreachable reports
                # its own failure — per-task reporting is most of what
                # owning the loop buys.
                report.warnings.append(
                    "some of what this run reads could not be fetched into this clone — "
                    f"tasks needing it will report it:\n{unfetched}"
                )
            # The run's environment is the checkout's own, made to match
            # the commit's lock: workers pass `--no-sync`, so this is the
            # only place on a run's path where it is converged. (A rerun
            # does not come through here; its entry point converges too.)
            runtime = replace(runtime, root=tree.root, env_dir=project.env_dir(tree.root))
            report.warnings.extend(f"uv: {w}" for w in container.converge(runtime))
            # Every record of the run names the commit its checkout is at.
            context = worker.RunContext(
                environment=environment, commit=tree.commit, runtime=runtime
            )
            written = sorted(
                {posixpath.dirname(path) for task in graph.tasks.values() for path in task.files}
            )
            pending: dict[Key, Any] = {}
            # Results left alone are values, not Dask tasks: they need no allocation
            # resources. Futures for everything that may run retain dependency order.
            for found in classified:
                task = found.task
                if not found.may_run(refresh=refresh):
                    pending[task.key] = _left_alone(found, report)
                    continue
                # What the checker found goes with the task: downstream of a
                # remake, whether the result still stands is known only once
                # its upstreams have landed, and the task answers that from
                # these findings and the bytes they gave, without git.
                pending[task.key] = scheduler.submit(
                    worker.materialize,
                    tree.root,
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

            # Made, and not yet committed in the project: task → why not.
            waiting: dict[Key, str] = {}
            for result in scheduler.completed(handles):
                reported += 1
                try:
                    _consume(tree, written, graph, result, dsid, runtime, report, waiting)
                except Exception as exc:
                    if reported == len(handles):
                        raise
                    raise ProjectError(f"{exc}. {UNSTOPPED}") from exc
            for key, why in waiting.items():
                report.failed.append(key)
                report.warnings.append(f"{key}: made, but not recorded: {why}")
        finally:
            # An unreported task can still have a running recipe, writing
            # in the checkout: then the slot is kept from the next run.
            if reported == len(handles):
                checkout.give_back(tree)
        _converge_crate(root, report, full, dsid)
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
    tree: checkout.Checkout,
    written: Sequence[str],
    graph: Graph,
    result: worker.TaskResult,
    dsid: str,
    runtime: container.Runtime,
    report: MaterializeReport,
    waiting: dict[Key, str],
) -> None:
    """Record one finished task, and land in the project what can be landed."""
    name = result.key
    if result.notes:
        # Named on a line of their own rather than prefixed onto each: a
        # prefix would land in the middle of a multi-line remedy and make
        # it uncopyable, which is the whole reason these travel separately.
        report.notes.extend([f"{name}:", *result.notes])

    if result.status == "ok":
        # Asked as each made result arrives, of the checkout its recipe
        # ran in: a record written there names a commit, and that is only
        # true of a checkout nothing but the run's own results changed.
        if edited := checkout.changed(tree, written):
            result = replace(
                result,
                status="failed",
                reason=(
                    "made, but not recorded: the run's checkout no longer holds the "
                    f"commit's files alone — {', '.join(edited)} (UNCOMMITTED_CHANGES)"
                ),
            )
        else:
            waiting[name] = ""
    elif result.status == "current":
        report.current.append(name)
    elif result.status == "behind":
        report.behind[name] = result.reason
    if result.status in ("failed", "blocked"):
        getattr(report, result.status).append(name)
        report.warnings.append(f"{name}: {result.reason}")

    # Each made result that is not in the project yet, this one included:
    # one that could not land a moment ago may be able to now.
    for key in list(waiting):
        task = graph.tasks[key]
        waiting[key] = checkout.land(tree, task, run_record(task, dsid, runtime))
        if not waiting[key]:
            del waiting[key]
            report.made.append(key)


class Scheduler(Protocol):
    """How the driver talks to whatever is running the graph.

    Hand over tasks with their upstream handles and iterate results as
    they land, keeping the commit logic independent of the provider.
    """

    @property
    def allocation(self) -> str:
        """The full ID of the allocation the tasks execute on. A run's
        checkout is held in its name: while the allocation lives, a
        recipe of the run may too."""
        ...

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
    allocation: str
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

    # Resolved once: a name costs a native discovery, an ID none.
    allocation = compute.identity(cluster_id)
    with compute.connect(allocation) as client:
        invocation = uuid4().hex
        with forwarding(client, stdout="stderr") as output:
            yield _Dask(
                client, allocation, invocation, output, client.scheduler_info()["workers"]
            )


def _require_storable(root: Path, graph: Graph) -> None:
    """Refuse a project whose results git would not store as lc means them.

    Asked of git for the very paths a run would commit, because the
    rules are files the researcher owns and convergence only appends to:
    an ignored result is committed by nobody and reported made; one that
    no line sends to the annex lands in git itself, in every clone
    forever; and a record a line does send there cannot be read on a
    clone that holds no content.

    Raises:
        ProjectError: Naming each such path.
    """
    places = [place for task in graph.tasks.values() for place in task.result.places]
    files = [place.file_path for place in places]
    records = [place.record_path for place in places]
    ignored = project._run(
        ["git", "check-ignore", "--no-index", "--", *files, *records], cwd=root
    ).stdout.split()
    listed = dataset._git(
        ["check-attr", "-z", "annex.largefiles", "--", *files, *records], cwd=root
    ).split("\0")
    routed = dict(zip(listed[::3], listed[2::3], strict=False))
    unrouted = [path for path in files if routed.get(path) in ("nothing", "unspecified", "unset")]
    annexed = [path for path in records if routed.get(path) == "anything"]
    refusals = [
        *(
            f"{path} is git-ignored, so it would never be committed — remove the "
            ".gitignore rule that covers it"
            for path in ignored
        ),
        *(
            f"{path} would be committed to git itself — the last .gitattributes line "
            "that matches it does not send it to the annex"
            for path in unrouted
        ),
        *(
            f"{path} would be annexed — the last .gitattributes line that matches it "
            "sends it there, and a record has to stay in git, where a clone with no "
            "content can read it"
            for path in annexed
        ),
    ]
    if refusals:
        raise ProjectError(
            "this project's results would not be stored as lc stores them:\n  "
            + "\n  ".join(refusals)
            + "\n`lc init` adds any of lightcone's .gitattributes lines that is missing. "
            "Where it adds none, a later line overrides them: remove it, or move "
            "lightcone's lines below it."
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
        full: The whole-project graph, as read before the run — never a
            run's narrowed one.
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
    if dataset.status(root):
        # The crate is a function of what is committed, and it is read
        # off the working tree: one being edited would publish the edit.
        report.warnings.append(
            "the project has uncommitted changes, so the RO-Crate publication view "
            "was not brought up to date — the next `lc materialize` does it"
        )
        return
    # A set difference rather than a walk driven by the spec: what is on
    # disk is exactly what the *previous* spec planned, so the edit that
    # orphans a record is the same edit that drops it from the graph.
    # Enumerated from git, which answers for the tree as committed.
    expected = {
        place.record_path for task in full.tasks.values() for place in task.result.places
    }
    for record in sorted(set(dataset.tracked_records(root)) - expected):
        report.warnings.append(
            f"{record} is the record of a result the analysis no longer plans, "
            "so that result is not in the publication view"
        )
    try:
        # The graph holds the records as they were when the run started;
        # what the run made has new ones, so they are read again.
        if report.made:
            full = plan.build(root)
        document = crate.render(
            root,
            full,
            license=spdx,
            dsid=dsid,
            remote=dataset.remote(root),
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


def run_record(task: Task, dsid: str, runtime: container.Runtime) -> str:
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
        "inputs": sorted(set(task.reads.values())),
        "outputs": task.files,
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


def _dirty(root: Path, changes: Sequence[tuple[str, str]]) -> str:
    """The refusal, split by what the right remedy actually is.

    Two path classes, because they call for opposite actions: work the
    researcher owns has to be committed, and what is in a ``results/`` or
    ``.astra/`` folder is lc's to write, so a change there is wreckage to
    discard rather than a contribution to keep. The commands name the
    very paths, so each does what it says on the tree as it is.
    """
    ours = [c for c in changes if re.search(r"(^|/)(results|\.astra)/", c[1])]
    theirs = [c for c in changes if c not in ours]

    lines = [
        f"uncommitted changes in {root} — a run builds the commit, in a checkout "
        "of its own, so a change that is not committed would be no part of it.",
    ]
    if theirs:
        lines += [
            "",
            '  commit these:   git add -A . && git commit -m "…"',
            *(f"      {code.strip() or '??'} {path}" for code, path in theirs),
        ]
    if ours:
        untracked = [path for code, path in ours if code == "??"]
        tracked = [path for code, path in ours if code != "??"]
        lines += ["", "  lc writes these, so discard them rather than commit them:"]
        if tracked:
            lines.append(f"      git restore --staged --worktree -- {shlex.join(tracked)}")
        if untracked:
            lines.append(f"      git clean -f -- {shlex.join(untracked)}")
    return "\n".join(lines)
