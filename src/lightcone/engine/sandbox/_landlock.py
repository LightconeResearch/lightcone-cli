"""Vendored Landlock ctypes bindings — parent side.

No external dependencies: three raw syscalls and the access-right
constants from ``linux/landlock.h``. The *parent* builds the ruleset FD
(:func:`build_ruleset_fd`); the child-side restrict step lives in
:mod:`lightcone._sandbox_exec`, which deliberately duplicates the two
constants it needs (shim-constant parity is pinned by a unit test).

Everything here is unprivileged — Landlock needs no capabilities, which
is what makes the enforcement tests runnable in any CI.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import functools
import os
import platform
from pathlib import Path

# asm-generic syscall numbers — identical on x86_64 and aarch64.
SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
_RULE_PATH_BENEATH = 1

# Access-right bits by the ABI that introduced them.
ACCESS_FS_EXECUTE = 1 << 0
ACCESS_FS_WRITE_FILE = 1 << 1
ACCESS_FS_READ_FILE = 1 << 2
ACCESS_FS_READ_DIR = 1 << 3
ACCESS_FS_REMOVE_DIR = 1 << 4
ACCESS_FS_REMOVE_FILE = 1 << 5
ACCESS_FS_MAKE_CHAR = 1 << 6
ACCESS_FS_MAKE_DIR = 1 << 7
ACCESS_FS_MAKE_REG = 1 << 8
ACCESS_FS_MAKE_SOCK = 1 << 9
ACCESS_FS_MAKE_FIFO = 1 << 10
ACCESS_FS_MAKE_BLOCK = 1 << 11
ACCESS_FS_MAKE_SYM = 1 << 12
_ABI1_ALL = (1 << 13) - 1
ACCESS_FS_REFER = 1 << 13  # ABI ≥ 2
ACCESS_FS_TRUNCATE = 1 << 14  # ABI ≥ 3
ACCESS_FS_IOCTL_DEV = 1 << 15  # ABI ≥ 5

READ_BITS = ACCESS_FS_READ_FILE | ACCESS_FS_READ_DIR
#: Rights the kernel accepts on a rule whose parent is a regular file.
_FILE_ONLY_BITS = (
    ACCESS_FS_EXECUTE
    | ACCESS_FS_WRITE_FILE
    | ACCESS_FS_READ_FILE
    | ACCESS_FS_TRUNCATE
    | ACCESS_FS_IOCTL_DEV
)

_SUPPORTED_ARCHES = {"x86_64", "aarch64", "arm64"}


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
    ]


class _PathBeneathAttr(ctypes.Structure):
    # The kernel struct is packed (u64 + s32).
    _pack_ = 1
    _layout_ = "ms"
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


@functools.cache
def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


@functools.cache
def abi() -> int:
    """The kernel's Landlock ABI level; 0 when unavailable.

    Doubles as the capability probe: a blocked syscall (old kernel, or
    a container seccomp profile that filters ``landlock_*``) reads as
    unavailable — never as an error.
    """
    if platform.machine() not in _SUPPORTED_ARCHES:
        return 0
    r = _libc().syscall(
        SYS_LANDLOCK_CREATE_RULESET, None, 0, LANDLOCK_CREATE_RULESET_VERSION
    )
    return int(r) if r > 0 else 0


def handled_access_for(abi_level: int) -> int:
    """Every fs bit this ABI can handle — never pass unknown bits
    (EINVAL); anything handled-but-not-granted is denied, which is the
    allowlist semantics."""
    bits = _ABI1_ALL
    if abi_level >= 2:
        bits |= ACCESS_FS_REFER
    if abi_level >= 3:
        bits |= ACCESS_FS_TRUNCATE
    if abi_level >= 5:
        bits |= ACCESS_FS_IOCTL_DEV
    return bits


def write_bits(abi_level: int) -> int:
    """The full write group for this ABI (a writable subtree gets
    everything: create/remove/link plus read-back).

    ABI-1 caveat, documented: without REFER, cross-directory rename or
    link into the write set is denied wholesale — the denial UX
    recognizes the resulting EXDEV.
    """
    bits = _ABI1_ALL  # includes all MAKE_*/REMOVE_* + read + write + exec? no exec
    bits &= ~ACCESS_FS_EXECUTE
    if abi_level >= 2:
        bits |= ACCESS_FS_REFER
    if abi_level >= 3:
        bits |= ACCESS_FS_TRUNCATE
    return bits


def create_ruleset(handled_fs: int) -> int:
    attr = _RulesetAttr(handled_fs, 0)
    fd = _libc().syscall(
        SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0
    )
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    return int(fd)


def add_path_rule(ruleset_fd: int, path: Path, access: int) -> None:
    """Grant *access* beneath *path*. Missing paths raise FileNotFoundError
    (callers decide skip-vs-fail); file paths get dir-only bits masked."""
    parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        if not os.fstat(parent_fd).st_mode & 0o040000:  # not a directory
            access &= _FILE_ONLY_BITS
        if not access:
            return
        attr = _PathBeneathAttr(access, parent_fd)
        r = _libc().syscall(
            SYS_LANDLOCK_ADD_RULE,
            ruleset_fd,
            _RULE_PATH_BENEATH,
            ctypes.byref(attr),
            0,
        )
        if r != 0:
            e = ctypes.get_errno()
            raise OSError(e, f"landlock_add_rule({path}): {errno.errorcode.get(e, e)}")
    finally:
        os.close(parent_fd)


def restrict_self(ruleset_fd: int) -> None:
    """Apply the ruleset to the calling process (test helper — the
    production restrict step happens in the exec shim)."""
    libc = _libc()
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS) failed")
    if libc.syscall(SYS_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0) != 0:
        raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
