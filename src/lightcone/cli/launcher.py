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
- **It does not touch a project that has no engine of its own.** The gate
  is a ``.venv/bin/lc`` that is already there, checked *before* anything
  is converged. Any other order would let ``lc status`` in an unrelated
  uv checkout run ``uv sync --exact`` against it and uninstall someone
  else's packages on the way to reporting that lc is not installed there.

Kept to the standard library: it runs on every invocation, ahead of click,
rich and the astra stack.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Set on delegation, and the whole of what the launcher tells the engine.
#: Its presence is also what stops a second hand-over — belt to the
#: :func:`maybe_delegate` braces, since the engine we exec lives in the
#: environment whose own check would already have returned.
DELEGATED_ENV = "LC_DELEGATED"

#: Verbs that never delegate, because they are what *produces* the
#: environment a delegation would hand over to. Everything else runs from
#: the project's own engine — including ``status``, which shares its
#: classification walk with ``materialize --check`` and would otherwise be
#: the one way to make those two verbs answer differently.
TOOL_ENV_VERBS = frozenset({"init"})


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

    # One stat, and it answers both questions worth asking here: a
    # directory holding this file is a built project, and it is one that
    # carries an engine of its own. Nothing before this point touches the
    # filesystem, so `lc --help` and shell completion pay for none of it.
    root = Path.cwd()
    engine = root / ".venv" / "bin" / "lc"
    if not engine.is_file():
        return
    if Path(sys.prefix).resolve() == (root / ".venv").resolve():
        # Already inside that environment — `uv run lc …`, or a direct
        # call to the binary. Delegating would re-exec this same program
        # to reach itself. `sys.prefix` rather than `sys.executable`:
        # `.venv/bin/python` is a symlink out to the base interpreter, so
        # resolving it names the interpreter's home, never the venv.
        return

    from lightcone.engine.project import ProjectError, child_env, converge_environment

    try:
        warnings = converge_environment(root)
    except ProjectError as e:
        # Raised before click is imported, so `_EngineErrorGroup` cannot
        # render it and this has to say it plainly itself.
        raise SystemExit(f"Error: {e}") from e
    for warning in warnings:
        print(f"uv: {warning}", file=sys.stderr)

    if not engine.is_file():
        # Converging removed it: the lock no longer carries
        # `lightcone-cli`, and `--exact` prunes what the lock does not
        # name. There is no project engine to hand over to, so this is the
        # tool env's command after all — convergence already warns about
        # the missing dependency.
        return
    env = child_env()
    env[DELEGATED_ENV] = "1"
    os.execve(str(engine), ["lc", *argv], env)
