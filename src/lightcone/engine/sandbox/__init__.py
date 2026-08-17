"""The sandbox/hermeticity layer (spec §7).

Public surface consumed by the boundary seam:

* :class:`~lightcone.engine.sandbox.exec_boundary.SandboxExecBoundary`
  — the enforced :class:`~lightcone.engine.boundary.ExecBoundary`.
* :func:`~lightcone.engine.sandbox.probe.probe` /
  :func:`~lightcone.engine.sandbox.probe.status_line` — capability
  probing and the ``lc status`` header line.
* :data:`~lightcone.engine.sandbox.policy.EXEC_ALLOWLIST_VERSION` /
  :data:`~lightcone.engine.sandbox.hints.HINT_TABLE_VERSION` — the two
  versioned policy surfaces.
"""
from __future__ import annotations

from lightcone.engine.sandbox.exec_boundary import SandboxExecBoundary
from lightcone.engine.sandbox.hints import HINT_TABLE_VERSION
from lightcone.engine.sandbox.policy import EXEC_ALLOWLIST_VERSION
from lightcone.engine.sandbox.probe import status_line

__all__ = [
    "EXEC_ALLOWLIST_VERSION",
    "HINT_TABLE_VERSION",
    "SandboxExecBoundary",
    "status_line",
]
