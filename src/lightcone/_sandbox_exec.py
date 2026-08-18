"""The Landlock exec shim — ``python -m lightcone._sandbox_exec``.

Landlock is a *self*-restriction: a process narrows its own access rights
and can never widen them again, and the restriction is inherited by
everything it execs. There is no way to restrict *another* process, so
there is no ``sandbox-exec``-shaped command to wrap a recipe in. This
module is that missing command: it reads a policy, restricts itself,
and ``execvp``'s the real command. That is what lets the engine treat
Landlock and Seatbelt as the same thing — a pure argv rewrite (see
:mod:`lightcone.engine.sandbox`).

The policy arrives as **JSON on argv**, not as an inherited ruleset FD.
Spec §7 describes building the ruleset before fork and passing it down
with ``pass_fds``; we build it here instead, which is also what the codex
CLI does. The reason is §11's own open spike — a Landlock FD cannot be
reopened, and whether one survives ``uv run``'s spawn/exec chain was
never verified. Serializing the policy makes the question moot, and it
keeps working across a boundary an FD cannot cross (a container).

Two properties of this module are load-bearing and pinned by tests:

- **Stdlib only, and no lightcone imports.** ``lightcone`` is a PEP 420
  namespace package with no ``__init__``, so ``-m lightcone._sandbox_exec``
  executes this file and nothing else. It runs on every sandboxed exec;
  it must not drag the engine in.
- **It never proceeds unsandboxed.** Every setup failure exits
  :data:`SETUP_FAILURE_EXIT` with a distinguishable message, so a broken
  sandbox can never be mistaken for a working one (the "silent
  best-effort" trap this design exists to avoid).
"""

from __future__ import annotations

import ctypes
import errno
import functools
import json
import os
import stat
import sys

# --- the kernel interface --------------------------------------------------

# asm-generic syscall numbers — identical on x86_64 and aarch64.
SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446

_LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

# Access-right bits, grouped by the ABI that introduced them.
ACCESS_FS_EXECUTE = 1 << 0
ACCESS_FS_WRITE_FILE = 1 << 1
ACCESS_FS_READ_FILE = 1 << 2
ACCESS_FS_READ_DIR = 1 << 3
_ABI1_ALL = (1 << 13) - 1  # bits 0..12: read, write, and every make/remove
ACCESS_FS_REFER = 1 << 13  # ABI ≥ 2
ACCESS_FS_TRUNCATE = 1 << 14  # ABI ≥ 3

#: Rights the kernel accepts on a rule whose parent is a regular file.
#: Everything else is directory-only and makes ``add_rule`` fail EINVAL.
_FILE_ONLY_BITS = (
    ACCESS_FS_EXECUTE | ACCESS_FS_WRITE_FILE | ACCESS_FS_READ_FILE | ACCESS_FS_TRUNCATE
)

READ_BITS = ACCESS_FS_READ_FILE | ACCESS_FS_READ_DIR
EXEC_BITS = ACCESS_FS_EXECUTE | ACCESS_FS_READ_FILE

#: Landlock's syscall numbers are asm-generic, but the ABI is not defined
#: for every architecture; refuse rather than issue syscall 444 blind.
_SUPPORTED_ARCHES = frozenset({"x86_64", "aarch64", "arm64"})

#: Reserved exit code for a failure to *set up* the sandbox — never a
#: command's own. The engine renders it as an lc problem, not a recipe's.
SETUP_FAILURE_EXIT = 97

#: The version of the ``--policy`` document this shim understands. It is
#: an interface between two possibly different lightcone-cli versions
#: (the launcher's and the project's), so it is checked, not assumed.
POLICY_VERSION = 1


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
    ]


class _PathBeneathAttr(ctypes.Structure):
    # The kernel struct is packed (u64 followed by s32). `_layout_` is the
    # 3.14 spelling and is ignored by older ctypes, which honor `_pack_`.
    _pack_ = 1
    _layout_ = "ms"
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


@functools.cache
def _libc() -> ctypes.CDLL:
    """The already-loaded libc.

    Cached, and `CDLL(None)` rather than `find_library("c")`: this is
    called once per rule, and `find_library` forks `ldconfig -p` every
    time — 74 subprocesses for a 71-path policy, which measured at ~740 ms
    of the shim's ~780 ms. `dlopen(NULL)` reaches the libc already mapped
    into this process, needs no search, and drops `ctypes.util` (and the
    `tempfile`/`random` chain behind it) off the import path.
    """
    return ctypes.CDLL(None, use_errno=True)


def abi() -> int:
    """The Landlock ABI this kernel supports, or 0 if it supports none.

    A version query, so it is cheap and side-effect free. Every failure
    mode — kernel < 5.13, the syscall blocked by seccomp, an
    architecture we do not vouch for — answers 0 rather than raising:
    the caller's question is "can I sandbox here", and "no" is a valid
    answer to it. Recording *which* ABI answered is the caller's job
    (spec §7 puts it in the manifest).
    """
    if os.uname().machine not in _SUPPORTED_ARCHES:
        return 0
    try:
        result = _libc().syscall(
            SYS_LANDLOCK_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(
                _LANDLOCK_CREATE_RULESET_VERSION
            )
        )
    except OSError:  # pragma: no cover - no libc to load
        return 0
    return int(result) if result > 0 else 0


def handled_access(abi_level: int) -> int:
    """The rights the ruleset declares it governs, for *abi_level*.

    Unknown bits make ``landlock_create_ruleset`` fail EINVAL, so this
    only ever widens with the ABI. Widening matters in both directions:
    a right the ruleset does not *handle* is one the kernel does not
    check at all, so ABI 1 silently permits ``ftruncate`` — while a
    handled right must also be granted somewhere or nothing can use it.

    ``REFER`` is the case worth naming: when a ruleset does not handle
    it, the kernel denies *every* cross-directory rename and link, which
    is the ABI-1 EXDEV the denial classifier knows about. Handling it
    (and granting it on the write roots) is what lets a recipe rename its
    own temporary files.

    ``IOCTL_DEV`` (ABI 5) is deliberately left unhandled: ioctls on
    device nodes are outside the accidental-leakage threat model, and
    handling it would break anything opening a terminal afresh.
    """
    handled = _ABI1_ALL
    if abi_level >= 2:
        handled |= ACCESS_FS_REFER
    if abi_level >= 3:
        handled |= ACCESS_FS_TRUNCATE
    return handled


def write_bits(abi_level: int) -> int:
    """The rights a writable root is granted: everything but EXECUTE.

    Write implies read — a directory you may create files in but not
    list is not a useful grant — so the read bits are included.
    """
    return (handled_access(abi_level) & ~ACCESS_FS_EXECUTE) | READ_BITS


def create_ruleset(handled_fs: int) -> int:
    """A new, empty Landlock ruleset FD governing *handled_fs*."""
    attr = _RulesetAttr(handled_fs, 0)
    fd = _libc().syscall(
        SYS_LANDLOCK_CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint32(0),
    )
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    return int(fd)


def add_path_rule(ruleset_fd: int, path: str, access: int) -> None:
    """Grant *access* beneath *path*.

    Rights are masked down for a regular-file rule, because the kernel
    rejects directory-only rights on one. That is what makes a per-file
    EXECUTE grant work — and per-file is the point: ``/usr/bin`` holds
    both the utility allowlist and every undeclared tool on the host, so
    granting the directory would defeat the layer.
    """
    parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        # `stat.S_ISDIR`, not a bitwise test: S_IFSOCK (0o140000) and
        # S_IFBLK (0o060000) both carry the S_IFDIR bit, so a hand-rolled
        # mask calls a socket a directory, leaves directory-only rights
        # unmasked, and turns a declared unix-socket input into EINVAL —
        # surfacing as a sandbox *setup* failure rather than a grant.
        if not stat.S_ISDIR(os.fstat(parent_fd).st_mode):
            access &= _FILE_ONLY_BITS
        if not access:
            return
        attr = _PathBeneathAttr(access, parent_fd)
        result = _libc().syscall(
            SYS_LANDLOCK_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            ctypes.c_uint32(_RULE_PATH_BENEATH),
            ctypes.byref(attr),
            ctypes.c_uint32(0),
        )
        if result != 0:
            code = ctypes.get_errno()
            raise OSError(code, f"landlock_add_rule({path}): {errno.errorcode.get(code, code)}")
    finally:
        os.close(parent_fd)


def build_ruleset(policy: dict[str, object], abi_level: int) -> int:
    """A ruleset FD realizing *policy*, or raise :class:`OSError`.

    A path that has vanished between policy construction and here grants
    nothing, which is safe, so it is skipped rather than fatal.
    """
    fd = create_ruleset(handled_access(abi_level))
    try:
        for key, access in (
            ("read", READ_BITS),
            ("write", write_bits(abi_level)),
            ("execute", EXEC_BITS),
        ):
            for path in _paths(policy, key):
                try:
                    add_path_rule(fd, path, access)
                except FileNotFoundError:
                    continue
    except BaseException:
        os.close(fd)
        raise
    return fd


def restrict_self(ruleset_fd: int) -> None:
    """Apply *ruleset_fd* to this process, irreversibly.

    ``PR_SET_NO_NEW_PRIVS`` first: the kernel refuses an unprivileged
    ``landlock_restrict_self`` without it, and it is also what stops a
    setuid binary from escaping the domain.
    """
    libc = _libc()
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS) failed")
    if libc.syscall(SYS_LANDLOCK_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0)) != 0:
        raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")


# --- the entry point -------------------------------------------------------


def _paths(policy: dict[str, object], key: str) -> list[str]:
    value = policy.get(key, [])
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ValueError(f"policy field {key!r} must be a list of strings")
    return list(value)


def _fail(message: str) -> None:
    sys.stderr.write(f"lc sandbox setup failed: {message}\n")
    raise SystemExit(SETUP_FAILURE_EXIT)


def main(argv: list[str] | None = None) -> None:
    """``--policy <json> -- <command>...`` — restrict, then become it.

    Hand-parsed rather than argparse'd: this runs before every sandboxed
    exec, and everything after ``--`` belongs to the command, including
    arguments that look like our own options.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "--policy":
        _fail("usage: -m lightcone._sandbox_exec --policy <json> -- <command>...")
    raw = args[1]
    rest = args[2:]
    if not rest or rest[0] != "--":
        _fail("missing `--` before the command")
    command = rest[1:]
    if not command:
        _fail("no command after `--`")

    try:
        policy = json.loads(raw)
        if not isinstance(policy, dict):
            raise ValueError("policy must be a JSON object")
        if policy.get("version") != POLICY_VERSION:
            raise ValueError(
                f"policy version {policy.get('version')!r} — this shim speaks {POLICY_VERSION}"
            )
        abi_level = abi()
        if abi_level == 0:
            raise ValueError("landlock unavailable (kernel < 5.13, or blocked by seccomp)")
        fd = build_ruleset(policy, abi_level)
        restrict_self(fd)
        os.close(fd)
    except (ValueError, OSError) as e:
        _fail(str(e))

    try:
        os.execvp(command[0], command)
    except OSError as e:
        # Not a setup failure: we are already restricted, so this is very
        # likely the sandbox denying the exec. Report it the way a shell
        # would — 126 "cannot execute", 127 "not found" — so the denial
        # classifier sees a message and an exit code it recognizes, and
        # exit 97 keeps meaning "lc's own sandbox setup broke".
        sys.stderr.write(f"lc sandbox: {command[0]}: {e.strerror}\n")
        raise SystemExit(127 if e.errno == errno.ENOENT else 126) from e


if __name__ == "__main__":
    main()
