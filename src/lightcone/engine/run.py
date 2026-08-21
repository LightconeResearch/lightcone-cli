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

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from lightcone.engine import container, sandbox
from lightcone.engine.project import (
    SPEC_FILENAME,
    child_env,
    require_uv,
    uv_prefix,
    uv_scrub_warning,
)


def probe(project: Path, command: Sequence[str]) -> sandbox.Outcome:
    """Run a command in the project environment, inside the boundary.

    A containerized probe never builds the image — it finds one, or
    refuses naming the exact ``lc build`` — and converges the in-image
    environment before executing, which is the same promise the direct
    probe makes through its syncing ``uv run`` hop: the environment a
    probe describes is one it just converged.

    Args:
        project: The project root.
        command: The argv to run. Required — there is deliberately no bare
            ``lc run`` shell, since an agent that opens an interactive
            shell waits forever for input nobody will type.

    Returns:
        The exit code, what the boundary enforced, and any lines the
        caller should print verbatim.
    """
    require_uv()
    spec = read_spec(project)

    runtime = container.runtime_for_run(project, build=False)
    if runtime.mode == "containerized":
        # The probe's converge. Direct mode's is the syncing hop below —
        # the deliberate exception to `container.converge`, because there
        # the hop itself is what converges.
        container.sync(project, runtime)

    built = container.policy_for(runtime, input_paths(project, spec))
    with sandbox.scope(built) as policy:
        outcome = sandbox.run(
            container.backend(runtime),
            policy,
            list(command),
            cwd=project,
            # The direct hop converges; the containerized one must not —
            # the converge above already did, into the in-image
            # environment the hop is about to enter.
            prefix=uv_prefix(project, sync=runtime.mode == "direct"),
            # Same reason as convergence: this uv invocation names its
            # project explicitly, so an environment activated elsewhere
            # is never what we mean — and uv says so, once per run, in
            # the middle of the probe's own output.
            env=child_env(),
        )
    # The probe is what called `child_env`, so the probe's outcome is
    # where the scrub's fact belongs — the caller prints notes verbatim.
    if warning := uv_scrub_warning():
        outcome = replace(outcome, notes=(warning, *outcome.notes))
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
