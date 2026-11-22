"""``lc run`` executes a command inside the reproducible environment.

The environment the project's lock gives, under the sandbox a recipe
runs under. A probe runs in the project's own working tree, where the
researcher is, on what it holds now; a recipe runs in a checkout of the
run's commit (:mod:`checkout`), on what that commit holds and its
output declares. So a probe that is denied, the recipe would have been;
one that works reads what is there, committed or not, declared or not.

What the boundary catches is a reach *outside* the declared set — a
tool, a library, or a data file that is on this machine and would not be
in the image. The tree itself is read-only apart from ``results/``,
which is where output goes, so the environment a run started with is the
one it finishes with.
"""

from __future__ import annotations

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
            container.backend(runtime, workdir=runtime.root), policy, command, cwd=runtime.root,
            prefix=uv_prefix(runtime.root), env=child_env(), output=output,
        )
    return outcome


def input_paths(project: Path) -> list[Path]:
    """Collect the files the analysis declares as inputs, best-effort.

    Those of every analysis read from the working tree, as its recipes
    get them: an analysis the project declares or drives has inputs of
    its own, and a probe denied one is a denial the researcher cannot act
    on. An input of an analysis at another commit is no file of this tree.

    An analysis ASTRA cannot resolve declares nothing a probe could be
    granted, and a probe is how someone debugs one, so that is no refusal
    here.

    Args:
        project: The project root.

    Returns:
        The resolved paths that exist; none where there is no analysis
        ASTRA can resolve.
    """
    from astra import api

    try:
        with plan.asking_astra(project):
            # Offline: a probe reads the project, and never a remote for it.
            analysis = api.resolve(project, offline=True)
    except ProjectError:
        return []
    found = [
        path.resolve()
        for each in analysis.analyses
        if each.commit is None
        for declared in each.inputs
        if (path := project / declared.path).exists()
    ]
    return list(dict.fromkeys(found))
