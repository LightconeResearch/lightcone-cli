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
from lightcone.engine.project import ProjectError, child_env, current_project

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
    #: Why it ran, or why it did not finish. Shown to the user verbatim.
    reason: str = ""
    #: Console lines from the boundary: a downgrade notice, a denial.
    notes: tuple[str, ...] = ()
    #: What the sandbox actually enforced, when a recipe ran.
    hermeticity: dict[str, object] | None = None

    @property
    def usable(self) -> bool:
        """Whether a dependent may proceed on this result."""
        return self.status in ("ok", "skipped")


# =============================================================================
# The Dask unit: decide, then execute
# =============================================================================


def materialize(root: Path, task: Task, env_version: str, *upstream: TaskResult) -> TaskResult:
    """Make *task* if it needs making. What Dask submits, once per task.

    *upstream* arrives as the results of the futures this task was given
    as arguments, which is what makes Dask the scheduler: the ordering
    falls out of the argument graph rather than out of a loop here.
    """
    reported = {u.key: u for u in upstream if u.usable}
    if absent := [dep for dep in task.depends_on if dep not in reported]:
        names = ", ".join(f"{u}/{o}" for u, o in absent)
        return TaskResult(task.key, "blocked", reason=f"upstream did not finish: {names}")

    try:
        versions = _input_versions(task, {k: u.data_version for k, u in reported.items()})
    except FileNotFoundError as e:
        return TaskResult(task.key, "failed", reason=f"declared input is missing: {e}")

    manifest = assets.read(task.output_dir)
    reason = assets.staleness(
        code_version=task.code_version, manifest=manifest, inputs=versions
    )
    if reason is None and manifest is not None:
        # The recorded digest, never a recomputed one: on a clone that has
        # fetched no annex content the files are dangling symlinks, and
        # rehashing them would quietly report a different output.
        return TaskResult(task.key, "skipped", data_version=manifest.data_version)

    return execute(root, task, env_version, versions, reason=str(reason))


# =============================================================================
# Executing one task, unconditionally
# =============================================================================


def execute(
    root: Path,
    task: Task,
    env_version: str,
    input_versions: Mapping[str, str],
    *,
    reason: str = "",
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

    read_paths = [p for p in task.inputs.values() if p.exists()]
    policy = sandbox.recipe_policy(root, task.output_dir, read_paths=read_paths)
    try:
        outcome = sandbox.run(
            sandbox.detect(),
            policy,
            [_SHELL, "-c", task.recipe],
            cwd=root,
            prefix=_uv_prefix(root),
            env=child_env(),
        )
    finally:
        shutil.rmtree(policy.tmp_home, ignore_errors=True)

    if outcome.returncode != 0:
        return TaskResult(
            task.key,
            "failed",
            reason=f"the recipe exited {outcome.returncode}",
            notes=outcome.notes,
        )
    if drift := _gate(root, env_version):
        return TaskResult(task.key, "failed", reason=drift, notes=outcome.notes)

    sha, remote = dataset.head(root)
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
    return TaskResult(
        task.key,
        "ok",
        data_version=data_version,
        reason=reason,
        notes=outcome.notes,
        hermeticity=asdict(outcome.attestation),
    )


def _gate(root: Path, env_version: str) -> str:
    """Empty while the environment is still the one the run started in."""
    if identity.env_version(root) == env_version:
        return ""
    return (
        "the environment changed while the run was in flight — uv.lock, "
        ".python-version, or an install setting was edited. Nothing was "
        "recorded; re-run `lc materialize`."
    )


def _uv_prefix(root: Path) -> list[str]:
    """``uv run``, outside the boundary, and syncing nothing.

    ``--no-sync`` because the environment was converged before the run
    started: syncing here would have every concurrent task writing the
    same ``.venv``. ``--locked`` still holds, so a lock that drifted under
    the run is uv's loud error rather than a silent relock. Always an
    explicit ``--project``: uv's own walk-up discovery is never trusted.
    """
    return ["uv", "run", "--locked", "--no-sync", "--project", str(root), "--"]


def _input_versions(task: Task, upstream: Mapping[Key, str]) -> dict[str, str]:
    """Each declared input's content identity, right now.

    An input another task produces takes that task's answer — computed in
    the worker that made it, before anything was staged. Everything else
    is hashed from disk.
    """
    return {
        name: upstream[key] if (key := task.produced_by.get(name)) else assets.data_version(path)
        for name, path in task.inputs.items()
    }


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
        result = execute(root, task, env_version, _from_disk(task))
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
    """Upstream versions, read from the manifests already in the tree."""
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
