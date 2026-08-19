"""Converge the project's environment, then hand the command over to it.

``lc`` is installed once, as a uv tool, while the *engine* that executes a
project lives inside that project's own lock — ``lc init`` pins
``lightcone-cli`` into its dependencies precisely so the engine that
produced a result stays recoverable from the commit that recorded it.
Those are two different programs, and until now nothing made them meet:
the driver, the graph, ``astra.resolve`` and every classification ran from
whichever ``lc`` happened to be on ``PATH``, while each manifest recorded
the lock's environment beside them. A project pinning one version could be
materialized by another, and nothing said so.

So before click sees an argument, this module converges the project's
environment and ``exec``s the ``lc`` inside it. What crosses that boundary
is deliberately minimal — the argv verbatim, plus ``LC_DELEGATED=1`` —
because a launcher of any version has to be able to hand over to a project
engine of any age, and anything richer is something the two would have to
agree about.

Three things it does not do:

- **It does not discover the project.** The directory you invoke from is
  the project, or nothing is delegated. There is no walk-up.
- **It does not report that a directory is not a project.** It simply
  declines to delegate, and the engine's own message says why — one
  error, written once.
- **It does not fall back.** A converged environment with no ``lc`` in it
  is a loud failure, because continuing in the tool env is exactly the
  silent version skew this module exists to remove.

Kept to the standard library: it runs on every invocation, ahead of click,
rich and the astra stack.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Set on delegation, and the whole of what the launcher tells the engine.
#: Its presence is also what stops a second hand-over: the engine we exec
#: runs this same code on the way in.
DELEGATED_ENV = "LC_DELEGATED"

#: Verbs that never delegate, because they are what *produces* the
#: environment a delegation would hand over to. Everything else runs from
#: the project's own engine — including ``status``, which shares its
#: classification walk with ``materialize --check`` and would otherwise be
#: the one way to make those two verbs answer differently.
TOOL_ENV_VERBS = frozenset({"init"})

#: What has to be there before delegating is even a question. Not
#: ``.venv``: converging is this module's job, so a project that has never
#: been synced is one to build, not one to refuse.
_PROJECT_FILES = ("pyproject.toml", "uv.lock")


def maybe_delegate(argv: list[str]) -> None:
    """Hand *argv* to the project's own engine, or return and let click run.

    Args:
        argv: The command line, without the program name.

    Returns:
        Nothing — and only when the caller should carry on in this
        environment. Otherwise the process is replaced.
    """
    if os.environ.get(DELEGATED_ENV):
        return
    verb = next((arg for arg in argv if not arg.startswith("-")), None)
    if verb is None or verb in TOOL_ENV_VERBS:
        return

    root = Path.cwd()
    if not all((root / name).is_file() for name in _PROJECT_FILES):
        return
    engine = root / ".venv" / "bin" / "lc"
    if Path(sys.executable).resolve().parent == engine.parent.resolve():
        # Already the project's engine — `uv run lc …`, or a direct call
        # to the binary in its `.venv`. Delegating would re-exec this same
        # program to reach itself, and re-converge what uv just converged.
        return

    from lightcone.engine.project import ProjectError, child_env, sync

    try:
        warnings = sync(root)
    except ProjectError as e:
        # Raised before click is imported, so `_EngineErrorGroup` cannot
        # render it and this has to say it plainly itself.
        raise SystemExit(f"Error: {e}") from e
    for warning in warnings:
        print(f"uv: {warning}", file=sys.stderr)

    if not engine.is_file():
        raise SystemExit(
            f"Error: {engine} does not exist after a successful sync — the "
            "engine lives inside the experiment's lock, so the project has "
            "to depend on it: `uv add lightcone-cli`."
        )
    os.execve(str(engine), ["lc", *argv], {**child_env(), DELEGATED_ENV: "1"})
