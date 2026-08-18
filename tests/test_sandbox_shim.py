"""Tests for `lightcone._sandbox_exec` — the Landlock exec shim.

Run as a real subprocess, because that is the only way the properties
under test are real: the shim's contract is its argv, its exit codes,
and the fact that `python -m lightcone._sandbox_exec` pulls in nothing
but the stdlib.

Only the paths that need no kernel support live here; enforcement itself
is `test_sandbox_landlock.py`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lightcone import _sandbox_exec

SHIM = [sys.executable, "-m", "lightcone._sandbox_exec"]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*SHIM, *args], capture_output=True, text=True, check=False)


def _document(**overrides: object) -> str:
    return json.dumps(dict(overrides))


# ---- never proceed unsandboxed --------------------------------------------


@pytest.mark.parametrize(
    ("args", "because"),
    [
        ((), "no arguments at all"),
        (("--policy",), "a --policy with no document"),
        (("--policy", _document(), "true"), "no `--` before the command"),
        (("--policy", _document(), "--"), "nothing after the `--`"),
        (("--policy", "{not json", "--", "true"), "a malformed document"),
        (("--policy", "[]", "--", "true"), "a document that is not an object"),
        (("--policy", _document(read="/usr"), "--", "true"), "a field that is not a list"),
    ],
)
def test_setup_failures_use_the_reserved_exit_code(args: tuple[str, ...], because: str) -> None:
    """A sandbox that cannot be set up must never fall through to running
    the command anyway — the failure mode this whole layer exists to make
    impossible. 97 is reserved so it is distinguishable from any exit a
    command could produce itself."""
    result = _run(*args)
    assert result.returncode == _sandbox_exec.SETUP_FAILURE_EXIT, because
    assert "lc sandbox setup failed" in result.stderr, because


def test_the_command_never_runs_when_setup_fails(tmp_path: Path) -> None:
    canary = tmp_path / "canary"
    _run("--policy", "{bad", "--", "touch", str(canary))
    assert not canary.exists()


# ---- the module stays alone -----------------------------------------------


def test_the_shim_imports_nothing_from_lightcone() -> None:
    """`lightcone` is a namespace package with no `__init__`, so `-m
    lightcone._sandbox_exec` executes this module and nothing else. The
    shim runs on every sandboxed exec; dragging the engine in would put
    click, rich, and the astra stack on that path."""
    probe = (
        "import sys, lightcone._sandbox_exec;"
        "print([m for m in sys.modules if m.startswith('lightcone')])"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert sorted(json.loads(result.stdout.replace("'", '"'))) == [
        "lightcone",
        "lightcone._sandbox_exec",
    ]


def test_the_shim_uses_only_the_standard_library() -> None:
    """Nothing third-party may sit between `lc` and an exec."""
    source = Path(_sandbox_exec.__file__).read_text()
    for line in source.splitlines():
        if line.startswith(("import ", "from ")) and "__future__" not in line:
            module = line.split()[1].split(".")[0]
            assert module in sys.stdlib_module_names, line


# ---- the ABI ladder -------------------------------------------------------


def test_handled_rights_only_widen_with_the_abi() -> None:
    """Unknown bits make `landlock_create_ruleset` fail EINVAL, so the
    mask is built up rather than assumed."""
    for lower, higher in ((1, 2), (2, 3), (3, 4)):
        assert (
            _sandbox_exec.handled_access(lower) & _sandbox_exec.handled_access(higher)
            == _sandbox_exec.handled_access(lower)
        )


def test_refer_is_handled_from_abi_2() -> None:
    """A ruleset that does not *handle* REFER makes the kernel deny every
    cross-directory rename — the ABI-1 EXDEV the denial classifier knows
    about. Handling it is what lets a recipe rename its own temp files."""
    assert not _sandbox_exec.handled_access(1) & _sandbox_exec.ACCESS_FS_REFER
    assert _sandbox_exec.handled_access(2) & _sandbox_exec.ACCESS_FS_REFER


def test_a_writable_root_never_gets_execute() -> None:
    """Otherwise a recipe could write a binary into its own scratch and
    run it, which is the exec allowlist defeated in two lines."""
    for abi in (1, 2, 3, 4):
        assert not _sandbox_exec.write_bits(abi) & _sandbox_exec.ACCESS_FS_EXECUTE


def test_write_implies_read() -> None:
    """A directory you may create files in but cannot list is not a
    useful grant."""
    assert _sandbox_exec.write_bits(1) & _sandbox_exec.READ_BITS == _sandbox_exec.READ_BITS


def test_abi_is_zero_or_a_real_version() -> None:
    """Every failure — old kernel, seccomp, unsupported arch — answers 0
    rather than raising: "can I sandbox here" has "no" as a valid answer."""
    assert _sandbox_exec.abi() >= 0
