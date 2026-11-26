"""Make an analysis in a pinned checkout shared by the run's Dask tasks.

The driver refuses uncommitted inputs, prepares one tree and complete uv
environment, then submits the graph. Workers execute and record without git.
Finished payloads remain in the run tree, readable by downstream tasks.

Once every task reports, the driver collects successes and commits each
result with its record, in graph order. Failures never touch the researcher's
tree. Interruption keeps the checkout because a remote subprocess may still
be writing; collection conflicts keep it because it holds uncollected work.
"""

from __future__ import annotations

import json
import re
import shlex
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from lightcone.engine import (
    container,
    content,
    dataset,
    environments,
    identity,
    plan,
    project,
    staging,
    worker,
)
from lightcone.engine.execution_resources import TaskResources, worker_capacities
from lightcone.engine.plan import Graph, ResultId, Task
from lightcone.engine.project import ProjectError

if TYPE_CHECKING:
    from astra.api import Status

    from lightcone.engine.compute.output import Forwarder


@dataclass
class MaterializeReport:
    """What a run did, or — in check mode — what it would do."""

    #: Each result this run made and committed, by id.
    made: list[str] = field(default_factory=list)
    #: Ok: nothing they are made from changed, and the environment is the
    #: one they were made under. Listed as ``unchanged`` because the
    #: report's ``ok`` says whether the run finished.
    unchanged: list[str] = field(default_factory=list)
    #: Still what the spec asks for, but made under an earlier environment
    #: — id → why. Left alone; ``refresh`` is what remakes them.
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

        ``behind`` does not count against it. A result made under an
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
        refresh: Whether to remake the requested results regardless of state.

    Returns:
        ``planned`` naming each result that would run and why, ``behind``
        those made under an earlier environment, and ``unchanged`` the
        rest, which are ok.

    Raises:
        ProjectError: If the analysis or the lock cannot be read, or a
            target matches nothing.
    """
    report = MaterializeReport()
    graph, environment = _graph(root, targets, report)
    asked = graph.resolve(list(targets)) if targets else list(graph.tasks)
    blocked: set[ResultId] = set()
    for found in _classify(root, graph, environment, refresh=asked if refresh else ()):
        verdict = found.verdict
        # What a run leaves alone reads nothing, so nothing it reads blocks it.
        stale = {
            dep: why
            for dep in found.task.depends_on
            if dep not in graph.tasks and (why := worker.unreadable(graph.checked.statuses[dep]))
        }
        reads_blocked = bool(stale) or bool(blocked.intersection(found.task.depends_on))
        if found.may_run() and reads_blocked:
            blocked.add(found.task.id)
            report.blocked.append(found.task.id)
            report.warnings.extend(f"{dep}: {why}" for dep, why in stale.items())
        elif verdict.calls_for_a_remake(refresh=found.refresh):
            report.planned[found.task.id] = verdict.why or "requested with --refresh"
        elif found.upstream:
            # Nothing here can know whether a rebuild comes out
            # byte-identical, and assuming it will would under-report.
            report.planned[found.task.id] = (
                f"`{found.upstream}`, which it reads, would be remade"
            )
        elif verdict.state == "behind":
            report.behind[found.task.id] = verdict.why
        else:
            report.unchanged.append(found.task.id)
    return report


@dataclass(frozen=True)
class _Classified:
    """One task, and where its result stands before the run."""

    task: Task
    #: What ASTRA's checker found of the result.
    found: Status
    #: Where this clone lacks the bytes its record gives, the remotes that
    #: hold them; None where nothing was asked (`plan.classify`).
    remotes: tuple[str, ...] | None
    #: Where it stands. Where something upstream may be remade, as the
    #: checker found it: only the task, once that has been made, can say
    #: more.
    verdict: plan.Verdict
    #: A task upstream of this one that may run, or empty.
    upstream: ResultId
    #: Whether this result was explicitly requested with ``--refresh``.
    refresh: bool

    def may_run(self) -> bool:
        """Whether a run has to hand this task to a worker."""
        return bool(self.upstream) or self.verdict.calls_for_a_remake(refresh=self.refresh)

    def certain(self) -> bool:
        """Whether a run is sure to make this result again: nothing it
        makes first can leave it standing."""
        through = not self.refresh and self.verdict.through_reads and bool(self.upstream)
        return self.verdict.calls_for_a_remake(refresh=self.refresh) and not through


def _classify(
    root: Path, graph: Graph, environment: str, *, refresh: Collection[ResultId]
) -> list[_Classified]:
    """Classify every task of *graph*, upstream first.

    The walk every mode shares, so there is one answer to "what is this
    result" and not one per verb: what ASTRA's checker found of each, and
    where git-annex finds the bytes this clone lacks, through the one
    rule. A result that reads one a run may remake may run too, whatever
    it is now: whether it stands once that is made is the task's to say.

    Args:
        root: The project root.
        graph: The tasks to classify, each after the tasks it reads.
        environment: The IRI of the project's environment.
        refresh: The results explicitly requested with ``--refresh``. Their
            dependencies are remade only where stale or missing.

    Returns:
        One entry per task, in the graph's order.

    Raises:
        ProjectError: If this clone lacks a result's bytes and git-annex
            is not set up in it, to say where they are.
    """
    # Asked once, of every result whose place holds a git-annex stand-in
    # for the bytes its record gives. Not of one whose place holds other
    # bytes: a run makes that again, whoever holds the recorded ones. Not
    # in a clone git-annex was never set up in either: asking would set it
    # up, without the rest of what `lc init` converges.
    away = {
        key: plan.declared_path(root, task.path)
        for key, task in graph.tasks.items()
        if (found := graph.checked.statuses[key]).state != "missing"
        and found.bytes is not None
        and found.bytes.state == "absent"
        and found.bytes.kept_by == "git-annex"
        and not any(diagnostic.code == "ABSENT_RESULT" for diagnostic in found.why)
    }
    if away and not dataset.is_annexed(root):
        raise ProjectError(
            "git-annex is not set up in this clone, so lc cannot tell where the bytes of "
            "the results it lacks are — `lc init` sets it up."
        )
    held = dataset.holders(root, list(away.values()))
    refreshed = set(refresh)
    may_run: set[ResultId] = set()
    classified = []
    for key, task in graph.tasks.items():
        found = graph.checked.statuses[key]
        remotes = held.get(away[key]) if key in away else None
        entry = _Classified(
            task,
            found,
            remotes,
            plan.classify(found=found, environment=environment, remotes=remotes),
            next((dep for dep in task.depends_on if dep in may_run), ""),
            key in refreshed,
        )
        if entry.may_run():
            may_run.add(key)
        classified.append(entry)
    return classified


# =============================================================================
# Status — what the project holds, and where each of it came from
# =============================================================================


@dataclass(frozen=True)
class ResultStatus:
    """Where one result stands, and what ASTRA's checker found of it."""

    #: The result's id (``fit#baseline``).
    result: str
    state: plan.State
    #: Why, for every state but ``ok``.
    why: str
    #: Ok or behind, and its bytes are not in this clone: what `lc fetch`
    #: brings.
    unfetched: bool
    #: Where it is unfetched, the remotes that hold its bytes.
    remotes: list[str]
    #: ASTRA's own status of the result, as its report gives it in JSON:
    #: its record, which names the commit and the environment it was made
    #: in, where its bytes are, and each rule it breaks, by code.
    astra: dict[str, Any]

    @property
    def commit(self) -> str:
        """The commit its record names, or empty where it has none."""
        return str(self.astra.get("record", {}).get("generated_by", {}).get("commit", ""))

    def as_dict(self) -> dict[str, Any]:
        """Return the status as JSON-ready data.

        Returns:
            Every field, in declaration order.
        """
        return asdict(self)


@dataclass
class StatusReport:
    """Every result the analysis plans, in dependency order."""

    results: list[ResultStatus] = field(default_factory=list)
    #: The lock scan's prose, and what ASTRA notes for the author.
    warnings: list[str] = field(default_factory=list)
    #: ``{tag, state}`` for a containerized project; ``None`` in direct
    #: mode. ``state`` is repository fact only — ``present``, ``absent``
    #: or ``unfetched`` — so status needs no runtime and no network.
    image: dict[str, str] | None = None
    #: The project's environment as a record made now would name it. A
    #: result whose own ``environment`` differs is the one that is behind.
    environment: str = ""
    #: Whether the project is online at all: this clone's ``origin`` is a
    #: repository on GitHub. What of it is published is `lc publish
    #: --check`'s to say.
    online: bool = False

    @property
    def counts(self) -> dict[str, int]:
        """How many results are in each state, states with none included."""
        tally = {"ok": 0, "behind": 0, "stale": 0, "missing": 0}
        for result in self.results:
            tally[result.state] += 1
        return tally

    def as_dict(self) -> dict[str, Any]:
        """Return the report as JSON-ready data.

        Returns:
            The header, the counts, then every result, then the warnings.
        """
        return {
            "image": self.image,
            "environment": self.environment,
            "online": self.online,
            "counts": self.counts,
            "results": [result.as_dict() for result in self.results],
            "warnings": self.warnings,
        }


def status(root: Path) -> StatusReport:
    """Report what state every planned result is in.

    Reads records through ASTRA's checker, and git-annex's log of which
    remote holds the bytes this clone lacks; runs nothing, fetches
    nothing, commits nothing, and does not care whether the tree is
    clean. Classified with nothing refreshed, because this says what the
    project *is* rather than what some run would do to it.

    Args:
        root: The project root.

    Returns:
        One status per planned result, upstream first.

    Raises:
        ProjectError: If the analysis or the lock cannot be read, or
            git-annex is not set up in a clone that lacks a result's
            bytes.
    """
    report = MaterializeReport()
    answer = StatusReport()
    state, tag, archive = container.image_state(root)
    if state != "direct":
        answer.image = {"tag": tag, "state": state, "archive": archive}
    graph, environment = _graph(root, [], report)
    answer.environment = environment
    answer.online = dataset.target(root) is not None
    checked = graph.checked.to_json()["statuses"]
    for found in _classify(root, graph, environment, refresh=()):
        held = found.found.bytes
        unfetched = found.verdict.state in ("ok", "behind") and not (
            held is None or held.state == "present"
        )
        answer.results.append(
            ResultStatus(
                result=found.task.id,
                state=found.verdict.state,
                why=found.verdict.why,
                unfetched=unfetched,
                remotes=list(found.remotes or ()) if unfetched else [],
                astra=checked[found.task.id],
            )
        )
    answer.warnings = report.warnings
    return answer


# =============================================================================
# Fetching — the bytes of current results, into this clone
# =============================================================================


@dataclass
class FetchReport:
    """What a fetch brought into this clone, and what it did not."""

    #: Each result or input whose bytes it fetched, by id.
    fetched: list[str] = field(default_factory=list)
    #: Those whose bytes this clone already held.
    present: list[str] = field(default_factory=list)
    #: Results that are stale or missing, so not fetched: a run makes them
    #: again — id → state.
    left: dict[str, str] = field(default_factory=dict)
    #: Not fetched — id → why.
    failed: dict[str, str] = field(default_factory=dict)
    #: Which of the ids above are inputs': the ones a target named.
    inputs: list[str] = field(default_factory=list)
    #: The lock scan's prose, and what ASTRA notes for the author.
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every result it set out to fetch is here."""
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        """Return the report as JSON-ready data.

        Returns:
            Every field, with ``ok`` first.
        """
        return {"ok": self.ok, **asdict(self)}


def fetch(root: Path, targets: Sequence[str]) -> FetchReport:
    """Bring the bytes of current results, and of named inputs, into this clone.

    A clone, or a pull, gives each result as a stand-in for its bytes. A
    result that is ok or behind is what the analysis asks for, so
    its bytes are fetched from a remote that holds them; one that is stale
    or missing is not, since a run makes it again. A declared input is
    what its commit names, whatever reads it, and is fetched when named.
    Nothing is made and nothing is committed: fetched bytes are the ones
    the record or the commit names, so the tree stays as it was.

    Args:
        root: The project root.
        targets: Each a result's id, an output's for its result in every
            universe, or an input's; empty means every result. Only
            these: what they read is not fetched with them, as a run
            would make it.

    Returns:
        What was fetched, what was already here, and what was not fetched,
        and why.

    Raises:
        ProjectError: If git-annex is not installed, or not set up in a
            clone that lacks a result's bytes, the analysis or the lock
            cannot be read, or a target matches nothing.
    """
    project.require_git_annex()
    scan = MaterializeReport()
    report = FetchReport()
    graph, environment = _graph(root, [], scan)
    report.warnings = scan.warnings
    # ASTRA refuses an analysis in which an input and an output share an
    # id (`AMBIGUOUS_ID`), so a target names one or the other.
    named = {name for key, task in graph.tasks.items() for name in (key, task.name)}
    for target in targets:
        if target not in graph.inputs and target not in named:
            raise ProjectError(
                f"no result or input matches `{target}`. Results: "
                f"{', '.join(sorted(graph.tasks)) or 'none'}. Inputs: "
                f"{', '.join(sorted(graph.inputs)) or 'none'}."
            )
    report.inputs = [target for target in dict.fromkeys(targets) if target in graph.inputs]
    results = [target for target in targets if target not in graph.inputs]
    asked = set(graph.resolve(results)) if targets else set(graph.tasks)
    wanted: dict[str, str] = {}
    for name in report.inputs:
        ref, declared = graph.inputs[name]
        kept = graph.checked.inputs.get(ref)
        if kept is None or kept.state == "present":
            report.present.append(name)
        elif kept.kept_by != "git-annex":
            report.failed[name] = f"its bytes are kept by {kept.kept_by}, not git-annex"
        else:
            wanted[declared] = name
    for found in _classify(root, graph, environment, refresh=()):
        key, held = found.task.id, found.found.bytes
        if key not in asked:
            continue
        if found.verdict.state in ("stale", "missing"):
            report.left[key] = found.verdict.state
        elif held is None or held.state == "present":
            report.present.append(key)
        elif held.kept_by != "git-annex":
            report.failed[key] = f"its bytes are kept by {held.kept_by}, not git-annex"
        else:
            for path in found.task.paths:
                wanted[plan.declared_path(root, path)] = key
    if wanted and not dataset.is_annexed(root):
        # Asked to fetch, git-annex would set itself up, without the rest
        # of what `lc init` converges.
        raise ProjectError(
            "git-annex is not set up in this clone, so nothing can be fetched into it — "
            "`lc init` sets it up."
        )
    for place, why in dataset.get(root, list(wanted)).items():
        name = wanted[place]
        # A result no remote holds any more is one a run makes; nothing
        # makes an input.
        lost = why == dataset.NO_REMOTE and name not in report.inputs
        report.failed[name] = (
            f"{why}, and it is missing now — `lc materialize` makes it again" if lost else why
        )
    report.fetched = [key for key in dict.fromkeys(wanted.values()) if key not in report.failed]
    return report


# =============================================================================
# Executing
# =============================================================================


def materialize(
    root: Path, targets: Sequence[str], *, cluster_id: str, refresh: bool = False
) -> MaterializeReport:
    """Make the results asked for on an explicitly selected, borrowed cluster.

    Args:
        root: The project root.
        targets: Results to make, with what they read; empty means every one.
        cluster_id: The name or immutable ID returned by ``lc compute launch``.
        refresh: Remake the requested results regardless of state.

    Returns:
        What was made, left alone, failed or blocked, and why.

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
    # The graph — and with it the spec validation and the lock scan —
    # before the image: a refusal here must not cost a minutes-long
    # build, and must not leave an archive commit behind a run that
    # "failed" on a typo in the spec.
    graph, environment = _graph(root, targets, report)
    # The declaration and environment must describe committed files before
    # either image preparation or the pinned execution checkout is made.
    if changes := dataset.status(root):
        raise ProjectError(_dirty(root, changes))
    worker.require_in_commit(root, graph.specs)
    asked = graph.resolve(list(targets)) if targets else list(graph.tasks)
    classified = _classify(root, graph, environment, refresh=asked if refresh else ())
    running = [found.task for found in classified if found.may_run()]
    if not running:
        # Nothing is submitted, so no allocation is needed.
        for found in classified:
            _left_alone(found, report)
        return report
    _require_storable(root, graph)
    # A result certain to be remade is the one thing a run need not
    # fetch: one that may run only because something it reads may is
    # left alone when that comes out as recorded, and its readers then
    # read its bytes.
    wanted, trees = _to_fetch(
        root,
        graph,
        running,
        {found.task.id for found in classified if found.certain()},
    )
    dsid = dataset.dataset_id(root)
    with cluster_for_run(cluster_id) as scheduler:
        requirements = scheduler.validate(running)
        if dataset.head(root) != graph.checked.head or dataset.status(root):
            raise ProjectError("the project changed while the run was preparing; run again")
        runtime = container.runtime_for_run(
            root, build=True, use_gpus=any(r.get("GPU", 0) for r in requirements.values()),
        )
        commit = dataset.head(root)
        changed = dataset._git(
            ["diff", "--name-only", graph.checked.head or commit, commit], cwd=root,
        ).splitlines()
        allowed = {runtime.archive, ".datalad/config"} if runtime.mode == "containerized" else set()
        if set(changed) - allowed or dataset.status(root):
            raise ProjectError("the project changed while the run was preparing; run again")
        stage = staging.take(root, commit)
        cleanup = True
        try:
            prepared = replace(graph, tasks={key: task.at(stage.root)
                                             for key, task in graph.tasks.items()})
            certain = {path for found in classified if found.certain()
                       for path in found.task.paths}
            paths = sorted({plan.declared_path(root, path) for task in running
                            for path in task.inputs.values()
                            if path not in certain and plan.at_commit(root, path) is None})
            report.warnings.extend(staging.fill(root, stage.top, stage.root, commit, paths,
                                                fetch=True))
            unreadable = _not_as_recorded(stage.root, prepared, wanted)
            ungiven: dict[str, str] = {}
            for pinned, paths in trees.items():
                try:
                    stage.give(pinned, graph.commits[pinned], paths)
                except ProjectError as exc:
                    ungiven[pinned] = str(exc)
                    report.warnings.append(f"{pinned}: {exc}")
            runtime, warnings = environments.prepare(root, runtime.at(stage.root), environment)
            report.warnings.extend(f"uv: {w}" for w in warnings)
            running = [task.at(stage.root) for task in running]
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
            hashed: dict[Path, tuple[tuple[int, int, int], content.Digest]] = {}
            for path in sorted(path for path, count in readers.items() if count > 1):
                with suppress(OSError, ProjectError):
                    content.require_fetched(path)
                    hashed[path] = content.stamp(path), content.digest_file(path)
            # Every task and record names the same commit, including an image
            # archive that preparation may just have committed.
            context = worker.RunContext(
                environment=environment,
                commit=commit,
                runtime=runtime,
                hashed=hashed,
            )
            pending: dict[ResultId, Any] = {}
            handles = []
            # Results left alone are values, not Dask tasks: they need no
            # allocation resources. Futures for everything that may run retain
            # dependency order.
            for found in classified:
                task = prepared.tasks[found.task.id]
                if why := unreadable.get(task.id):
                    # What reads it is blocked, and with its bytes in this clone
                    # now, ASTRA's checker sees what they are.
                    pending[task.id] = worker.TaskOutcome(task.id, "failed", reason=why)
                    report.failed.append(task.id)
                    report.warnings.append(f"{task.id}: {why}")
                    continue
                if not found.may_run():
                    pending[task.id] = _left_alone(replace(found, task=task), report)
                    continue
                at = {plan.at_commit(stage.root, path) for path in task.inputs.values()}
                if missing := sorted({each[0] for each in at if each} & ungiven.keys()):
                    # Its dependents see an outcome that did not finish, and
                    # are blocked in turn.
                    pending[task.id] = worker.TaskOutcome(
                        task.id, "blocked", reason=f"the tree of {missing[0]} could not be given"
                    )
                    report.blocked.append(task.id)
                    continue
                # A result from a commit is a value too, read once for every
                # task of the run that reads it.
                for entry, dep in task.produced_by.items():
                    if dep not in graph.tasks and dep not in pending:
                        pending[dep] = worker.from_commit(
                            dep, graph.checked.statuses[dep], task.inputs[entry]
                        )
                        if not pending[dep].usable:
                            report.warnings.append(f"{dep}: {pending[dep].reason}")
                # What the checker found goes with the task: downstream of a
                # remake, whether the result still stands is known only once
                # its upstreams are made, and the task answers that from these
                # findings and the bytes they gave, without git.
                cleanup = False
                pending[task.id] = scheduler.submit(
                    worker.materialize,
                    stage.root,
                    task,
                    found.found,
                    found.remotes,
                    context,
                    found.refresh,
                    *[pending[dep] for dep in task.depends_on],
                    key=task.id,
                    resources=requirements[task.id],
                )
                handles.append(pending[task.id])
            outcomes = {outcome.id: outcome for outcome in scheduler.completed(handles)}
            if any(not (attempt / "outcome.json").is_file()
                   for attempt in (stage.root / ".lightcone/tasks").glob("*")):
                # A resubmission can report failure while its original worker
                # still writes. Its claim, unlike that reply, proves no completion.
                raise ProjectError("a task attempt may still be running; no results were collected")
            cleanup = True
            # Dask's delivery order need not be dependency order. All readers have
            # finished, so collection follows ASTRA's order, including partial success.
            made = [graph.tasks[key] for key, outcome in outcomes.items()
                    if outcome.status == "made"]
            destinations = [path for task in made for path in (*task.paths, *task.record_paths)]
            cleanup = False
            with staging.collecting(stage, destinations):
                for key, original in graph.tasks.items():
                    if (outcome := outcomes.get(key)) is None:
                        continue
                    task = prepared.tasks[key]
                    if outcome.status == "made":
                        if any(content.digest_file(path) != outcome.digest for path in task.paths):
                            raise ProjectError(f"{key}: completed bytes changed before collection")
                        with staging.collect(task, original):
                            _consume(root, original, outcome, dsid, runtime, report)
                    else:
                        _consume(root, original, outcome, dsid, runtime, report)
            cleanup = True
            # Extra files remain private to this run and are discarded with it.
            owned = {path for task in prepared.tasks.values() for path in task.paths}
            extras = [str(path.relative_to(stage.root))
                      for folder in {task.path.parent for task in prepared.tasks.values()}
                      for path in folder.rglob("*")
                      if path.is_file() and not path.is_symlink() and path not in owned]
            if extras:
                report.warnings.append(
                    "discarded undeclared files from the run: " + ", ".join(extras)
                )
            return report
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


def _left_alone(found: _Classified, report: MaterializeReport) -> worker.TaskOutcome:
    """Report a result no run has to make, as the value its dependents read."""
    record = found.found.record
    assert record is not None and found.verdict.state in ("ok", "behind")
    if found.verdict.state == "behind":
        report.behind[found.task.id] = found.verdict.why
    else:
        report.unchanged.append(found.task.id)
    path = found.task.path
    return worker.TaskOutcome(
        found.task.id,
        found.verdict.state,
        record.content_hash,
        record.size,
        stamp=content.stamp(path) if path.exists() else None,
        reason=found.verdict.why,
    )


def _consume(
    root: Path,
    task: Task,
    outcome: worker.TaskOutcome,
    dsid: str,
    runtime: container.Runtime,
    report: MaterializeReport,
) -> None:
    """Report one finished task and commit a collected success."""
    name = task.id
    if outcome.notes:
        # Named on a line of their own rather than prefixed onto each: a
        # prefix would land in the middle of a multi-line remedy and make
        # it uncopyable, which is the whole reason these travel separately.
        report.notes.extend([f"{name}:", *outcome.notes])

    if outcome.status == "made":
        dataset.save(
            root, _owned(root, task), run_record(root, task, dsid, runtime),
            annexed=task.paths, in_git=task.record_paths,
            named=dict.fromkeys(task.paths, outcome.digest),
        )
        report.made.append(name)
        return

    if outcome.status == "ok":
        report.unchanged.append(name)
        return

    if outcome.status == "behind":
        report.behind[name] = outcome.reason
        return

    getattr(report, outcome.status).append(name)
    report.warnings.append(f"{name}: {outcome.reason}")


class Scheduler(Protocol):
    """How the driver talks to whatever is running the graph.

    Hand over tasks with their upstream handles and iterate results as
    they land, keeping the commit logic independent of the provider.
    """

    def validate(self, tasks: Iterable[Task]) -> dict[ResultId, dict[str, float]]:
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

    def completed(self, handles: list[Any]) -> Iterator[worker.TaskOutcome]:
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

    def validate(self, tasks: Iterable[Task]) -> dict[ResultId, dict[str, float]]:
        """Require each selected task to fit a worker before any task starts."""
        capacities = worker_capacities(self.workers)
        requests = {}
        for task in tasks:
            try:
                requests[task.id] = TaskResources.parse(task.resources).requirements(capacities)
            except ProjectError as exc:
                raise ProjectError(f"{task.id}: {exc}") from exc
        return requests

    def submit(self, fn: Any, *args: Any, key: str, resources: dict[str, float]) -> Any:
        """Submit an ordinary Dask task with a unique key and forwarded output."""
        from lightcone.engine.compute.output import call

        return self.client.submit(
            call, fn, self.output.topic, key, *args,
            key=f"lc-{self.invocation}-{key}", pure=False, resources=resources,
        )

    def completed(self, handles: list[Any]) -> Iterator[worker.TaskOutcome]:
        """Yield each task's outcome as Dask completes it."""
        from distributed import as_completed

        try:
            for _, outcome in as_completed(  # type: ignore[no-untyped-call]
                handles, with_results=True, loop=self.client.loop
            ):
                if not self.output.wait(outcome.id):
                    outcome = replace(
                        outcome,
                        notes=(*outcome.notes,
                               "remote output forwarding did not finish before its deadline"),
                    )
                yield outcome
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


#: The command that fetches content another tool keeps, by the name ASTRA's
#: checker gives the tool.
_FETCHED_BY = {"Git LFS": "git lfs pull", "DVC": "dvc pull"}


def _to_fetch(
    root: Path, graph: Graph, running: Sequence[Task], certain: set[ResultId]
) -> tuple[list[str], dict[str, list[str]]]:
    """What a run has to give its recipes before they read it.

    Every file a task that may run reads, bar a result certain to be
    remade: its declared inputs, and each result it reads that may be
    left alone, whose bytes are then what the recipe reads. A file of the
    working tree is fetched where git-annex keeps it and this clone lacks
    it; a file at another commit is given in that commit's tree
    (:mod:`commits`), whether git or git-annex keeps it. Which tool keeps
    each is ASTRA's checker's answer, and what another tool keeps is
    refused, before anything runs, since a recipe handed its stand-in
    would read a few lines of text for its data.

    Args:
        root: The project root.
        graph: The run's graph, with the checker's report.
        running: The tasks that may run.
        certain: The ids of those certain to be remade.

    Returns:
        The files for git-annex to fetch, from the project root, and the
        files to give at each other commit, by commit, from the top of its
        tree.

    Raises:
        ProjectError: Naming each file another tool keeps, and the
            command that fetches it.
    """
    wanted: set[str] = set()
    trees: dict[str, set[str]] = {}
    elsewhere: set[str] = set()
    for task in running:
        for source in task.result.inputs:
            if source.result in certain:
                continue
            # A result from a commit that nothing is built from is read by
            # nothing, and is not fetched (STALE_INPUT).
            if source.result is not None and source.result not in graph.tasks:
                if worker.unreadable(graph.checked.statuses[source.result]):
                    continue
            held = (
                graph.checked.statuses[source.result].bytes
                if source.result is not None
                else graph.checked.inputs.get(source.input or "")
            )
            path = task.inputs[source.entry]
            at = plan.at_commit(root, path)
            # The tool whose stand-in is there, for bytes this clone lacks.
            keeper = held.kept_by if held is not None and held.state == "absent" else None
            if keeper and keeper != "git-annex":
                command = _FETCHED_BY.get(keeper) if at is None else None
                how = f"; `{command}` fetches it" if command else ""
                elsewhere.add(f"{plan.declared_path(root, path)} — kept by {keeper}{how}")
            elif at is not None:
                trees.setdefault(at[0], set()).add(at[1])
            elif keeper:
                wanted.add(plan.declared_path(root, path))
    if elsewhere:
        raise ProjectError(
            "this clone holds only a stand-in for what this run's recipes read, and lc "
            "fetches only what git-annex keeps:\n  " + "\n  ".join(sorted(elsewhere))
        )
    return sorted(wanted), {commit: sorted(paths) for commit, paths in trees.items()}


def _not_as_recorded(root: Path, graph: Graph, fetched: Sequence[str]) -> dict[ResultId, str]:
    """The results a run fetched whose bytes are not the ones their records give.

    ASTRA's checker compares a result's bytes with its record before they
    are fetched only where their stand-in gives a SHA-256, and takes one
    that gives none — git-annex's, for a key of another backend — to stand
    for the recorded bytes. What reads a result is recorded as reading the
    bytes its record gives, so what a run fetched for a recipe to read is
    hashed before any recipe reads it. Hashing what was just fetched costs a
    fraction of fetching it.

    Args:
        root: The project root.
        graph: The run's graph, with the checker's report.
        fetched: The files the run fetched, from the project root.

    Returns:
        Why each such result cannot be read, by id. A file the fetch did not
        bring is left to what reads it to report.
    """
    wanted = set(fetched)
    unreadable = {}
    for task in graph.tasks.values():
        record = graph.checked.statuses[task.id].record
        if record is None or plan.declared_path(root, task.path) not in wanted:
            continue
        try:
            content.require_fetched(task.path)
            digest = content.digest_file(task.path)
        except (OSError, ProjectError):
            continue
        if digest != (record.content_hash, record.size):
            unreadable[task.id] = (
                "the bytes fetched for it are not the ones its record gives, so nothing "
                "can read it as recorded — with them in this clone, the next run makes "
                "it again"
            )
    return unreadable


def _require_storable(root: Path, graph: Graph) -> None:
    """Refuse a project whose results git would not store as lc means them.

    Asked of git for the very paths a run would commit, because the
    rules are files the researcher owns and convergence only appends to:
    an ignored result is committed by nobody and reported made; one that
    no line sends to the annex lands in git itself, in every clone
    forever; one another filter takes (Git LFS) is stored where lc reads
    and fetches nothing; and a record that leaves git cannot be read on a
    clone that holds no content. In a clone where git-annex was never set
    up, no line sends anything to the annex.

    What the attributes settle is refused here, before anything runs. An
    ``annex.largefiles`` expression can turn on the bytes themselves, so
    where one decides, what git staged is the answer, and every save
    checks it (``dataset.save``).

    Raises:
        ProjectError: Naming each such path.
    """
    if not dataset.is_annexed(root):
        raise ProjectError(
            "git-annex is not set up in this clone, so git would commit every result "
            "to git itself — `lc init` sets it up."
        )
    files = [plan.declared_path(root, path) for task in graph.tasks.values() for path in task.paths]
    records = [
        plan.declared_path(root, record)
        for task in graph.tasks.values()
        for record in task.record_paths
    ]
    # NUL-separated both ways, and on stdin: git otherwise quotes a path
    # holding an accent, a space would end one, and a command line holds
    # only so many. `check-ignore` takes `-z` no other way.
    paths = "".join(f"{path}\0" for path in (*files, *records))
    asked = project._run(
        ["git", "check-ignore", "-z", "--no-index", "--stdin"], cwd=root, stdin=paths
    )
    ignored = [path for path in asked.stdout.split("\0") if path]
    attributed = project._run(
        ["git", "check-attr", "-z", "--stdin", "annex.largefiles", "filter"],
        cwd=root,
        stdin=paths,
    )
    if attributed.returncode != 0:
        raise ProjectError(f"`git check-attr` failed:\n{attributed.stderr.strip()}")
    listed = attributed.stdout.split("\0")
    attributes = {
        (path, name): value
        for path, name, value in zip(listed[::3], listed[1::3], listed[2::3], strict=False)
    }

    unannexed: set[str] = set()
    refusals = [
        f"{path} is git-ignored, so it would never be committed — remove the "
        ".gitignore rule that covers it"
        for path in ignored
    ]
    for path in files:
        taken_by = attributes.get((path, "filter"), "unspecified")
        if taken_by not in ("annex", "unspecified", "unset"):
            refusals.append(
                f"{path} would be stored by `filter={taken_by}`, not git-annex — a "
                ".gitattributes line below lightcone's sets its filter"
            )
        elif taken_by != "annex" or attributes.get((path, "annex.largefiles")) == "nothing":
            refusals.append(
                f"{path} would be committed to git itself — the last .gitattributes line "
                "that matches it does not send it to the annex"
            )
            unannexed.add(path)
    for path in records:
        taken_by = attributes.get((path, "filter"), "unspecified")
        if taken_by not in ("annex", "unspecified", "unset"):
            how = f"stored by `filter={taken_by}`"
        elif taken_by == "annex" and attributes.get((path, "annex.largefiles")) == "anything":
            how = "annexed"
        else:
            continue
        refusals.append(
            f"{path} would be {how} — the last .gitattributes line that matches it says "
            "so, and a record has to stay in git, where a clone with no content can read it"
        )
    if refusals:
        # Lightcone's lines annex the project's own `results/`; a result of
        # an analysis in a folder of the project is in that folder's, which
        # its record's place names (`<folder>/.astra/records/…`).
        elsewhere = sorted(
            {
                place.record_path.partition("/.astra/records/")[0]
                for task in graph.tasks.values()
                for place in task.result.places
                if place.file_path in unannexed and "/.astra/records/" in place.record_path
            }
        )
        raise ProjectError(
            "this project's results would not be stored as lc stores them:\n  "
            + "\n  ".join(refusals)
            + "\n`lc init` adds any of lightcone's .gitattributes lines that is missing. "
            "Where it adds none, a later line overrides them: remove it, or move "
            "lightcone's lines below it."
            + "".join(
                f"\nThe analysis in {folder} keeps its results in {folder}/results/: add "
                f"`{folder}/results/** annex.largefiles=anything` at the end of the "
                "project's .gitattributes."
                for folder in elsewhere
            )
        )


# =============================================================================
# The commit
# =============================================================================


def run_record(root: Path, task: Task, dsid: str, runtime: container.Runtime) -> str:
    """Build the commit message for one result a run made.

    A ``[DATALAD RUNCMD]`` record — datalad's format, not ours, so all of
    it is written and none of it abbreviated. ``datalad rerun`` reads it
    with a regex and reports "no command; skipping" on any mismatch.

    ``cmd`` is the worker module, never a console script, behind an
    ephemeral ``uv run`` pinning the engine that made the result
    (:func:`_engine_requirement`) — so the rerun executes that engine
    rather than whatever the host has grown into. The bare recipe would
    reconstruct nothing lc adds, and ``lc materialize`` cannot be it
    because a rerun removes the result's files first, dirtying the tree
    materialize refuses to start from. The worker rebuilds the *project*
    environment itself from the lock of the commit being rerun.

    Args:
        root: The project root.
        task: The result that was made.
        dsid: The dataset's UUID, which ``rerun`` reads.
        runtime: The run's execution world. A containerized run lists its
            committed image archive under ``extra_inputs``, which
            ``datalad rerun`` fetches before executing — so the worker
            enters the exact bytes that made the result, on whatever
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
            f"python -m lightcone.engine.worker {shlex.quote(task.id)}"
        ),
        "dsid": dsid,
        "exit": 0,
        # What datalad fetches before it reruns: the files of the dataset.
        # One at another commit is the worker's to give, as a run gives it.
        "inputs": sorted(
            plan.declared_path(root, path)
            for path in task.inputs.values()
            if plan.at_commit(root, path) is None
        ),
        "outputs": [
            plan.declared_path(root, path) for path in (*task.paths, *task.record_paths)
        ],
        "pwd": ".",
    }
    if runtime.mode == "containerized":
        info["extra_inputs"] = [runtime.archive]
    body = json.dumps(info, indent=1, sort_keys=True, ensure_ascii=False)
    return (
        f"[DATALAD RUNCMD] {task.id}\n\n"
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
            "the lock has dependencies that cannot be audited, so a result's "
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
    if dataset.is_annexed(root):
        # Before ASTRA's checker reads the place of each result: a file
        # whose bytes are here, left as a stand-in, reads as one to fetch.
        dataset.populate(root)
        # And before git-annex is asked who holds the rest: a store a pull
        # brought is not one this clone fetches from until it is enabled.
        dataset.enable_store(root)
    graph = plan.build(root)
    report.warnings.extend(graph.notes)
    if targets:
        graph = graph.closure(graph.resolve(list(targets)))
    return graph, environment


def _owned(root: Path, task: Task) -> list[str]:
    """Every path one result owns, as git pathspecs taken to the letter:
    its file and its records (`Task.owns`).

    Never the parent directory: siblings share it, and under Dask they are
    in flight — ``git clean`` on it would delete a running task's bytes.
    """
    return [
        f":(literal){plan.declared_path(root, path)}"
        for path in (*task.paths, *task.record_paths)
    ]


def _dirty(root: Path, changes: Sequence[tuple[str, str]]) -> str:
    """Name uncommitted work without guessing which files the researcher wants kept."""
    paths = "\n".join(f"  {code.strip() or '??'} {path}" for code, path in changes)
    return (
        f"uncommitted changes in {root} — a record must name the commit its recipe ran on:\n"
        f"{paths}\nReview and commit these changes before running `lc materialize`."
    )
