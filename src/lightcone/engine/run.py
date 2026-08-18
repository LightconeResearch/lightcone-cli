"""``lc run`` executes a command inside the reproducible environment.

Byte-for-byte the environment recipes will get — the same lock, the same
converged ``.venv`` — under the same sandbox. That equivalence is the
point: if a probe works, the recipe will, and if a probe
is denied, the recipe would have been.

What the boundary is for is catching a reach *outside* the project — a
tool, a library, or a data file that is on this machine and would not be
in the image. Inside the project it stays out of the way: the tree is
writable, the way a container's bind-mounted working directory is.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lightcone.engine import sandbox
from lightcone.engine.project import SPEC_FILENAME, child_env, require_uv


def probe(project: Path, command: Sequence[str]) -> sandbox.Outcome:
    """Run *command* in the project environment, inside the boundary.

    *command* is required, and there is deliberately no bare-``lc run``
    shell. A probe is run far more often by an agent than by a person,
    and an agent that opens an interactive shell waits forever for input
    nobody is going to type.
    """
    require_uv()
    spec = read_spec(project)

    with sandbox.scope(project, read_paths=input_paths(project, spec)) as policy:
        return sandbox.run(
            sandbox.detect(),
            policy,
            list(command),
            cwd=project,
            prefix=uv_prefix(project),
            # Same reason as convergence: this uv invocation names its
            # project explicitly, so an environment activated elsewhere
            # is never what we mean — and uv says so, once per run, in
            # the middle of the probe's own output.
            env=child_env(),
        )


def uv_prefix(project: Path) -> list[str]:
    """``uv run``, pinned to *project* and refusing to drift.

    ``--locked`` makes a stale lock uv's loud error rather than a silent
    relock, and ``--exact`` keeps a previously-installed extra from
    surviving into the environment being probed. Always an explicit
    ``--project``: uv's own walk-up discovery is never trusted.
    """
    return ["uv", "run", "--locked", "--exact", "--project", str(project), "--"]


def read_spec(project: Path) -> dict[str, Any]:
    """The project's ``astra.yaml``, with sub-analyses merged in if possible.

    Best-effort by design: a probe exists to debug a project, and a spec
    whose sub-analysis references are stale is exactly when someone runs
    one. A tree that will not resolve degrades to the top-level document
    rather than making the verb unusable — and no spec at all is simply
    an empty one, with no declared inputs.
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
    """The declared inputs that are filesystem paths, resolved.

    A probe's read allowlist is the union of *all* declared inputs — it
    has no output, so it has no narrower set to use. ASTRA's ``source``
    is free-form (a URI, a dotted name, a path), so the test for "is
    this a path" is whether it resolves to something that exists.
    Anything else is somebody else's input kind.
    """
    from astra.helpers import get_inputs

    found: list[Path] = []
    for declared in get_inputs(spec):
        source = declared.get("source")
        if not isinstance(source, str) or not source:
            continue
        candidate = Path(source)
        resolved = candidate if candidate.is_absolute() else project / candidate
        if resolved.exists():
            found.append(resolved.resolve())
    return found
