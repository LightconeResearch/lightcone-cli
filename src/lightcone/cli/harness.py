"""AI coding harness registry for lightcone-cli plugin installation.

Kept deliberately leaf (no imports from :mod:`lightcone.cli.commands`) to avoid
import cycles and allow use by both the CLI and the eval harness.

To add support for a new harness, add an entry to ``HARNESS_REGISTRY`` and
open a dedicated PR — one harness per PR keeps skill performance verification
tractable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HarnessConfig:
    tool_id: str
    tool_name: str
    prefix: str
    has_hooks: bool
    has_settings: bool


HARNESS_REGISTRY: dict[str, HarnessConfig] = {
    "claude": HarnessConfig(
        tool_id="claude",
        tool_name="Claude Code",
        prefix=".claude",
        has_hooks=True,
        has_settings=True,
    ),
}


def resolve_harness(tool_id: str) -> HarnessConfig:
    """Return the harness config for *tool_id*, raising ``ValueError`` if unknown."""
    try:
        return HARNESS_REGISTRY[tool_id]
    except KeyError:
        available = ", ".join(sorted(HARNESS_REGISTRY))
        raise ValueError(
            f"Unknown harness {tool_id!r}. Available: {available}"
        ) from None


def available_harnesses() -> list[str]:
    """Sorted list of registered harness IDs."""
    return sorted(HARNESS_REGISTRY)
