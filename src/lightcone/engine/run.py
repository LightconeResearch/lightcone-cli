"""``lc run`` executes a command inside the reproducible environment.

Byte-for-byte the environment recipes will get — the same lock, the same
converged ``.venv`` — under the same sandbox. That equivalence is the
point: if a probe works, the recipe will, and if a probe
is denied, the recipe would have been.

What the boundary catches is a reach *outside* the declared set — a
tool, a library, or a data file that is on this machine and would not be
in the image. The tree itself is read-only apart from ``results/``,
which is where output goes, so the environment a run started with is the
one it finishes with.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from lightcone.engine import container, plan, sandbox
from lightcone.engine.execution_resources import TaskResources, worker_capacities
from lightcone.engine.project import (
    ProjectError,
    child_env,
    require_uv,
    uv_prefix,
    uv_scrub_warning,
)


def probe(project: Path, command: Sequence[str], *, cluster_id: str) -> sandbox.Outcome:
    """Run a sandboxed command on one worker of the selected cluster.

    Args:
        project: The shared project root.
        command: Command argv; no implicit shell is opened.
        cluster_id: The name or immutable ID returned by ``lc compute launch``.

    Returns:
        The command's exit status, sandbox attestation and diagnostic notes.

    Raises:
        ProjectError: If the cluster or its workers cannot execute this project.
    """
    from lightcone.engine import compute
    from lightcone.engine.compute.output import call, forwarding

    require_uv()
    paths = input_paths(project)
    with compute.connect(cluster_id) as client:
        resources = TaskResources().requirements(
            worker_capacities(client.scheduler_info()["workers"]), whole_worker=True,
        )
        runtime = container.runtime_for_run(project, build=False)
        notes = [f"uv: {warning}" for warning in container.converge(runtime)]
        use_gpus = resources.get("GPU", 0) > 0 and runtime.supports_gpus
        if resources.get("GPU", 0) > 0 and not use_gpus:
            notes.append(
                f"GPU access is not supported by {runtime.runtime}; this probe runs without GPUs"
            )
        invocation = uuid4().hex
        with forwarding(client) as output:
            future = client.submit(
                call, _probe, output.topic, "probe", runtime, paths, tuple(command),
                use_gpus,
                key=f"lc-{invocation}-probe", pure=False, resources=resources,
            )
            try:
                outcome: sandbox.Outcome = future.result()
            except ProjectError:
                raise
            except Exception as exc:
                raise ProjectError(
                    f"cluster execution failed: {exc}. {compute.UNSTOPPED}"
                ) from exc
            if not output.wait("probe"):
                notes.append("remote output forwarding did not finish before its deadline")
    if warning := uv_scrub_warning():
        notes.append(warning)
    return replace(outcome, notes=(*notes, *outcome.notes))


def _probe(
    runtime: container.Runtime, paths: list[Path], command: tuple[str, ...],
    use_gpus: bool,
    *, output: Callable[[str, bytes], None],
) -> sandbox.Outcome:
    """Execute the prepared probe; the driver alone converges its environment."""
    built = container.policy_for(runtime, paths, use_gpus=use_gpus)
    with sandbox.scope(built) as policy:
        outcome = sandbox.run(
            container.backend(runtime), policy, command, cwd=runtime.root,
            prefix=uv_prefix(runtime.root), env=child_env(), output=output,
        )
    return outcome


def input_paths(project: Path) -> list[Path]:
    """Collect the files the analysis declares as inputs, best-effort.

    Every analysis of the stack read in the working tree, not just the
    project's own: a declared analysis has inputs of its own, and a probe
    denied one is a denial the researcher cannot act on — the file is
    declared, just not at the top of the tree. Bound rather than
    validated, because a probe exists to debug a project, and a spec that
    breaks a rule is exactly when someone runs one.

    Args:
        project: The project root.

    Returns:
        The resolved paths that exist; none where there is no analysis
        ASTRA can read.
    """
    from astra import api
    from astra.cache import CacheError
    from astra.sources.git import GitError

    try:
        stack = api.bind(project).stack
    except (api.NoAnalysisError, GitError, CacheError, OSError):
        return []
    if stack is None:
        return []
    above = posixpath.dirname(stack.analyses[stack.root].file)
    found: list[Path] = []
    for member, analysis in stack.determined():
        if member.identity.commit is None:
            folder = project / plan.relative(member.identity.folder, above)
            for declared in analysis.inputs.values():
                if (path := folder / declared.path).exists():
                    found.append(path.resolve())
    return list(dict.fromkeys(found))
