"""Enforcement: does the kernel actually stop the leaks?

Everything else in the suite checks what lc *says* it will enforce — the
policy it builds, the argv it emits, the profile it generates. This file
is the one that runs commands and looks at what happened, and it is
written once for **both** mechanisms: the same tests exercise Landlock on
Linux and Seatbelt on macOS, because the seam makes them the same shape.
That symmetry is the point. A leak that only Linux catches is a leak.

Two deliberate choices:

- **The real policy.** These run against
  :func:`~lightcone.engine.sandbox.policy.probe_policy` — what an actual
  ``lc run`` gets — not a policy hand-built to make a point. A test that
  grants exactly what it is testing cannot discover that the shipped
  policy grants something else.
- **Real leaks.** Undeclared *tools* are executed, undeclared *libraries*
  are imported, undeclared *data* is read. Those are the three channels
  the design exists to close, so they are attempted
  literally rather than asserted about.

Skipped whole where no mechanism exists — a mocked sandbox proves
nothing. But see :func:`_mechanism`: on CI that skip is a **failure**,
because a suite that silently skips its own subject is worse than no
suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from lightcone.engine import sandbox
from lightcone.engine.sandbox.boundary import env_argv
from lightcone.engine.sandbox.policy import _UTILITY_PATH, EXEC_ALLOWLIST

#: Set on CI. Turns "no sandbox here, skip" into a hard failure, so a
#: runner that quietly loses its mechanism cannot report green.
REQUIRED_ENV = "LC_SANDBOX_TESTS_REQUIRED"


def _mechanism() -> sandbox.Backend:
    backend = sandbox.detect()
    if backend.capability.kind == "none":
        detail = backend.capability.detail or "no mechanism"
        if os.environ.get(REQUIRED_ENV):
            pytest.fail(
                f"{REQUIRED_ENV} is set but this host cannot enforce: {detail}. "
                "Enforcement tests must not be skipped on CI."
            )
        pytest.skip(f"no sandbox mechanism here: {detail}")
    return backend


@pytest.fixture(scope="module")
def backend() -> sandbox.Backend:
    return _mechanism()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project shaped like a real one, minus the cost of a real venv.

    ``.venv/bin/python`` is a symlink to the running interpreter, which is
    exactly the shape the policy cares about: it grants EXECUTE on the
    *resolved* target and READ on the install root beside it.
    """
    root = tmp_path / "proj"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(Path(sys.executable).resolve())
    (root / "astra.yaml").write_text("title: T\n")
    (root / "data.txt").write_text("in-tree\n")
    return root


@pytest.fixture
def outside() -> Iterator[Path]:
    """A directory the project never declares — the leak's other end.

    Under `$HOME`, not `tmp_path`. `/tmp` is in the write baseline, so
    anything pytest hands us there is *granted*, and a denial test
    written against it would pass only for as long as something else
    happened to keep `/tmp` out of the policy. The real home is outside
    every grant by construction — the boundary hands the command a
    private one — which is the property these tests actually need.

    `mkdtemp`, not a fixed name: two suites sharing a home (xdist, two
    checkouts, two CI jobs) would otherwise race on the same directory,
    and a fixed name is also somebody's real path to delete.
    """
    elsewhere = Path(tempfile.mkdtemp(prefix="lc-enforcement-", dir=Path.home()))
    (elsewhere / "secret.txt").write_text("undeclared\n")
    (elsewhere / "sneaky.py").write_text("VALUE = 'undeclared import'\n")
    try:
        yield elsewhere
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


def run(
    backend: sandbox.Backend,
    policy: sandbox.Policy,
    argv: Sequence[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Exactly what :func:`sandbox.run` spawns, captured instead of teed.

    Mirrors the boundary's composition — the env overlay inside the wrap,
    no ``uv run`` prefix (these test the boundary, not the launcher) — so
    what runs here is what runs in production.
    """
    wrapped = backend.wrap(policy, [*env_argv(policy), *argv])
    return subprocess.run(wrapped, cwd=cwd, capture_output=True, text=True, check=False)


def shell(
    backend: sandbox.Backend, policy: sandbox.Policy, script: str, *, cwd: Path
) -> subprocess.CompletedProcess[str]:
    return run(backend, policy, ["bash", "-c", script], cwd=cwd)


def undeclared_tool() -> str:
    """A real binary on this host that the exec allowlist does not name.

    Not hardcoded: the allowlist is a maintained surface and the runners
    differ, so the test asks the host rather than assuming.
    """
    for name in ("git", "curl", "openssl", "id", "who", "hostname"):
        found = shutil.which(name, path=_UTILITY_PATH)
        if found and name not in EXEC_ALLOWLIST:
            return found
    pytest.skip("no undeclared tool available to try")  # pragma: no cover


# ---- the mechanism is what we think it is ---------------------------------


def test_the_expected_mechanism_is_in_use(backend: sandbox.Backend) -> None:
    expected = {"linux": "landlock", "darwin": "seatbelt"}.get(sys.platform)
    assert backend.capability.kind == expected


def test_a_sandboxed_run_attests_a_scoped_filesystem(
    backend: sandbox.Backend, project: Path
) -> None:
    with sandbox.scope(project) as policy:
        attestation = backend.attest(policy)
    assert attestation.fs == "declared"
    assert attestation.mechanism == backend.capability.kind


# ---- leak channel 1: undeclared tools --------------------------------------


def test_an_undeclared_host_tool_cannot_be_executed(
    backend: sandbox.Backend, project: Path
) -> None:
    """The #1 leakage channel: a recipe that works only because the
    author happens to have some tool installed."""
    tool = undeclared_tool()
    with sandbox.scope(project) as policy:
        result = shell(backend, policy, f"{tool} --version", cwd=project)
    assert result.returncode != 0, f"{tool} ran inside the sandbox"


def test_an_undeclared_tool_is_readable_but_still_not_executable(
    backend: sandbox.Backend, project: Path
) -> None:
    """The distinction the exec tier exists for. The OS baseline is
    readable so the dynamic linker works, which means an undeclared tool
    can be *seen* — running it is the leak, and only that is denied."""
    tool = undeclared_tool()
    with sandbox.scope(project) as policy:
        seen = shell(backend, policy, f"test -r {tool} && echo READABLE", cwd=project)
        ran = shell(backend, policy, f"{tool} --version", cwd=project)
    assert "READABLE" in seen.stdout
    assert ran.returncode != 0


def test_an_allowlisted_utility_is_executable(
    backend: sandbox.Backend, project: Path
) -> None:
    """The other half: the allowlist has to actually work, or every
    recipe that pipes through sed breaks."""
    with sandbox.scope(project) as policy:
        result = shell(backend, policy, "printf 'b\\na\\n' | sort | head -1", cwd=project)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "a"


def test_a_dynamically_linked_binary_runs_at_all(
    backend: sandbox.Backend, project: Path
) -> None:
    """Proves the loader tier — the ELF interpreter on Linux, dyld on
    macOS. Without it *nothing* dynamically linked starts, bash included,
    and every other test here would fail for the wrong reason."""
    with sandbox.scope(project) as policy:
        result = shell(backend, policy, "echo LOADER-OK", cwd=project)
    assert result.returncode == 0, result.stderr
    assert "LOADER-OK" in result.stdout


def test_a_binary_dropped_into_the_writable_scope_cannot_be_run(
    backend: sandbox.Backend, project: Path
) -> None:
    """Write does not imply execute. Otherwise the allowlist is two lines
    from being defeated: copy a tool into scratch, run it from there."""
    tool = undeclared_tool()
    with sandbox.scope(project) as policy:
        smuggled = policy.tmp_home / "smuggled"
        result = shell(
            backend,
            policy,
            f"cp {tool} {smuggled} && chmod +x {smuggled} && {smuggled} --version",
            cwd=project,
        )
    assert result.returncode != 0, "a binary copied into scratch was executable"


def test_the_projects_own_interpreter_runs_and_finds_its_stdlib(
    backend: sandbox.Backend, project: Path
) -> None:
    with sandbox.scope(project) as policy:
        result = shell(
            backend, policy, f"{project}/.venv/bin/python -c 'import json; print(json.dumps(1))'",
            cwd=project,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


# ---- leak channel 2: undeclared libraries ----------------------------------


def test_an_undeclared_python_module_cannot_be_imported(
    backend: sandbox.Backend, project: Path, outside: Path
) -> None:
    """Python-level leakage: a module reachable on the host but not part
    of the declared environment. Denied at *read*, so the import fails
    however sys.path was arranged."""
    with sandbox.scope(project) as policy:
        result = shell(
            backend,
            policy,
            f"{project}/.venv/bin/python -c "
            f"\"import sys; sys.path.insert(0, '{outside}'); import sneaky; print(sneaky.VALUE)\"",
            cwd=project,
        )
    assert result.returncode != 0
    assert "undeclared import" not in result.stdout


def test_a_compiled_extension_module_can_be_imported(
    backend: sandbox.Backend, project: Path
) -> None:
    """The declared environment has to keep working, and native code is
    where the two mechanisms differ: Landlock does not gate `mmap`, so a
    read grant is enough, while macOS gates `dlopen` on
    `file-map-executable`. If the read tier lost that right, this is the
    test that fails — on macOS only."""
    with sandbox.scope(project) as policy:
        result = shell(
            backend,
            policy,
            f"{project}/.venv/bin/python -c 'import ssl, zlib, _socket; print(\"EXT-OK\")'",
            cwd=project,
        )
    assert result.returncode == 0, result.stderr
    assert "EXT-OK" in result.stdout


def test_an_undeclared_shared_library_cannot_be_loaded(
    backend: sandbox.Backend, project: Path, outside: Path
) -> None:
    """The dlopen channel, tried literally: a real native library sitting
    at a path the project never declared. Denied at open on Linux, at
    mapping on macOS — either way it must not load."""
    library = _a_compiled_extension()
    smuggled = outside / library.name
    shutil.copy(library, smuggled)

    with sandbox.scope(project) as policy:
        result = shell(
            backend,
            policy,
            f"{project}/.venv/bin/python -c "
            f"\"import ctypes; ctypes.CDLL('{smuggled}'); print('LOADED')\"",
            cwd=project,
        )
    assert result.returncode != 0, "an undeclared shared library loaded"
    assert "LOADED" not in result.stdout


def _a_compiled_extension() -> Path:
    """Any real native library on this host, to smuggle somewhere undeclared.

    The interpreter's own stdlib extensions are the portable source: they
    exist on both platforms and are genuine loadable objects, unlike
    `/usr/lib/libz.dylib`, which on modern macOS is not a file at all.
    """
    dynload = Path(sysconfig.get_paths()["stdlib"]) / "lib-dynload"
    for extension in sorted(dynload.glob("*.so")) + sorted(dynload.glob("*.dylib")):
        return extension
    pytest.skip("no compiled extension module to copy")  # pragma: no cover


# ---- leak channel 3: undeclared data ---------------------------------------


def test_an_undeclared_data_file_cannot_be_read(
    backend: sandbox.Backend, project: Path, outside: Path
) -> None:
    with sandbox.scope(project) as policy:
        result = shell(backend, policy, f"cat {outside / 'secret.txt'}", cwd=project)
    assert result.returncode != 0
    assert "undeclared" not in result.stdout


def test_a_declared_input_outside_the_project_can_be_read(
    backend: sandbox.Backend, project: Path, outside: Path
) -> None:
    """The same file, declared. This is what makes the denial actionable
    rather than a wall: the remedy the message prints has to work."""
    declared = outside / "secret.txt"
    with sandbox.scope(project, read_paths=[declared]) as policy:
        result = shell(backend, policy, f"cat {declared}", cwd=project)
    assert result.returncode == 0, result.stderr
    assert "undeclared" in result.stdout


def test_the_project_tree_is_readable(backend: sandbox.Backend, project: Path) -> None:
    with sandbox.scope(project) as policy:
        result = shell(backend, policy, "cat data.txt", cwd=project)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "in-tree"


def test_the_real_home_is_not_readable(backend: sandbox.Backend, project: Path) -> None:
    """The dotfile-steering channel. A private HOME is what lets
    matplotlib and astropy work without it being reopened."""
    canary = Path.home() / ".lc-enforcement-canary"
    canary.write_text("host home\n")
    try:
        with sandbox.scope(project) as policy:
            result = shell(backend, policy, f"cat {canary}", cwd=project)
    finally:
        canary.unlink(missing_ok=True)
    assert result.returncode != 0
    assert "host home" not in result.stdout


# ---- the write scope -------------------------------------------------------


def test_the_project_tree_can_be_written(backend: sandbox.Backend, project: Path) -> None:
    """A container bind-mounts the working tree read-write, and so do we.
    The boundary is for catching a reach *outside* the project; a command
    writing inside its own project is not that."""
    with sandbox.scope(project) as policy:
        result = shell(backend, policy, "printf written > data.txt", cwd=project)
    assert result.returncode == 0, result.stderr
    assert (project / "data.txt").read_text() == "written"


def test_a_declared_input_is_read_only(
    backend: sandbox.Backend, project: Path, outside: Path
) -> None:
    """Declaring an input makes it readable, never writable — it is
    somebody else's file.

    Deliberately not a write to `/etc`: the OS denies that to any
    non-root user on its own, so such a test passes with no sandbox at
    all and pins nothing. This target is owned by the user running the
    suite, so the *only* thing that can refuse the write is the
    boundary.
    """
    target = outside / "secret.txt"
    with sandbox.scope(project, read_paths=[outside]) as policy:
        result = shell(backend, policy, f"printf clobbered > {target}", cwd=project)
    assert result.returncode != 0
    assert target.read_text() == "undeclared\n", "the file changed anyway"


def test_a_binary_dropped_into_the_venv_cannot_be_executed(
    backend: sandbox.Backend, project: Path
) -> None:
    """The exec allowlist has to survive a writable project.

    `.venv/bin` is exec-granted, and the tree is writable, so a grant on
    the *directory* would be a grant on whatever is written there next:
    copying a host tool in would run it, and the by-name denial of that
    same tool would mean nothing. The grants are per file, taken when the
    policy is built.
    """
    host_tool = shutil.which("git") or shutil.which("id")
    if host_tool is None:  # pragma: no cover - a host with neither
        pytest.skip("no host tool to smuggle")
    smuggled = project / ".venv" / "bin" / "smuggled"
    with sandbox.scope(project) as policy:
        result = shell(
            backend, policy, f"cp {host_tool} {smuggled} && {smuggled} --version", cwd=project
        )
    assert smuggled.exists(), "the copy itself should be allowed — the tree is writable"
    assert result.returncode != 0, "a binary written into .venv/bin must not be executable"


def test_the_private_scope_is_writable(backend: sandbox.Backend, project: Path) -> None:
    with sandbox.scope(project) as policy:
        target = policy.tmp_home / "result.txt"
        result = shell(backend, policy, f"printf ok > {target}", cwd=project)
        wrote = target.read_text() if target.exists() else ""
    assert result.returncode == 0, result.stderr
    assert wrote == "ok"


def test_tempfile_works_inside_the_boundary(
    backend: sandbox.Backend, project: Path
) -> None:
    """TMPDIR points into the private scope, so the stdlib's own scratch
    keeps working even where the shared /tmp left the write set."""
    with sandbox.scope(project) as policy:
        result = shell(
            backend,
            policy,
            f"{project}/.venv/bin/python -c "
            "\"import tempfile; f=tempfile.NamedTemporaryFile(delete=False); "
            "f.write(b'ok'); print('TEMP-OK')\"",
            cwd=project,
        )
    assert result.returncode == 0, result.stderr
    assert "TEMP-OK" in result.stdout


def test_a_command_can_allocate_a_pty(backend: sandbox.Backend, project: Path) -> None:
    """devpts and friends are granted, so pexpect and pytest's own
    capture work. Without them `pty.openpty()` fails as "out of pty
    devices" — a message naming neither a path nor the sandbox."""
    with sandbox.scope(project) as policy:
        result = shell(
            backend,
            policy,
            f"{project}/.venv/bin/python -c 'import pty; pty.openpty(); print(\"PTY-OK\")'",
            cwd=project,
        )
    assert result.returncode == 0, result.stderr
    assert "PTY-OK" in result.stdout


# ---- the boundary cannot be shed -------------------------------------------


def test_the_restriction_is_inherited_by_grandchildren(
    backend: sandbox.Backend, project: Path, outside: Path
) -> None:
    """Both mechanisms confine the whole descendant tree and neither can
    be shed — which is why wrapping the outermost command is enough."""
    with sandbox.scope(project) as policy:
        result = shell(
            backend,
            policy,
            f"bash -c 'bash -c \"cat {outside / 'secret.txt'}\"'",
            cwd=project,
        )
    assert result.returncode != 0
    assert "undeclared" not in result.stdout


# ---- what the user is told -------------------------------------------------


def test_a_denial_reaches_the_user_through_the_boundary(
    backend: sandbox.Backend, project: Path
) -> None:
    """The whole way through `sandbox.run`, not the test's own harness:
    a real denial must produce the explanation *and* the trailer, or the
    sandbox is an invisible wall."""
    tool = undeclared_tool()
    with sandbox.scope(project) as policy:
        outcome = sandbox.run(
            backend,
            policy,
            ["bash", "-c", f"{tool} --version"],
            cwd=project,
            env=dict(os.environ),
        )
    notes = "\n".join(outcome.notes)
    assert outcome.returncode != 0
    assert "ran under the lc sandbox" in notes, notes
    assert f"cannot execute {tool}" in notes, notes


# ---- the guard on this file itself -----------------------------------------


def test_the_ci_guard_fails_rather_than_skipping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one test here that must pass everywhere, including hosts with
    no mechanism: it checks that CI cannot go green by skipping the rest.

    Without this, `LC_SANDBOX_TESTS_REQUIRED` is a comment.
    """
    from lightcone.engine.sandbox.boundary import Unavailable
    from lightcone.engine.sandbox.model import Capability

    monkeypatch.setenv(REQUIRED_ENV, "1")
    monkeypatch.setattr(
        sandbox, "detect", lambda: Unavailable(capability=Capability(kind="none", detail="pretend"))
    )
    with pytest.raises(pytest.fail.Exception, match="must not be skipped"):
        _mechanism()


def test_without_the_guard_a_mechanismless_host_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """And off CI it stays a skip — a laptop without Landlock should run
    the rest of the suite, not fail it."""
    from lightcone.engine.sandbox.boundary import Unavailable
    from lightcone.engine.sandbox.model import Capability

    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    monkeypatch.setattr(
        sandbox, "detect", lambda: Unavailable(capability=Capability(kind="none", detail="pretend"))
    )
    with pytest.raises(pytest.skip.Exception):
        _mechanism()


def test_an_allowlisted_tool_resolves_to_the_copy_that_was_granted(
    backend: sandbox.Backend, project: Path
) -> None:
    """Found by the macOS runner: PATH inside the boundary has to be the
    search path the exec set was built from. The runner's ambient PATH
    fronts homebrew, so `env bash` resolved `/opt/homebrew/bin/bash`
    while the policy had granted `/bin/bash` — and the sandbox denied
    bash itself with "Operation not permitted"."""
    with sandbox.scope(project) as policy:
        result = shell(backend, policy, "command -v bash && echo RESOLVED", cwd=project)
    assert result.returncode == 0, result.stderr
    resolved = Path(result.stdout.splitlines()[0].strip())
    assert policy.grants(resolved, policy.execute), f"{resolved} is not in the exec set"
