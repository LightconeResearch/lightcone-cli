"""Sandbox backends for the eval harness.

A backend is the execution substrate one trial runs inside. All backends share
the :class:`Sandbox` surface, so a harness drives any of them unchanged.

  - :class:`LocalDockerSandbox` — a local Docker container per trial. The
    counterpart of the Daytona :class:`lightcone.eval.sandbox.EvalSandbox`, for
    running the suite on a developer/CI host with Docker rather than a Daytona
    account.
"""

from __future__ import annotations

from lightcone.eval.backends.base import ExecuteResult, Sandbox
from lightcone.eval.backends.local_docker import LocalDockerSandbox

__all__ = [
    "ExecuteResult",
    "LocalDockerSandbox",
    "Sandbox",
]
