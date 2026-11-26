"""Development probes through the same per-execution staging as recipes.

A probe starts at HEAD, overlays uncommitted files as read-only links, and
exposes locally available annex objects without bulk fetching. It uses the
same complete environment cache and boundary as recipes. New/replaced regular
files beneath results/ are collected after the probe reports, even on a
nonzero exit; deletions and undeclared files are confined to the run tree.
An interrupted probe retains its tree because its subprocess may still run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from lightcone.engine import container, content, dataset, environments, identity, sandbox, staging
from lightcone.engine.execution_resources import TaskResources, worker_capacities
from lightcone.engine.project import (
    ProjectError,
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
    with compute.connect(cluster_id) as client:
        resources = TaskResources().requirements(
            worker_capacities(client.scheduler_info()["workers"]), whole_worker=True,
        )
        runtime = container.runtime_for_run(project, build=False)
        stage = staging.take(project, dataset.head(project))
        cleanup = True
        try:
            paths = staging.probe_files(stage)
            before = {path: content.stamp(path) for path in (stage.root / "results").rglob("*")
                      if path.is_file() and not path.is_symlink()}
            runtime, warnings = environments.prepare(
                project, runtime.at(stage.root), identity.environment(stage.root),
            )
            notes = [f"uv: {warning}" for warning in warnings]
            use_gpus = resources.get("GPU", 0) > 0 and runtime.supports_gpus
            if resources.get("GPU", 0) > 0 and not use_gpus:
                notes.append(
                    f"GPU access is not supported by {runtime.runtime}; "
                    "this probe runs without GPUs"
                )
            invocation = uuid4().hex
            with forwarding(client) as output:
                cleanup = False
                future = client.submit(
                    call, _probe, output.topic, "probe", runtime, paths, tuple(command),
                    use_gpus,
                    key=f"lc-{invocation}-probe", pure=False, resources=resources,
                )
                try:
                    outcome: sandbox.Outcome = future.result()
                    cleanup = True
                except ProjectError:
                    raise
                except Exception as exc:
                    raise ProjectError(
                        f"cluster execution failed: {exc}. {compute.UNSTOPPED}"
                    ) from exc
                if not output.wait("probe"):
                    notes.append("remote output forwarding did not finish before its deadline")
            # Existing local files are symlinks; only files the probe made are regular.
            made = [path for path in (stage.root / "results").rglob("*")
                    if path.is_file() and not path.is_symlink()
                    and before.get(path) != content.stamp(path)]
            cleanup = False
            destinations = [project / path.relative_to(stage.root) for path in made]
            with staging.collecting(stage, destinations):
                for path in made:
                    target = project / path.relative_to(stage.root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    path.replace(target)
            cleanup = True
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
            prefix=uv_prefix(runtime.root), env=runtime.env, output=output,
        )
    return outcome
