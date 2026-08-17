"""The launcher↔engine / driver↔worker environment-variable contract.

One home for every ``LC_*`` variable that crosses a process boundary —
the launcher's delegation, the podman run wrapper, and the per-run
sandbox flags — so producer and consumers can never drift apart. (The
sandbox *shim*'s variables live in :mod:`lightcone._sandbox_exec`,
which must stay stdlib-only; the engine imports them from there.)
"""
from __future__ import annotations

import os
from pathlib import Path

#: Set on delegation (direct exec or podman re-entry) — the launcher
#: never delegates twice. Part of the frozen delegation interface.
DELEGATED_ENV = "LC_DELEGATED"

#: "container" when the process runs inside the project image (set by
#: the podman run wrapper); how every layer answers "am I in the
#: image?".
WORKER_RUNTIME_ENV = "LC_WORKER_RUNTIME"
CONTAINER_RUNTIME_VALUE = "container"

#: The network posture the container wrapper actually applied
#: ("none" | "host") — what the hermeticity record reports.
CONTAINER_NETWORK_ENV = "LC_CONTAINER_NETWORK"

#: The driver-resolved image id, asserted by the worker's env check.
IMAGE_DIGEST_ENV = "LC_IMAGE_DIGEST"

#: Per-run sandbox flags (env, not cfg: run flags must never perturb
#: the content-addressed job identity).
NO_SANDBOX_ENV = "LC_NO_SANDBOX"
REQUIRE_SANDBOX_ENV = "LC_REQUIRE_SANDBOX"


def in_container() -> bool:
    return os.environ.get(WORKER_RUNTIME_ENV) == CONTAINER_RUNTIME_VALUE


def recipe_env_prefix(project_root: Path) -> Path:
    """The recipe environment's prefix on this process's venue: the
    baked ``/opt/venv`` inside the image, the project ``.venv``
    otherwise. The single home for that decision — sandbox policy and
    probe assembly both use it."""
    if in_container():
        from lightcone.engine.image.constants import OPT_VENV

        return Path(OPT_VENV)
    return project_root / ".venv"
