"""The sandbox/hermeticity layer (spec §7).

The boundary seam consumes exactly one name from this package:
:class:`~lightcone.engine.sandbox.exec_boundary.SandboxExecBoundary`,
the enforced :class:`~lightcone.engine.boundary.ExecBoundary`.
Everything else (policy, probe, wrap, denial) is addressed by its
submodule.
"""
from __future__ import annotations

from lightcone.engine.sandbox.exec_boundary import SandboxExecBoundary

__all__ = ["SandboxExecBoundary"]
