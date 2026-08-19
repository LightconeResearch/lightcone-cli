"""Building the policy a sandboxed command runs under.

One policy, both mechanisms. This module decides *what* a command may
touch; :mod:`~lightcone.engine.sandbox.landlock` and
:mod:`~lightcone.engine.sandbox.seatbelt` only decide how to say it.

The shape it encodes is what a container gives the command, minus the
container: the project and the declared inputs and the OS baseline
readable, ``results/`` and a private scratch scope writable, and only
the project's own environment plus a versioned utility allowlist
runnable. What it catches is a command reaching *outside* that set — a
tool, a library, or a data file that is on this machine and would not be
in the image.

A read-only tree with one writable directory inside it is the shape all
three mechanisms express *natively*, which is why it is the shape:
Landlock unions rights over ancestors, so a nested grant only ever
widens; SBPL restates the write tier after the guard; and podman mounts
the project ``:ro`` with ``results`` ``:rw`` over it. The reverse — a
writable tree with a read-only hole in it — needs rights *subtraction*,
which Landlock cannot do at all.
"""

from __future__ import annotations

import functools
import os
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from fnmatch import fnmatch
from pathlib import Path

from lightcone.engine.sandbox.model import Policy

#: The utility tier of the exec allowlist. A maintained policy
#: surface, versioned by ``EXEC_ALLOWLIST_VERSION`` — recipes routinely
#: shell out to these, and none of them is a scientific dependency in
#: disguise, so admitting them costs nothing the design cares about.
EXEC_ALLOWLIST: tuple[str, ...] = (
    "sh", "bash", "env",
    "grep", "egrep", "fgrep", "sed", "awk", "gawk", "mawk",
    "tar", "gzip", "gunzip", "zcat", "bzip2", "xz",
    "cat", "head", "tail", "ls", "cp", "mv", "rm", "mkdir", "rmdir", "ln",
    "chmod", "touch", "date", "sort", "uniq", "cut", "tr", "wc", "tee",
    "find", "xargs", "mktemp", "readlink", "realpath", "dirname", "basename",
    "echo", "printf", "sleep", "true", "false", "test",
)  # fmt: skip

#: Where the allowlist is resolved from — deliberately *not* the ambient
#: ``$PATH``. A user's PATH may front `/usr/bin` with a directory full of
#: undeclared tools, and resolving through it would quietly admit them.
#: NixOS keeps none of the allowlist under FHS paths, so its system
#: profile is listed too or nothing but `/bin/sh` ever resolves there.
_UTILITY_PATH = "/usr/local/bin:/usr/bin:/bin:/run/current-system/sw/bin"


def utility(name: str) -> Path | None:
    """Where *name* resolves from the fixed search path, if it is there.

    The **only** way anything works out where an allowlisted tool lives.
    Anywhere that hardcodes a path instead is a second answer to the same
    question, and the two disagree the moment a host keeps its copy
    somewhere else: the exec set would grant one file and the caller
    would run the other, which is a denial on every single run.
    """
    found = shutil.which(name, path=_UTILITY_PATH)
    return Path(found) if found else None

#: Readable everywhere: the OS the interpreter and its libraries live in.
#: Read, never execute — being able to *read* /usr is what lets the
#: dynamic linker work; being able to *run* what is in it is the leak.
#:
#: System-level paths only, deliberately: this list must never widen to
#: reach user data, which is what the project and declared-input grants
#: are for. `/nix/store` and `/run/current-system/sw` are here because on
#: NixOS *everything* — interpreter, libraries, the utility allowlist —
#: resolves into them, so without them the sandbox is unusable there
#: rather than merely incomplete. (Both entries are taken from codex's
#: own `LINUX_PLATFORM_DEFAULT_READ_ROOTS`.)
#:
#: `/run` is granted whole, where codex names only `/run/current-system/sw`.
#: It reaches `/run/user/$UID` — sockets, dconf, portal state — and that
#: is fine here: the test is whether undeclared *inputs* arrive through a
#: path, and runtime sockets are not a channel a recipe accidentally
#: reads data from. `/etc/resolv.conf` is a symlink into `/run` wherever
#: systemd-resolved is in use, so the grant also keeps DNS working.
_OS_READ_BASELINE = (
    "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/opt", "/run",
    "/nix/store", "/run/current-system/sw",
    "/dev/urandom", "/dev/random",
)  # fmt: skip

#: Writable everywhere: the scratch surfaces and device nodes a command
#: legitimately uses.
#:
#: The `/dev` entries mirror what bubblewrap's `--dev` primitive
#: materializes — `null, zero, full, random, urandom, tty`, plus a devpts
#: mount and `ptmx`. Landlock has no device-tree primitive, so where bwrap
#: gets them from one flag we enumerate them. `/dev/tty` is what lets
#: anything open the controlling terminal afresh (`lc run`'s own shell
#: included); `/dev/pts` and `/dev/ptmx` are what let a command allocate a
#: *new* pty, which pexpect, pytest's capture, and any subprocess wanting
#: a terminal all do.
#:
#: Granting the whole devpts directory is deliberate. The threat model is
#: accidental leakage, not a hostile recipe, and terminals are
#: not a channel undeclared *inputs* arrive through. It is also less
#: permissive than it reads: Landlock only ever removes access, never adds
#: it, so ordinary Unix permissions still apply — devpts gives each pty to
#: its allocating user at mode 0620.
#:
#: `/dev/zero` and `/dev/full` are here rather than in the read baseline
#: because writes to them are discard-by-construction — that is what the
#: devices *are* — so read-only buys nothing and costs the tools that use
#: `/dev/full` to exercise ENOSPC handling. Only the entropy sources stay
#: read-only, since writing to those seeds the host's pool.
#:
#: `/proc` and `/sys` are unrestricted for the same reason. Real tools do
#: write them — `/proc/self/oom_score_adj`, `coredump_filter`, MPI and
#: CUDA runtimes poking `/sys` — and none of that is a channel undeclared
#: *inputs* arrive through. Landlock only ever removes access, so the
#: kernel's own permissions are still the real gate here: almost all of
#: both trees is root-owned, and this simply stops lc adding a second,
#: more confusing denial on top of the one the OS already enforces.
_WRITE_BASELINE = (
    "/tmp", "/var/tmp", "/dev/shm", "/proc", "/sys",
    "/dev/null", "/dev/zero", "/dev/full", "/dev/tty", "/dev/pts", "/dev/ptmx",
)  # fmt: skip

#: The ELF interpreter. Landlock checks EXECUTE on the *loader's* open,
#: so without these every dynamically linked binary — bash and python
#: included — fails EACCES and the sandbox is unusable. Globbed rather
#: than hardcoded: the path differs
#: across glibc/musl and architectures.
_ELF_LOADER_GLOBS = (
    "/lib64/ld-linux-*.so.*",
    "/lib/ld-linux*.so.*",
    "/lib/ld-musl-*.so.*",
    "/usr/lib/ld-linux*.so.*",
    "/usr/lib64/ld-linux-*.so.*",
)

#: Prefixes shared with the rest of the host. An interpreter installed
#: into one of these does not bring its own tree with it, so only the
#: binary itself may be granted EXECUTE — granting the prefix would make
#: every tool on the machine runnable, since Landlock unions rights over
#: ancestors. Anything else — a uv-managed store, a framework version
#: directory, a Homebrew Cellar — *is* the interpreter's own tree.
_SHARED_PREFIXES = frozenset(
    {"/", "/usr", "/usr/local", "/opt", "/opt/homebrew", "/opt/local", "/System", "/Library"}
)

#: The redirected environment, as ``variable -> subdirectory of HOME``.
#: One mapping, so the directories that get created and the variables
#: that point at them cannot drift apart.
_HOME_LAYOUT = {
    "XDG_CONFIG_HOME": ".config",
    "XDG_CACHE_HOME": ".cache",
    "XDG_DATA_HOME": ".local/share",
    "MPLCONFIGDIR": ".mplconfig",
    "PYTHONPYCACHEPREFIX": ".pycache",
    "TMPDIR": ".tmp",
}


def exec_policy(project: Path, *, read_paths: Sequence[Path] = ()) -> Policy:
    """What a sandboxed command may touch — one policy, every caller.

    The tree is read-only except for ``results/``, which is where output
    goes. That keeps the environment itself — ``.venv``, ``uv.lock``, the
    spec — exactly as the lock describes it for the whole run, and it is
    what makes ``lc run`` a real rehearsal: a probe and a recipe get the
    *same* scope, so a command that works under one works under the other,
    with nothing to reason about in between.

    A recipe is deliberately **not** narrowed to its own output directory.
    That would be a second answer to "are these bytes what produced them",
    and the manifest's ``data_version`` is the first — content-addressed,
    checked by ``lc verify``, and the only one that survives a rebuild on
    another machine. Two mechanisms for one guarantee is one more than can
    be kept honest. (The residue, stated: a cross-write that lands *before*
    the victim hashes leaves a manifest that is self-consistent and wrong,
    which no checksum can see. It needs concurrent tasks and a hardcoded
    sibling path; the threat model here is accidental leakage, not a
    hostile recipe.)

    ``results/`` is granted only if it is already there. Convergence
    creates it, and a policy that made directories would be a side effect
    nobody asked for — a policy describes, it does not prepare.

    Creates the per-run HOME on disk as a side effect; the caller owns
    removing it (see :func:`~lightcone.engine.sandbox.boundary.scope`).
    """
    tmp_home = Path(tempfile.mkdtemp(prefix="lc-home-")).resolve()
    for sub in _HOME_LAYOUT.values():
        (tmp_home / sub).mkdir(parents=True, exist_ok=True)

    python = _venv_python(project)
    # EXECUTE on the interpreter *file*; READ on the install root beside
    # it, for the stdlib. See :func:`_venv_python` and :func:`_stdlib_root`.
    stdlib = _stdlib_root(python)
    write = _existing([tmp_home, project / "results", *_write_roots(project)])
    read = _existing([project, *read_paths, *stdlib, *(Path(p) for p in _OS_READ_BASELINE)])

    return Policy(
        read=read,
        write=write,
        execute=_existing(_exec_set(project, python)),
        tmp_home=tmp_home,
        env=home_overlay(tmp_home, project),
    )


def _write_roots(project: Path) -> list[Path]:
    """The write baseline, minus any root that would swallow the project.

    ``/tmp`` is writable by design — but a project that *lives* under it
    would then be writable too, silently voiding the read-only tree for
    exactly the people who keep scratch analyses in ``/tmp``. Dropping
    the offending root is safe because ``TMPDIR`` points at the private
    scope regardless, so ``tempfile`` keeps working either way. The
    device entries can never contain a project, so the filter is a no-op
    for them.
    """
    resolved = project.resolve()
    roots = tuple(Path(root).resolve() for root in _WRITE_BASELINE)
    return [root for root in roots if not resolved.is_relative_to(root)]


def home_overlay(tmp_home: Path, project: Path) -> dict[str, str]:
    """HOME and friends, pointed at a fresh directory.

    The real ``$HOME`` is neither readable nor writable inside the
    boundary. Left alone, that breaks matplotlib, astropy, and R on first
    import — and the obvious patch, mounting ``$HOME`` read-only, would
    reopen the dotfile-steering channel the whole layer exists to close.
    Giving them a private HOME instead is the Bazel/nix move: they work,
    and they cannot be steered.

    ``PATH`` is set for a different reason, but the same one at heart:
    what the command resolves has to be what the policy granted.

    ``PYTHONPYCACHEPREFIX`` is here for the same reason at the other end:
    the tree is read-only apart from ``results/``, so without it every
    ``import`` of an in-tree module fails trying to write its
    ``__pycache__``. ``TMPDIR`` points inside too, so ``tempfile`` works
    even where the shared ``/tmp`` had to leave the write set.
    """
    return {
        "HOME": str(tmp_home),
        # The search path *is* the exec set. Without this the command
        # resolves tools through the host's ambient PATH while the policy
        # granted whatever `_UTILITY_PATH` resolved — so on a machine
        # whose PATH fronts another copy (homebrew's bash on macOS, say)
        # the sandbox denies `bash` itself, and the message blames the
        # user's command for lc's own incoherence.
        "PATH": os.pathsep.join([str(project / ".venv" / "bin"), _UTILITY_PATH]),
        **{k: str(tmp_home / v) for k, v in _HOME_LAYOUT.items()},
    }


def _venv_python(project: Path) -> Path | None:
    """The realpath of the venv's interpreter, if there is one.

    Resolved, because ``.venv/bin/python`` is a symlink and Landlock
    evaluates the target. What gets granted on it is
    :func:`_exec_set`'s decision, and its install root is separately a
    read root (:func:`exec_policy`) for the standard library beside it.
    """
    python = project / ".venv" / "bin" / "python"
    return python.resolve() if python.exists() else None


def _stdlib_root(python: Path | None) -> list[Path]:
    """The install root to grant READ on, for the standard library.

    The stdlib sits beside the interpreter, outside the project and
    outside `/usr` for a managed build, so without this grant the child
    dies with ``Failed to import encodings module``.

    Refused when the root is ``$HOME`` or an ancestor of it, which is
    what an interpreter installed straight into ``~/bin`` produces. That
    grant would make the real home readable and silently undo the
    private-``$HOME`` design — the one thing the environment overlay
    exists to guarantee. Failing loudly on a layout nobody uses beats
    voiding the guarantee for the people who do. (Reading the base
    prefix out of ``pyvenv.cfg`` instead would not help: it reports the
    same directory.)
    """
    if python is None:
        return []
    root = python.parent.parent
    return [] if Path.home().resolve().is_relative_to(root) else [root]


def _exec_set(project: Path, python: Path | None) -> list[Path]:
    """The two exec tiers: the environment, and the utility allowlist.

    Grants are per *file* for the utilities, never per directory:
    ``/usr/bin`` holds ``bash`` and ``latex`` alike, so a directory grant
    there would admit every undeclared tool on the host and leave the
    layer enforcing nothing.

    The interpreter is the one place that judgement is not enough, and
    the rule is narrower than either extreme. Its **own tree** is granted
    — a uv-managed store, a framework version directory, a Homebrew
    Cellar — because a framework build does not exec the binary on PATH
    at all: it re-execs itself into
    ``Resources/Python.app/Contents/MacOS/Python``, and a grant on the
    launcher alone leaves it unable to start. A **shared** prefix is not
    granted (:data:`_SHARED_PREFIXES`): a venv built against the system
    python roots at ``/usr``, and since Landlock unions rights over
    ancestors, that single grant would make every binary on the host
    runnable and silently outrank this whole allowlist.
    """
    paths: list[Path] = []
    bin_dir = project / ".venv" / "bin"
    if bin_dir.is_dir():
        # Per *file*, never the directory, for the same reason `/usr/bin`
        # is: a directory grant is a grant on whatever the directory holds
        # *later*. The read-only tree means nothing a run does can put a
        # binary here, so this is belt-and-braces today — but it was a live
        # hole for as long as the tree was writable (`cp /usr/bin/git
        # .venv/bin/` ran a tool the allowlist denies by name), and
        # enumerating costs one scandir.
        paths.extend(
            entry.resolve() for entry in bin_dir.iterdir() if os.access(entry, os.X_OK)
        )
    if python is not None:
        paths.append(python)
        # macOS framework builds `posix_spawn` themselves into
        # `Resources/Python.app/Contents/MacOS/Python`, a *different*
        # file from the one on PATH — so granting the launcher alone
        # leaves the interpreter unable to start itself.
        install_root = python.parent.parent
        if str(install_root) not in _SHARED_PREFIXES:
            paths.append(install_root)
    for name in EXEC_ALLOWLIST:
        found = utility(name)
        if found is not None:
            paths.append(found)
    paths.extend(elf_loaders())
    return paths


@functools.cache
def elf_loaders() -> tuple[Path, ...]:
    """The dynamic loaders present on this host, realpath'd.

    Scans each distinct directory once rather than globbing five
    patterns: on a merged-``/usr`` system all five resolve to the same
    directory, and `glob` re-lists and re-matches its ~8000 entries per
    pattern — which measured as 95% of the whole policy build. Cached
    because the answer cannot change while the process runs.
    """
    found: set[Path] = set()
    for directory, patterns in _loader_patterns().items():
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            # Cheap prefix reject before fnmatch: almost nothing in a
            # library directory starts with `ld-`.
            if entry.name.startswith("ld-") and any(
                fnmatch(entry.name, pattern) for pattern in patterns
            ):
                found.add(Path(entry.path).resolve())
    return tuple(sorted(found))


def _loader_patterns() -> dict[str, set[str]]:
    """The loader globs, grouped by the real directory they name."""
    grouped: dict[str, set[str]] = {}
    for pattern in _ELF_LOADER_GLOBS:
        directory, _, name = pattern.rpartition("/")
        grouped.setdefault(os.path.realpath(directory), set()).add(name)
    return grouped


def _existing(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Resolve, drop what is not there, de-duplicate, keep order.

    A rule for a path that does not exist cannot be added, and a
    baseline entry missing on this OS is normal rather than an error.
    """
    resolved: dict[Path, None] = {}
    for path in paths:
        candidate = Path(path).resolve()
        if candidate.exists():
            resolved.setdefault(candidate, None)
    return tuple(resolved)
