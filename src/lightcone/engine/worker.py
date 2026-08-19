"""Making one output — the unit of work, and the only thing that runs a recipe.

Also an entry point:

    python -m lightcone.engine.worker <universe>/<output_id>

which is what the ``[DATALAD RUNCMD]`` record in every materialization
commit names. That is why it is a module rather than an ``lc`` verb: it
skips the staleness check, commits nothing, and leaves the tree dirty by
design — precisely the state ``lc materialize`` refuses to start from —
so advertising it in ``lc --help`` would hand people a footgun. A
``uv run --locked --project .`` in front of it reconstructs the exact
engine that produced an output from the lock of the commit that recorded
it, module path included.

Keep this module cheap to import: no click, no rich. It is on the
``python -m`` path of every rerun, and of every task in every run.

Nothing here writes to git, and nothing here raises. A task that fails
returns a result saying so, because Dask propagates an exception to every
dependent and "who actually failed" would stop being answerable —
reporting every independent failure in one run is most of the point of
owning the loop.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from lightcone.engine import assets, dataset, identity, plan, sandbox
from lightcone.engine.plan import Key, Task
from lightcone.engine.project import (
    ProjectError,
    child_env,
    current_project,
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
    status: Literal["ok", "skipped", "failed", "blocked"]
    #: The output's content identity. Present for ``ok`` and ``skipped``,
    #: which are the two states in which the bytes on disk are current —
    #: this is what a dependent compares against.
    data_version: str = ""
    #: Why it did not finish. Shown to the user verbatim.
    reason: str = ""
    #: Console lines from the boundary: a downgrade notice, a denial.
    notes: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Whether a dependent may proceed on this result."""
        return self.status in ("ok", "skipped")


# =============================================================================
# The Dask unit: decide, then execute
# =============================================================================


def materialize(
    root: Path,
    task: Task,
    env_version: str,
    head: Head,
    versions: assets.Versions,
    *upstream: TaskResult,
) -> TaskResult:
    """Make *task* if it needs making. What Dask submits, once per task.

    This is where "the worker never raises" is enforced, rather than at
    each fallible call inside it. Dask re-raises a task's exception in the
    driver, which would abort every other task in flight — so the contract
    is absolute, and a contract assembled from individually-guarded call
    sites is only as true as the last person to add one.

    *upstream* arrives as the results of the futures this task was given
    as arguments, which is what makes Dask the scheduler: the ordering
    falls out of the argument graph rather than out of a loop here.
    *head* and *versions* are the run's, handed down: the driver commits
    as outputs land so HEAD moves under the run, and one input declared by
    many outputs is the same bytes every time.
    """
    try:
        return _materialize(root, task, env_version, head, versions, upstream)
    except Exception as e:  # the contract is that this function returns
        return TaskResult(task.key, "failed", reason=f"{type(e).__name__}: {e}")


def _materialize(
    root: Path,
    task: Task,
    env_version: str,
    head: Head,
    versions: assets.Versions,
    upstream: tuple[TaskResult, ...],
) -> TaskResult:
    reported = {u.key: u for u in upstream if u.usable}
    if absent := [dep for dep in task.depends_on if dep not in reported]:
        names = ", ".join(f"{u}/{o}" for u, o in absent)
        return TaskResult(task.key, "blocked", reason=f"upstream did not finish: {names}")

    live = {key: u.data_version for key, u in reported.items()}
    inputs = {
        name: live[key] if (key := task.produced_by.get(name)) else versions.of(path)
        for name, path in task.inputs.items()
    }
    manifest = assets.read(task.output_dir)
    if assets.staleness(code_version=task.code_version, manifest=manifest, inputs=inputs) is None:
        # The recorded digest, never a recomputed one: on a clone that has
        # fetched no annex content the files are dangling symlinks, and
        # rehashing them would quietly report a different output.
        assert manifest is not None  # staleness returns a reason when it is None
        return TaskResult(task.key, "skipped", data_version=manifest.data_version)

    return execute(root, task, env_version, inputs, head=head)


# =============================================================================
# Executing one task, unconditionally
# =============================================================================


def execute(
    root: Path,
    task: Task,
    env_version: str,
    input_versions: Mapping[str, str],
    *,
    head: Head,
) -> TaskResult:
    """Run *task*'s recipe and record what it produced.

    The environment is checked on both sides of the recipe: an edit to the
    lock while a long graph runs would otherwise leave manifests claiming
    an environment that no longer existed by the time the recipe ran.

    The output directory is reset first. The recipe owns it, and a file
    left over from a previous run would otherwise survive into the content
    hash and be committed as part of an output that never produced it.
    """
    if drift := _gate(root, env_version):
        return TaskResult(task.key, "failed", reason=drift)

    shutil.rmtree(task.output_dir, ignore_errors=True)
    task.output_dir.mkdir(parents=True)

    read_paths = [p for p in task.inputs.values() if p.exists()]
    policy = sandbox.exec_policy(root, read_paths=read_paths)
    with sandbox.scope(policy):
        outcome = sandbox.run(
            sandbox.detect(),
            policy,
            [_SHELL, "-c", task.recipe],
            cwd=root,
            prefix=uv_prefix(root, sync=False),
            env=child_env(),
        )

    if outcome.returncode != 0:
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe exited {outcome.returncode}",
            notes=outcome.notes,
        )
    if drift := _gate(root, env_version):
        return TaskResult(task.key, "failed", reason=drift, notes=outcome.notes)

    # Guarded separately from the boundary catch above it, because these
    # two failures deserve different words: "your recipe failed" and "your
    # recipe worked and we could not record it" are different problems.
    try:
        sha, remote = head
        data_version = assets.data_version(task.output_dir)
        assets.write(
            task.output_dir,
            assets.Manifest(
                output_id=task.output_id,
                universe_id=task.universe_id,
                recipe=task.recipe,
                code_version=task.code_version,
                env_version=env_version,
                data_version=data_version,
                decisions=dict(task.decisions),
                input_versions=dict(input_versions),
                git_sha=sha,
                git_remote=remote,
                lc_version=_lc_version(),
                hermeticity=asdict(outcome.attestation),
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


def _gate(root: Path, env_version: str) -> str:
    """Empty while the environment is still the one the run started in."""
    if identity.env_version(root) == env_version:
        return ""
    return (
        "the environment changed while the run was in flight — uv.lock, "
        ".python-version, or an install setting was edited. Nothing was "
        "recorded; re-run `lc materialize`."
    )


def _lc_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("lightcone-cli")
    except PackageNotFoundError:  # pragma: no cover - only in a source tree
        return ""


# =============================================================================
# The entry point the run record names
# =============================================================================


def main(argv: list[str]) -> int:
    """``python -m lightcone.engine.worker <universe>/<output_id>``.

    A thin wrapper over :func:`execute` — the same function Dask calls, so
    this is an entry point rather than a second implementation. It runs the
    task unconditionally: a rerun is a rerun, and the caller (a person, or
    ``datalad rerun``) has already said what they want.

    Upstream versions come from the manifests on disk, because there is no
    graph in flight to take them from. An upstream that has never been
    materialized is a refusal, not a silent zero.
    """
    if len(argv) != 1 or "/" not in argv[0]:
        print("usage: python -m lightcone.engine.worker <universe>/<output_id>", file=sys.stderr)
        return 2

    universe_id, _, output_id = argv[0].partition("/")
    try:
        root = current_project()
        env_version = identity.env_version(root)
        graph = plan.build(root, env_version=env_version)
        task = graph.tasks[(universe_id, output_id)]
        # This one-task run reads HEAD for itself, because it *is* the
        # driver here — the rule is that the run's commit is read once by
        # whoever owns the run, not that a worker never reads it.
        result = execute(
            root, task, env_version, _from_disk(task), head=dataset.head(root)
        )
    except KeyError:
        print(f"no output `{argv[0]}` in this project", file=sys.stderr)
        return 2
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
        if task.produced_by.get(name) is None:
            versions[name] = assets.data_version(path)
        elif (manifest := assets.read(path)) is None:
            raise ProjectError(
                f"the input `{name}` has never been materialized — there is no "
                f"manifest in {path}. Run `lc materialize` instead."
            )
        else:
            versions[name] = manifest.data_version
    return versions


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
