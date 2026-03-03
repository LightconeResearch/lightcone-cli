"""Prism - Lightcone Research's ASTRA-compliant Agentic Layer."""

from __future__ import annotations

try:
    from importlib.metadata import version

    __version__ = version("prism")
except Exception:
    __version__ = "0.0.0.dev"
