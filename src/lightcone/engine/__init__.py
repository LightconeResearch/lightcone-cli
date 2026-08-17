"""The lightcone execution engine."""
from __future__ import annotations


def lc_version() -> str:
    """The running lightcone-cli version ("unknown" for broken installs)."""
    try:
        from importlib.metadata import version

        return version("lightcone-cli")
    except Exception:
        return "unknown"
