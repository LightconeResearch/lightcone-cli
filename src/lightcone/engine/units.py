"""Exact byte quantities used by execution and compute."""

from __future__ import annotations

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
