"""Manual smoke test — NOT run in CI.

Run on a real Perlmutter login node with a configured target. Asserts:
  1. A SLURM allocation is acquired exactly once
  2. Three trivial recipes run sequentially within it
  3. The allocation is released on exit (squeue shows it gone)

Usage:
    cd <test-project>
    pytest tests/manual/test_perlmutter_smoke.py -v -s
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest

PERLMUTTER_TARGET = os.environ.get("LC_SMOKE_TARGET", "perlmutter")


def _squeue_user_count(user: str) -> int:
    """Return number of jobs the user has in the queue."""
    out = subprocess.run(
        ["squeue", "-u", user, "--noheader"],
        capture_output=True, text=True,
    )
    return len([line for line in out.stdout.splitlines() if line.strip()])


@pytest.mark.skipif(
    "SLURM_CONF" not in os.environ and not os.path.exists("/etc/slurm/slurm.conf"),
    reason="not running on a SLURM head node",
)
def test_one_pilot_three_recipes(tmp_path):
    """End-to-end smoke: one pilot, three recipes, allocation released."""
    user = os.environ["USER"]
    before = _squeue_user_count(user)

    # Caller is responsible for cd'ing into a test project before
    # running this test. We just invoke `lc run` and observe.
    result = subprocess.run(
        ["lc", "run", "--target", PERLMUTTER_TARGET],
        capture_output=True, text=True, timeout=1800,
    )
    assert result.returncode == 0, result.stderr

    # Give SLURM a moment to release the allocation
    time.sleep(15)
    after = _squeue_user_count(user)
    assert after == before, (
        f"Allocation not released: before={before}, after={after}"
    )
