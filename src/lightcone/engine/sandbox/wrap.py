"""Assemble the wrapped recipe exec.

The boundary placement is the **exec-shim** (spec §7): the sandbox
wraps *the recipe*, not the engine. The parent builds the Landlock
ruleset FD before fork and passes it down (``pass_fds`` + env); the
shim (:mod:`lightcone._sandbox_exec`) performs exactly
``prctl(PR_SET_NO_NEW_PRIVS)`` + ``landlock_restrict_self(fd)`` and
execs bash. No ``preexec_fn`` anywhere.

The shim is launched with the *current interpreter* — ``run_rule``
already executes inside the recipe environment (the delegated engine in
direct mode, the image's ``/opt/venv`` in containerized mode), so no
``uv run`` hop is needed. (The FD-survival spike verified inheritance
holds even through uv's spawn chain; the direct exec makes it moot.)
"""
from __future__ import annotations

import sys
import tempfile

from lightcone.engine.sandbox.model import (
    SandboxCapability,
    SandboxPolicy,
    WrappedCommand,
)


def wrap_command(
    shell_command: str,
    policy: SandboxPolicy,
    capability: SandboxCapability,
) -> WrappedCommand:
    """Wrap a rule's shell command (the ``run_rule`` path)."""
    return wrap_argv(
        ("bash", "-c", shell_command),
        policy,
        capability,
        interpreter=(sys.executable,),
    )


def wrap_argv(
    recipe_argv: tuple[str, ...],
    policy: SandboxPolicy,
    capability: SandboxCapability,
    *,
    interpreter: tuple[str, ...],
) -> WrappedCommand:
    """Wrap an arbitrary argv through the shim.

    *interpreter* is how the shim's python is reached — the current
    interpreter for rules, or a ``uv run … -- python`` prefix for
    probes (uv stays trusted plumbing *outside* the boundary; the FD
    survives its spawn chain — spike-verified).
    """
    argv = (
        *interpreter,
        "-m",
        "lightcone._sandbox_exec",
        "--",
        *recipe_argv,
    )

    if capability.kind == "landlock":
        fd = _build_ruleset(policy)
        return WrappedCommand(
            argv=argv,
            pass_fds=(fd,),
            env={
                "LC_SANDBOX_MODE": "landlock",
                "LC_SANDBOX_FD": str(fd),
                **policy.env,
            },
            close_after_spawn=(fd,),
        )

    if capability.kind == "seatbelt":
        from lightcone.engine.sandbox.seatbelt import generate_profile

        with tempfile.NamedTemporaryFile(
            "w", prefix="lc-sandbox-", suffix=".sb", delete=False
        ) as f:
            f.write(generate_profile(policy))
            profile_path = f.name
        return WrappedCommand(
            argv=argv,
            pass_fds=(),
            env={
                "LC_SANDBOX_MODE": "seatbelt",
                "LC_SANDBOX_PROFILE": profile_path,
                **policy.env,
            },
        )

    return WrappedCommand(
        argv=argv,
        pass_fds=(),
        env={"LC_SANDBOX_MODE": "none", **policy.env},
    )


def _build_ruleset(policy: SandboxPolicy) -> int:
    """Build the Landlock ruleset FD from the policy (parent side)."""
    import os

    from lightcone.engine.sandbox import _landlock

    abi = _landlock.abi()
    handled = _landlock.handled_access_for(abi)
    fd = _landlock.create_ruleset(handled)
    try:
        read_bits = _landlock.READ_BITS
        write_bits = _landlock.write_bits(abi) | read_bits
        exec_bits = _landlock.ACCESS_FS_EXECUTE | read_bits
        for path in policy.read:
            _add_if_exists(fd, path, read_bits)
        for path in policy.write:
            _add_if_exists(fd, path, write_bits)
        for path in policy.execute:
            _add_if_exists(fd, path, exec_bits)
    except BaseException:
        os.close(fd)
        raise
    # The FD must survive fork+exec into the shim.
    os.set_inheritable(fd, True)
    return fd


def _add_if_exists(fd: int, path: object, access: int) -> None:
    from pathlib import Path

    from lightcone.engine.sandbox import _landlock

    p = Path(str(path))
    try:
        _landlock.add_path_rule(fd, p, access)
    except FileNotFoundError:
        # OS-baseline entries vary by distro; a vanished path grants
        # nothing, which is safe (allowlist semantics).
        pass
