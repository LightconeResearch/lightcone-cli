"""The tool-env launcher: discover → mode-detect → scrub → converge → exec.

``lc`` is installed as a uv tool (or any ambient environment); the
*engine* that executes a project lives inside the project's own lock
("the engine is inside the experiment's lock"). The launcher bridges
the two: for verbs that operate on the recipe environment it converges
that environment and re-execs the project's own ``lc`` binary from it.

The delegation interface is **minimal and frozen**: argv passthrough
plus ``LC_DELEGATED=1``. A tool-env launcher of any version must be
able to delegate to a project-locked engine of any age — nothing else
may ever travel across this boundary.

Verbs in :data:`TOOL_ENV_VERBS` run directly in the tool env: they are
pre-lock (``init``), offline/manifest-driven (``status``, ``verify``,
``export``), or build the environment itself (``build`` — no delegable
environment exists before the image does).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from lightcone.engine import uv_env
from lightcone.engine.contract import DELEGATED_ENV
from lightcone.engine.environment import (
    EnvironmentSpec,
    Mode,
    ProjectEnvironmentError,
    load_environment,
)
from lightcone.engine.project import find_root

#: Verbs that never delegate.
TOOL_ENV_VERBS = frozenset({"init", "status", "verify", "build", "export"})


def _fail(message: str) -> None:
    sys.stderr.write(f"Error: {message}\n")
    raise SystemExit(1)


def maybe_delegate(argv: list[str]) -> None:
    """Delegate to the project-locked engine when appropriate.

    Either returns (caller proceeds with normal Click dispatch in this
    environment) or execs the project engine and never returns.
    """
    if os.environ.get(DELEGATED_ENV) == "1":
        return
    verb = next((a for a in argv if not a.startswith("-")), None)
    if verb is None or verb in TOOL_ENV_VERBS:
        return

    root = find_root()
    if root is None:
        return  # Click renders the no-project error uniformly.

    try:
        env = load_environment(root)
    except ProjectEnvironmentError as e:
        _fail(str(e))
        return  # unreachable; keeps mypy honest

    # Ambient UV_* steering could redirect which environment converges;
    # lc always passes explicit flags, and the scrub closes the rest.
    uv_env.scrub(os.environ)

    if env.mode is Mode.CONTAINERIZED:
        _delegate_containerized(root, env, argv, verb)
        return  # unreachable (exec or SystemExit)

    _converge_direct(root)

    engine = env.venv / "bin" / "lc"
    if not engine.is_file():
        _fail(
            f"{engine} does not exist after a successful sync — the "
            "project's lock must include lightcone-cli (the engine lives "
            "inside the experiment's lock): `uv add lightcone-cli`."
        )
    # Direct exec — the frozen delegation interface: argv passthrough +
    # LC_DELEGATED=1. Never a PATH fallback.
    os.execve(
        str(engine),
        ["lc", *argv],
        {**os.environ, DELEGATED_ENV: "1"},
    )


def _delegate_containerized(
    root: Path, env: EnvironmentSpec, argv: list[str], verb: str
) -> None:
    """Full-stack delegation: one ``podman run`` hosts the delegated
    engine, its dask workers, the child snakemake, and every recipe —
    all from the image's baked ``/opt/venv``. There is no host ``.venv``
    for the project at all.

    ``lc materialize`` builds a missing image (announced); ``lc run``
    never builds — it errors with the exact ``lc build`` command.
    """
    from lightcone.engine.image import (
        ImageError,
        ensure_image,
        resolve_pinned,
    )
    from lightcone.engine.image.machine import machine_preflight
    from lightcone.engine.image.mounts import (
        compute_mount_set,
        external_input_paths,
    )
    from lightcone.engine.image.runtime_podman import PodmanRuntime

    try:
        runtime = PodmanRuntime()
        if verb == "materialize":
            record = ensure_image(
                root,
                env,
                on_progress=lambda msg: print(msg, file=sys.stderr),
            )
        else:
            record = resolve_pinned(root, env)
        mounts = compute_mount_set(
            root,
            external_inputs=external_input_paths(root),
            readonly_project=(verb == "run"),
        )
        machine_preflight(mounts.sources())
    except ImageError as e:
        _fail(str(e))
        return  # unreachable

    runtime.exec_full_stack(record=record, mounts=mounts, lc_argv=argv)


def _converge_direct(root: Path) -> None:
    """``uv sync --locked --exact`` — converge once; workers then run
    with the offline overlay and never write to the environment."""
    proc = subprocess.run(
        [
            "uv", "sync", "--locked", "--exact", "--compile-bytecode",
            "--project", str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(
            "`uv sync --locked --exact` failed — the lock and "
            f"pyproject.toml disagree, or uv is unavailable:\n{proc.stderr.strip()}"
        )
