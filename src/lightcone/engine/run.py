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

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from lightcone.engine import container, sandbox
from lightcone.engine.project import (
    SPEC_FILENAME,
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
        cluster_id: The allocation identity returned by ``lc compute launch``.

    Returns:
        The command's exit status, sandbox attestation and diagnostic notes.

    Raises:
        ProjectError: If the cluster or its workers cannot execute this project.
    """
    from lightcone.engine import compute
    from lightcone.engine.compute.output import call, forwarding

    with compute.connect(cluster_id) as client:
        require_uv()
        paths = input_paths(project, read_spec(project))
        runtime = container.runtime_for_run(project, build=False)
        warnings = container.converge(runtime)
        invocation = uuid4().hex
        with forwarding(client, invocation) as output:
            future = client.submit(
                call, _probe, output.topic, "probe", runtime, paths, tuple(command),
                key=f"lc-{invocation}-probe", pure=False,
            )
            try:
                outcome: sandbox.Outcome = future.result()
            except ProjectError:
                raise
            except Exception as exc:
                raise ProjectError(
                    f"cluster execution failed: {exc}. lc did not stop the allocation; "
                    "tasks that did not report may still be running"
                ) from exc
            if not output.wait("probe"):
                warnings.append("remote output forwarding did not finish before its deadline")
    notes = [*(f"uv: {warning}" for warning in warnings)]
    if warning := uv_scrub_warning():
        notes.append(warning)
    return replace(outcome, notes=tuple(dict.fromkeys((*outcome.notes, *notes))))


def _probe(
    runtime: container.Runtime, paths: list[Path], command: tuple[str, ...],
    *, output: Callable[[str, bytes], None],
) -> sandbox.Outcome:
    """Execute the prepared probe; the driver alone converges its environment."""
    built = container.policy_for(runtime, paths)
    with sandbox.scope(built) as policy:
        outcome = sandbox.run(
            container.backend(runtime), policy, command, cwd=runtime.root,
            prefix=uv_prefix(runtime.root, sync=False), env=child_env(), output=output,
        )
    if warning := uv_scrub_warning():
        outcome = replace(outcome, notes=(*outcome.notes, warning))
    return outcome


def read_spec(project: Path) -> dict[str, Any]:
    """Read the project's spec, best-effort.

    A probe exists to debug a project, and a spec whose sub-analysis
    references are stale is exactly when someone runs one.

    Args:
        project: The project root.

    Returns:
        The spec with sub-analyses merged in; the top-level document alone
        if the tree will not resolve; an empty spec if there is none.
    """
    from astra.helpers import load_yaml, resolve_analysis_tree

    spec_path = project / SPEC_FILENAME
    if not spec_path.exists():
        return {}
    data: dict[str, Any] = load_yaml(spec_path)
    try:
        return dict(resolve_analysis_tree(data, project))
    except Exception:
        return data


def input_paths(project: Path, spec: dict[str, Any]) -> list[Path]:
    """Collect the declared inputs that are filesystem paths.

    ASTRA's ``source`` is free-form — a URI, a dotted name, a path — so
    the test for "is this a path" is whether it resolves to something that
    exists. Anything else is somebody else's input kind.

    Args:
        project: The project root, for resolving relative sources.
        spec: The spec to read inputs from.

    Returns:
        The resolved paths that exist.
    """
    from astra.helpers import get_inputs
    from astra.resolve import iter_analysis_nodes

    found: list[Path] = []
    # Every node, not just the root: a sub-analysis declares its own
    # inputs, and a probe denied one is a denial the researcher cannot act
    # on — the file is declared, just not at the top of the tree.
    for _scope, node in iter_analysis_nodes(spec):
        for declared in get_inputs(node):
            source = declared.get("source")
            if not isinstance(source, str) or not source:
                continue
            candidate = Path(source)
            resolved = candidate if candidate.is_absolute() else project / candidate
            if resolved.exists():
                found.append(resolved.resolve())
    return list(dict.fromkeys(found))
