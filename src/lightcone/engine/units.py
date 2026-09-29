"""Exact byte quantities and explicit walltime units used by execution and compute."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def whole_bytes(amount: str, unit_bytes: int) -> int:
    """Scale a decimal memory amount into positive bytes without rounding.

    Args:
        amount: A decimal quantity in the caller's units.
        unit_bytes: The number of bytes represented by one unit.

    Raises:
        ValueError: The quantity is not positive or exactly representable in bytes.
    """
    try:
        numerator, denominator = Decimal(amount).as_integer_ratio()
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("memory must be positive and exactly representable in bytes") from exc
    result, remainder = divmod(numerator * unit_bytes, denominator)
    if result <= 0 or remainder:
        raise ValueError("memory must be positive and exactly representable in bytes")
    return result


def duration_seconds(value: object) -> int:
    """Parse a positive duration such as ``30m``, ``1h30m``, or ``45s``.

    Args:
        value: Ordered day, hour, minute, and second components.

    Raises:
        ValueError: The duration is malformed, empty, or zero.
    """
    if not isinstance(value, str) or not (
        match := re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value)
    ):
        raise ValueError("duration must use explicit units, e.g. 30m, 1h30m, or 45s")
    seconds = sum(int(part or 0) * unit for part, unit in zip(match.groups(), (86400, 3600, 60, 1)))
    if seconds <= 0:
        raise ValueError("duration must be positive")
    return seconds
