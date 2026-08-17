"""The default filesystem policy (spec §7) — one policy, both modes.

* **write**: the rule's own output dir + the run scratch + ``/tmp`` +
  ``/dev/shm`` + ``/dev/null`` + a fresh per-recipe HOME. The
  per-output ``writable-project`` escalation adds the project tree and
  downgrades the recorded scope to ``project-rw``. Probes (no output
  dir) get the tmp scope only — never in-tree.
* **read**: the project tree, declared inputs, and the OS baseline.
* **execute** — two tiers plus the loader: the env's ``bin`` directory,
  an enumerated *versioned* utility allowlist, and the realpath'd ELF
  loaders — Landlock checks EXECUTE on the interpreter's open of the
  loader, so without it every dynamically linked binary (python and
  bash included) fails EACCES. Shared libraries need only the read
  baseline.
* **HOME/XDG contract**: HOME, ``XDG_{CONFIG,CACHE,DATA}_HOME``,
  ``MPLCONFIGDIR``, and ``PYTHONPYCACHEPREFIX`` point at a fresh
  per-recipe directory under the writable tmp scope. The real ``$HOME``
  is simply *not granted* — never "fix" a HOME failure by granting it.
  (``PYTHONPYCACHEPREFIX`` redirects in-tree bytecode caches to the tmp
  scope, eliminating the read-only-tree first-run slowdown without
  widening any grant.)
"""
from __future__ import annotations

import glob
import shutil
import tempfile
from pathlib import Path

from lightcone.engine.boundary import ExecScope
from lightcone.engine.sandbox.model import SandboxPolicy

#: Version of the exec allowlist below — recorded in every manifest so
#: an audit can reconstruct exactly what a recipe was allowed to run.
EXEC_ALLOWLIST_VERSION = 1

#: v1: shells, the classic text/stream tools, archivers, and a curated
#: coreutils subset. A maintained policy surface — extend by bumping
#: the version, never silently.
EXEC_ALLOWLIST_V1: tuple[str, ...] = (
    "sh", "bash", "env",
    "grep", "sed", "awk", "gawk", "mawk",
    "tar", "gzip", "gunzip", "zcat",
    "cat", "head", "tail", "ls", "cp", "mv", "rm", "mkdir", "rmdir",
    "ln", "chmod", "touch", "date", "sort", "uniq", "cut", "tr", "wc",
    "tee", "mktemp", "readlink", "realpath", "dirname", "basename",
    "echo", "printf", "sleep", "true", "false",
)

_UTILITY_PATH = "/usr/local/bin:/usr/bin:/bin"

#: The OS read baseline: interpreters' shared libraries, config, locale
#: and SSL data, /proc self-inspection, entropy. Missing entries are
#: skipped (distro variance), declared inputs are not.
_OS_READ_BASELINE = (
    "/usr", "/lib", "/lib64", "/etc", "/proc", "/sys", "/opt",
    "/dev/urandom", "/dev/random", "/dev/zero", "/run",
)

_ELF_LOADER_GLOBS = (
    "/lib64/ld-linux-*.so.*",
    "/lib/ld-linux*.so.*",
    "/lib/ld-musl-*.so.*",
    "/usr/lib/ld-linux*.so.*",
)


def _elf_loaders() -> tuple[Path, ...]:
    found: set[Path] = set()
    for pattern in _ELF_LOADER_GLOBS:
        for hit in glob.glob(pattern):
            found.add(Path(hit).resolve())
    return tuple(sorted(found))


def build_policy(
    scope: ExecScope,
    *,
    env_prefix: Path,
    scratch_dirs: tuple[Path, ...] = (),
) -> SandboxPolicy:
    """Realize the §7 policy for one exec.

    *env_prefix* is the recipe environment's prefix (``<project>/.venv``
    in direct mode, ``/opt/venv`` in an image); its ``bin`` gets a
    directory EXECUTE grant.
    """
    tmp_home = Path(tempfile.mkdtemp(prefix="lc-home-"))
    for sub in (".config", ".cache", ".local/share", ".mplconfig", ".pycache"):
        (tmp_home / sub).mkdir(parents=True, exist_ok=True)

    write: list[Path] = [tmp_home]
    if scope.output_dir is not None:
        scope.output_dir.mkdir(parents=True, exist_ok=True)
        write.append(scope.output_dir.resolve())
    write.extend(p.resolve() for p in scratch_dirs if p.exists())
    for p in ("/tmp", "/dev/shm", "/dev/null"):
        if Path(p).exists():
            write.append(Path(p).resolve())

    fs_scope: str = "declared"
    if scope.writable_project:
        write.append(scope.project_root.resolve())
        fs_scope = "project-rw"

    read: list[Path] = [scope.project_root.resolve()]
    read.extend(p.resolve() for p in scope.read_paths if p.exists())
    for p in _OS_READ_BASELINE:
        if Path(p).exists():
            read.append(Path(p).resolve())

    execute: list[Path] = []
    bin_dir = env_prefix / "bin"
    if bin_dir.is_dir():
        execute.append(bin_dir.resolve())
        # The venv's `python` is a symlink to the uv-managed interpreter;
        # Landlock checks the *resolved* file, so the real install root
        # needs read+execute too (realpath every policy path).
        python_link = bin_dir / "python"
        if python_link.exists():
            real = python_link.resolve()
            install_root = real.parent.parent
            execute.append(install_root)
            read.append(install_root)
    unresolved: list[str] = []
    for name in EXEC_ALLOWLIST_V1:
        hit = shutil.which(name, path=_UTILITY_PATH)
        if hit is None:
            unresolved.append(name)
        else:
            execute.append(Path(hit).resolve())
    execute.extend(_elf_loaders())

    env = {
        "HOME": str(tmp_home),
        "XDG_CONFIG_HOME": str(tmp_home / ".config"),
        "XDG_CACHE_HOME": str(tmp_home / ".cache"),
        "XDG_DATA_HOME": str(tmp_home / ".local/share"),
        "MPLCONFIGDIR": str(tmp_home / ".mplconfig"),
        "PYTHONPYCACHEPREFIX": str(tmp_home / ".pycache"),
    }

    return SandboxPolicy(
        read=tuple(dict.fromkeys(read)),
        write=tuple(dict.fromkeys(write)),
        execute=tuple(dict.fromkeys(execute)),
        tmp_home=tmp_home,
        env=env,
        fs_scope=fs_scope,  # type: ignore[arg-type]
        exec_allowlist_version=EXEC_ALLOWLIST_VERSION,
        unresolved_utilities=tuple(unresolved),
    )
