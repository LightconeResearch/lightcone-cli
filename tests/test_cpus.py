"""Tests for `lightcone.engine.cpus` — how wide one task runs.

The knob is environment, not recipe: nothing here may change what an
output *is*, only how much of a machine making it is allowed to take.
So what these check is the mapping and its fallbacks, plus the one place
the answer leaves this module — the sandbox's thread pins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightcone.engine.cpus import task_cpus
from lightcone.engine.sandbox.policy import home_overlay


def test_first_matching_pattern_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Insertion order is the priority list — a general pattern placed
    after a specific one must not swallow it."""
    monkeypatch.setenv(
        "LC_TASK_CPUS_MAP",
        json.dumps({"two_point/xi_integration": 24, "two_point/": 4}),
    )
    assert task_cpus("/p/results/u/two_point/xi_integration") == 24
    assert task_cpus("/p/results/u/two_point/plots") == 4


@pytest.mark.parametrize(
    "value",
    ["", "not json", "[1, 2]", '{"a": "wide"}', '{"a": 0}', '{"a": -3}'],
)
def test_malformed_maps_fall_back_silently(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in a performance knob must degrade to the behaviour that
    predates it, never fail a run that would otherwise have completed."""
    monkeypatch.setenv("LC_TASK_CPUS_MAP", value)
    monkeypatch.setenv("LC_TASK_THREADS", "3")
    assert task_cpus("/p/results/u/a") == 3


def test_the_default_is_one_task_one_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """No map, no `LC_TASK_THREADS`: exactly today's answer."""
    monkeypatch.delenv("LC_TASK_CPUS_MAP", raising=False)
    monkeypatch.delenv("LC_TASK_THREADS", raising=False)
    assert task_cpus("/p/results/u/a") == 1


def test_the_width_reaches_the_thread_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reservation the driver makes and the pool the recipe's
    libraries size themselves to are one number. A task that matched
    nothing keeps the global pin."""
    monkeypatch.setenv("LC_TASK_CPUS_MAP", json.dumps({"covariance/": 8}))
    wide = home_overlay(tmp_path, tmp_path / ".venv", write_dir=Path("/p/results/u/covariance"))
    narrow = home_overlay(tmp_path, tmp_path / ".venv", write_dir=Path("/p/results/u/plots"))
    assert wide["OMP_NUM_THREADS"] == "8"
    assert wide["NUMBA_NUM_THREADS"] == "8"
    assert narrow["OMP_NUM_THREADS"] == "1"
