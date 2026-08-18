"""The exec boundary: what a command may touch, and how that is enforced.

Guarantees that a command cannot use executables or files outside the
declared set wherever a mechanism exists, and what actually enforced it
is always recorded.

This includes three components. :mod:`policy` decides *what* is allowed, in
plain path sets that mention no mechanism. A :class:`~model.Backend`
turns that into an argv rewrite — Landlock through
:mod:`lightcone._sandbox_exec`, Seatbelt through ``sandbox-exec``, and
:class:`~boundary.Unavailable` through no change at all. :mod:`boundary`
picks one, runs it, and reports what it enforced.
"""

from __future__ import annotations

from lightcone.engine.sandbox.boundary import (
    Outcome,
    Unavailable,
    detect,
    run,
    scope,
)
from lightcone.engine.sandbox.model import Attestation, Backend, Capability, Policy

__all__ = [
    "Attestation",
    "Backend",
    "Capability",
    "Outcome",
    "Policy",
    "Unavailable",
    "detect",
    "run",
    "scope",
]
