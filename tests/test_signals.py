"""Entry points shed an inherited signal mask before spawning anything."""

from __future__ import annotations

import signal
import subprocess
import sys

import pytest

from lightcone import _signals

_REPORT_MASK = (
    "import signal; print(sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, ())))"
)


def _blocked() -> set[signal.Signals]:
    return set(signal.pthread_sigmask(signal.SIG_BLOCK, ()))


@pytest.fixture
def sigchld_blocked():
    """Block SIGCHLD on this thread the way a Slurm step can, then restore."""
    before = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, before)


def _grandchild_mask(*, clear: bool) -> str:
    """Spawn a Python with SIGCHLD blocked; report what *its* child inherits."""
    parent = (
        "import subprocess, sys\n"
        "from lightcone._signals import clear_inherited_mask\n"
        + ("clear_inherited_mask()\n" if clear else "")
        + f"subprocess.run([sys.executable, '-c', {_REPORT_MASK!r}], check=True)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", parent],
        preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD}),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_blocked_mask_is_inherited_by_children() -> None:
    # The hazard itself: without clearing, the block reaches grandchildren.
    assert _grandchild_mask(clear=False) == f"[{int(signal.SIGCHLD)}]"


def test_clearing_stops_the_block_reaching_children() -> None:
    assert _grandchild_mask(clear=True) == "[]"


def test_cli_entry_clears_before_dispatch(sigchld_blocked, monkeypatch) -> None:
    import lightcone.cli
    import lightcone.cli.commands

    seen: list[set[signal.Signals]] = []
    monkeypatch.setattr(lightcone.cli.commands, "main", lambda: seen.append(_blocked()))
    assert signal.SIGCHLD in _blocked()
    lightcone.cli.main()
    assert seen == [set()]


class ClearedError(Exception):
    """Raised by a stand-in clear, proving an entry point reached it first."""


def _clear() -> None:
    raise ClearedError


def test_slurm_bootstrap_clears_first(monkeypatch) -> None:
    from lightcone.engine.compute import slurm_bootstrap

    monkeypatch.setattr(slurm_bootstrap, "clear_inherited_mask", _clear)
    monkeypatch.setattr(sys, "argv", ["slurm_bootstrap"])  # would fail argparse
    with pytest.raises(ClearedError):
        slurm_bootstrap.main()


def test_local_runtime_clears_first(monkeypatch) -> None:
    from lightcone.engine.compute import local_runtime

    monkeypatch.setattr(local_runtime, "clear_inherited_mask", _clear)
    with pytest.raises(ClearedError):
        local_runtime.main()


def test_helper_is_idempotent(sigchld_blocked) -> None:
    _signals.clear_inherited_mask()
    _signals.clear_inherited_mask()
    assert _blocked() == set()
