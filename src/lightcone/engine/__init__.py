"""The lightcone execution engine.

Rebuild in progress: the engine is being re-added layer by layer on top
of the normative design spec (``docs/design/execution-environment.md``).
Layer 1 is project scaffolding only — :mod:`lightcone.engine.project`
(discovery) and :mod:`lightcone.engine.constants`.
"""

from __future__ import annotations


def lc_version() -> str:
    """The running lightcone-cli version ("unknown" for broken installs)."""
    try:
        from importlib.metadata import version

        return version("lightcone-cli")
    except Exception:
        return "unknown"
