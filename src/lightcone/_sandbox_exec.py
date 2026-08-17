"""The sandbox exec shim: ``python -m lightcone._sandbox_exec -- ARGV...``

Runs *between* fork and the recipe: applies the enforcement the parent
prepared, then execs the recipe argv. Deliberately stdlib-only with
zero lightcone imports — ``lightcone`` is a namespace package (no
``__init__``), so importing this module executes nothing else, keeping
the shim's footprint inside the exec path at effectively zero and
guaranteeing it can never drag engine code inside the sandbox setup.

Contract (env, set by the parent's ``wrap_command``):

* ``LC_SANDBOX_MODE`` — ``landlock`` | ``seatbelt`` | ``none``.
* ``LC_SANDBOX_FD`` — (landlock) the inherited ruleset FD.
* ``LC_SANDBOX_PROFILE`` — (seatbelt) path to the generated SBPL file.

Exit code **97 is reserved for sandbox-setup failure** — the parent
attributes it to lc, never to the recipe, and it is the never-silent
guard: if the ruleset FD ever fails to survive to this point, the run
fails loudly instead of proceeding unsandboxed.

The Landlock constants are duplicated from
``lightcone.engine.sandbox._landlock`` on purpose (no engine imports
here); a unit test pins the parity.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

_SYS_LANDLOCK_RESTRICT_SELF = 446
_PR_SET_NO_NEW_PRIVS = 38
_SETUP_FAILURE_EXIT = 97


def _fail(message: str) -> None:
    sys.stderr.write(f"lc sandbox setup failed: {message}\n")
    sys.stderr.flush()
    raise SystemExit(_SETUP_FAILURE_EXIT)


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        _fail("no command after --")

    mode = os.environ.pop("LC_SANDBOX_MODE", None)
    if mode == "landlock":
        _restrict_landlock()
    elif mode == "seatbelt":
        _exec_seatbelt(argv)
        return  # unreachable
    elif mode != "none":
        _fail(f"unknown LC_SANDBOX_MODE {mode!r}")

    os.execvp(argv[0], argv)


def _restrict_landlock() -> None:
    fd_str = os.environ.pop("LC_SANDBOX_FD", None)
    if fd_str is None:
        _fail("LC_SANDBOX_FD not set")
    try:
        fd = int(fd_str)  # type: ignore[arg-type]
        os.fstat(fd)
    except (ValueError, OSError):
        _fail("ruleset fd did not survive to the shim")
        return
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        _fail(f"prctl(PR_SET_NO_NEW_PRIVS): errno {ctypes.get_errno()}")
    if libc.syscall(_SYS_LANDLOCK_RESTRICT_SELF, fd, 0) != 0:
        _fail(f"landlock_restrict_self: errno {ctypes.get_errno()}")
    os.close(fd)


def _exec_seatbelt(argv: list[str]) -> None:
    profile = os.environ.pop("LC_SANDBOX_PROFILE", None)
    if not profile or not os.path.isfile(profile):
        _fail(f"seatbelt profile missing: {profile!r}")
        return
    os.execv("/usr/bin/sandbox-exec", ["sandbox-exec", "-f", profile, *argv])


if __name__ == "__main__":
    main()
