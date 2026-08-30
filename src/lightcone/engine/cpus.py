"""How many cores one task gets.

A run's tasks are not alike: a covariance integration saturates a node
for an hour, while the plot beside it is a second of matplotlib. The
scheduler's default — one task, one slot, one thread — is right for the
second and wastes a node on the first, and the only knob that existed
(``LC_TASK_THREADS``) is global: raising it for the integration starves
every cheap task of concurrency.

``LC_TASK_CPUS_MAP`` says it per task instead: a JSON object mapping a
substring of the output's path to the width that output deserves, first
match in insertion order winning. It is *environment*, never recipe —
how wide a task runs is a fact about the machine it runs on, not about
the artifact it makes, and nothing here may reach identity or the
manifest. The same width answers both halves of the question: the
driver reserves that many of the worker's ``CPU`` resource so the node
is not oversubscribed, and the sandbox pins ``OMP_NUM_THREADS`` and
friends to it so the recipe's libraries actually take the cores.

Absent, unparseable, or matching nothing, the answer is what it has
always been: ``LC_TASK_THREADS``, defaulting to 1.
"""

from __future__ import annotations

import json
import os


def task_cpus(path: str | None = None) -> int:
    """The CPU width for a task whose output lands at ``path``.

    Args:
        path: The directory an output lands in — matched as a plain
            substring, with a trailing separator, against each pattern in
            ``LC_TASK_CPUS_MAP``. ``None`` skips the map entirely.

    Returns:
        The first matching pattern's width, else the ``LC_TASK_THREADS``
        fallback (1 if that is unset or not a positive integer).
    """
    if path is not None:
        # A trailing separator, always: what is matched is a *directory*,
        # and the obvious way to write a scope is `"covariance/"` — which
        # would match nothing against the bare directory path, silently
        # and with no way to tell it apart from a genuine miss.
        haystack = path.rstrip("/") + "/"
        for pattern, width in _cpus_map().items():
            if pattern in haystack:
                return width
    return _positive_int(os.environ.get("LC_TASK_THREADS"), 1)


def _cpus_map() -> dict[str, int]:
    """Parse ``LC_TASK_CPUS_MAP``, silently dropping anything malformed.

    Silence is the point: this is a performance knob read on every task,
    and a typo in it must degrade to today's behaviour rather than fail a
    run that would otherwise have completed. Insertion order is JSON's
    own, so the map reads top-to-bottom as the priority list it is.
    """
    raw = os.environ.get("LC_TASK_CPUS_MAP")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    entries = ((k, _positive_int(v, 0)) for k, v in parsed.items())
    return {k: v for k, v in entries if isinstance(k, str) and v}


def _positive_int(value: object, default: int) -> int:
    """``value`` as a positive int, or ``default``."""
    try:
        parsed = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
