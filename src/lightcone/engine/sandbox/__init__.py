"""The exec boundary: what a command may touch, and how that is enforced.

The guarantee (spec §7, G6): a command cannot use executables or files
outside the declared set wherever a mechanism exists, and what actually
enforced it is always recorded.

The layer is three pieces. :mod:`policy` decides *what* is allowed, in
plain path sets that mention no mechanism. A :class:`~model.Backend`
turns that into an argv rewrite — Landlock through
:mod:`lightcone._sandbox_exec`, Seatbelt through ``sandbox-exec``, and
:class:`~boundary.Unavailable` through no change at all. :mod:`boundary`
picks one, runs it, and reports what it enforced.

The threat model is stated, not implied: this enforces
declared-dependency *discipline* against accidental leakage. It is not a
boundary against a hostile command — metadata stays visible, an
interpreter can still read a script it was handed, and memfd-exec is
unaddressed. All three are adversarial-only.
"""

from __future__ import annotations

from lightcone.engine.sandbox.boundary import (
    DISABLED,
    Outcome,
    Unavailable,
    detect,
    run,
    scope,
)
from lightcone.engine.sandbox.model import Attestation, Backend, Capability, Policy

__all__ = [
    "DISABLED",
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
