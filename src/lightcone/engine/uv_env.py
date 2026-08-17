"""The uv environment-variable contract.

Two closed lists (spec §4, §6):

* :data:`SCRUB_LIST` — ambient ``UV_*`` variables the launcher unsets
  before any uv invocation. Ambient uv steering could silently redirect
  the environment lc converges (a different project env, a different
  index, a different interpreter). Explicit flags beat ambient
  variables in uv (verified, §13), so lc's always-pass-explicit-flags
  posture makes the scrub defense-in-depth — but scrubbing keeps the
  posture honest even for settings lc has no flag for.

* :data:`OFFLINE_OVERLAY` — applied to every worker/recipe exec after
  convergence: converge once, then never write to the environment.

Audited against uv 0.12 (the project's pinned engine version).
"""
from __future__ import annotations

from collections.abc import MutableMapping

SCRUB_LIST: tuple[str, ...] = (
    "UV_CACHE_DIR",
    "UV_CONFIG_FILE",
    "UV_EXTRA_INDEX_URL",
    "UV_FIND_LINKS",
    "UV_FROZEN",
    "UV_INDEX_URL",
    "UV_LINK_MODE",
    "UV_LOCKED",
    "UV_NO_CACHE",
    "UV_NO_CONFIG",
    "UV_NO_SYNC",
    "UV_OFFLINE",
    "UV_PROJECT",
    "UV_PROJECT_ENVIRONMENT",
    "UV_PYTHON",
    "UV_PYTHON_DOWNLOADS",
    "UV_PYTHON_INSTALL_DIR",
)

#: Converge once, then never write: applied to recipe/worker execs so a
#: mid-run ``uv run`` can neither hit the network nor fetch an
#: interpreter.
OFFLINE_OVERLAY: dict[str, str] = {
    "UV_OFFLINE": "1",
    "UV_PYTHON_DOWNLOADS": "never",
}


def scrub(env: MutableMapping[str, str]) -> None:
    """Remove every scrub-listed variable from *env*, in place."""
    for name in SCRUB_LIST:
        env.pop(name, None)
