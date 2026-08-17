"""Tests for the exec shim (`python -m lightcone._sandbox_exec`)."""
from __future__ import annotations

import os
import subprocess
import sys

SHIM = [sys.executable, "-m", "lightcone._sandbox_exec"]


def _run_shim(*argv: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*SHIM, "--", *argv],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


def test_mode_none_passthrough() -> None:
    """LC_SANDBOX_MODE=none keeps the argv shape uniform: straight exec."""
    r = _run_shim("echo", "shim-ok", env={"LC_SANDBOX_MODE": "none"})
    assert r.returncode == 0
    assert r.stdout.strip() == "shim-ok"


def test_missing_fd_exits_97() -> None:
    """The never-silent guard: a landlock exec whose ruleset FD did not
    survive must fail with the reserved setup exit code — never proceed
    unsandboxed."""
    r = _run_shim("true", env={"LC_SANDBOX_MODE": "landlock"})
    assert r.returncode == 97
    assert "lc sandbox setup failed" in r.stderr


def test_bad_fd_exits_97() -> None:
    r = _run_shim(
        "true", env={"LC_SANDBOX_MODE": "landlock", "LC_SANDBOX_FD": "999"}
    )
    assert r.returncode == 97
    assert "did not survive" in r.stderr


def test_unknown_mode_exits_97() -> None:
    r = _run_shim("true", env={"LC_SANDBOX_MODE": "bogus"})
    assert r.returncode == 97


def test_no_command_exits_97() -> None:
    r = subprocess.run(
        [*SHIM, "--"],
        capture_output=True,
        text=True,
        env={**os.environ, "LC_SANDBOX_MODE": "none"},
    )
    assert r.returncode == 97


def test_shim_constants_match_bindings() -> None:
    """The shim duplicates the restrict-side constants on purpose (no
    engine imports inside the exec path) — parity is pinned here."""
    from lightcone import _sandbox_exec
    from lightcone.engine.sandbox import _landlock

    assert _sandbox_exec._SYS_LANDLOCK_RESTRICT_SELF == (
        _landlock.SYS_LANDLOCK_RESTRICT_SELF
    )
    assert _sandbox_exec._PR_SET_NO_NEW_PRIVS == 38


def test_shim_scrubs_control_env() -> None:
    """The LC_SANDBOX_* control vars must not leak into the recipe."""
    r = _run_shim("env", env={"LC_SANDBOX_MODE": "none"})
    assert r.returncode == 0
    assert "LC_SANDBOX_MODE" not in r.stdout
