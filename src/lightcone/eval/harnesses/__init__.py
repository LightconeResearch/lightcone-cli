"""Harness registry — maps a harness id to its :class:`Harness` implementation.

To add a harness: create ``harnesses/<name>.py`` with a ``Harness`` subclass,
import it here, and add it to ``_REGISTRY``. The eval run config selects
harnesses by id (``harnesses: [claude, codex, pi]``).
"""

from __future__ import annotations

from lightcone.eval.harnesses.base import (
    AgentResult,
    CommandResult,
    Harness,
    SandboxLike,
)
from lightcone.eval.harnesses.claude import ClaudeHarness

_REGISTRY: dict[str, type[Harness]] = {
    ClaudeHarness.name: ClaudeHarness,
}


def get_harness(name: str) -> Harness:
    """Instantiate the harness registered under ``name``."""
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(
            f"Unknown harness {name!r}; known harnesses: {sorted(_REGISTRY)}"
        ) from None


def available_harnesses() -> list[str]:
    """Sorted list of registered harness ids."""
    return sorted(_REGISTRY)


__all__ = [
    "AgentResult",
    "ClaudeHarness",
    "CommandResult",
    "Harness",
    "SandboxLike",
    "available_harnesses",
    "get_harness",
]
