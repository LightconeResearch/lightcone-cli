"""Tests for `lightcone.engine.sandbox.policy` — what a probe may touch.

Pure construction, no enforcement: these run on any OS. Whether the
kernel honors the policy is `test_sandbox_landlock.py`'s question.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from lightcone.engine.sandbox import policy as policy_module
from lightcone.engine.sandbox.boundary import scope
from lightcone.engine.sandbox.model import EXEC_ALLOWLIST_VERSION


@pytest.fixture
def built(tmp_path: Path) -> Iterator[policy_module.Policy]:
    """A probe policy over a bare project directory.

    Through `boundary.scope`, which owns the per-run HOME's lifetime —
    so the cleanup contract is exercised by the suite rather than
    re-implemented seven times beside it.
    """
    project = tmp_path / "proj"
    project.mkdir()
    with scope(policy_module.exec_policy(project)) as built:
        yield built


# ---- the write scope ------------------------------------------------------


def test_the_tree_is_read_only_apart_from_results(
    built: policy_module.Policy, tmp_path: Path
) -> None:
    """The environment a run starts with is the one it ends with: nothing
    it does can touch `.venv`, the lock, or the spec."""
    project = tmp_path / "proj"
    assert not built.grants(project / "astra.yaml", built.write)
    assert not built.grants(project / ".venv" / "bin" / "python", built.write)


def test_results_is_writable(tmp_path: Path) -> None:
    """Output goes here, and it is the same scope a recipe gets — which
    is what makes a probe a probe of the real thing.

    A writable directory nested inside a read-only tree is the shape all
    three mechanisms express natively: Landlock unions rights so a nested
    grant only widens, SBPL restates the write tier after the guard, and
    podman mounts `results` `:rw` over a `:ro` project."""
    project = tmp_path / "proj"
    (project / "results").mkdir(parents=True)
    with scope(policy_module.exec_policy(project)) as built:
        assert built.grants(project / "results" / "out.csv", built.write)


def test_results_is_granted_only_if_it_exists(tmp_path: Path) -> None:
    """Convergence makes it. A policy that made directories would be a
    side effect nobody asked a probe for."""
    project = tmp_path / "bare"
    project.mkdir()
    with scope(policy_module.exec_policy(project)) as built:
        assert not built.grants(project / "results", built.write)
        assert not (project / "results").exists()


def test_the_project_is_also_readable(built: policy_module.Policy, tmp_path: Path) -> None:
    """Not redundant with the write grant. Landlock's write bits include
    the read ones, but SBPL's write tier grants `file-read* file-write*`
    and *not* `file-map-executable` — which macOS gates `dlopen` on, and
    every compiled extension module under site-packages needs. Dropping
    the project from `read` as duplication breaks `import numpy` on macOS
    alone, with Linux CI still green."""
    assert built.grants(tmp_path / "proj" / "astra.yaml", built.read)


def test_the_private_home_is_writable(built: policy_module.Policy) -> None:
    assert built.tmp_home in built.write


def test_the_shared_tmp_is_writable_for_a_project_outside_it() -> None:
    """`/tmp` specifically, not `gettempdir()`: on macOS the latter is the
    per-user `$TMPDIR` under /var/folders, a different directory that is
    not in the baseline at all."""
    with scope(policy_module.exec_policy(Path.home() / ".lc-policy-test-project")) as built:
        assert Path("/tmp").resolve() in built.write


def test_a_project_living_under_tmp_does_not_become_writable() -> None:
    """`/tmp` is writable by design, so a project that *lives* there would
    otherwise be writable through that grant — voiding the read-only tree
    for exactly the people who keep scratch analyses in /tmp."""
    shared = Path("/tmp").resolve()
    with scope(policy_module.exec_policy(shared / "lc-policy-under-tmp")) as built:
        assert shared not in built.write


def test_tmpdir_always_points_into_the_private_scope(built: policy_module.Policy) -> None:
    """Which is what keeps `tempfile` working even when the shared /tmp
    had to be dropped from the write set."""
    assert Path(built.env["TMPDIR"]).is_relative_to(built.tmp_home)
    assert Path(built.env["TMPDIR"]).is_dir()


# ---- the read scope -------------------------------------------------------


def test_the_project_is_readable(built: policy_module.Policy, tmp_path: Path) -> None:
    assert (tmp_path / "proj").resolve() in built.read


def test_declared_inputs_join_the_read_scope(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    external = tmp_path / "elsewhere" / "data.fits"
    external.parent.mkdir()
    external.touch()

    with scope(policy_module.exec_policy(project, read_paths=[external])) as built:
        assert external.resolve() in built.read


def test_the_os_baseline_is_readable_but_never_executable(built: policy_module.Policy) -> None:
    """The distinction the whole layer rests on: /usr must be *readable*
    or the dynamic linker cannot work, and must not be *executable* or
    every undeclared tool on the host is admitted."""
    assert Path("/usr") in built.read
    assert Path("/usr") not in built.execute


@pytest.mark.skipif(sys.platform == "win32", reason="no /dev on Windows")
def test_dev_urandom_is_readable(built: policy_module.Policy) -> None:
    """Not decoration: CPython seeds hash randomization from it during
    preinitialization, so without the grant the interpreter dies before
    `main` with "failed to get random numbers"."""
    assert Path("/dev/urandom") in built.read


def test_the_nix_roots_are_in_the_read_baseline() -> None:
    """On NixOS *everything* — interpreter, libraries, the utility
    allowlist — resolves into these, so omitting them makes the sandbox
    unusable there rather than merely incomplete. Asserted against the
    constant, since neither path exists on most hosts."""
    assert "/nix/store" in policy_module._OS_READ_BASELINE
    assert "/run/current-system/sw" in policy_module._OS_READ_BASELINE


def test_the_read_baseline_is_system_paths_only() -> None:
    """It must never widen to reach user data — that is what the project
    and declared-input grants are for."""
    home = str(Path.home())
    for entry in policy_module._OS_READ_BASELINE:
        assert not entry.startswith(home), entry
        assert entry.startswith("/"), entry


def test_the_minimal_device_set_is_covered(built: policy_module.Policy) -> None:
    """bubblewrap materializes `null, zero, full, random, urandom, tty`
    from one `--dev` flag. Landlock has no device-tree primitive, so we
    enumerate the same set, split by the access each needs — `/dev/tty`
    writable, without which anything opening the controlling terminal
    afresh fails, `lc run`'s own shell included."""
    granted = {*built.read, *built.write}
    for node in ("null", "zero", "full", "random", "urandom", "tty"):
        device = Path("/dev") / node
        if device.exists():
            assert device in granted, device
    # The terminal set specifically: writable, so anything opening the
    # controlling terminal or allocating a pty works. That it *actually*
    # works is `test_sandbox_enforcement.py`'s job.
    for node in ("/dev/tty", "/dev/pts", "/dev/ptmx"):
        device = Path(node)
        if device.exists():
            assert device.resolve() in built.write, node


def test_discard_devices_are_writable(built: policy_module.Policy) -> None:
    """Writes to these are discard-by-construction — that is what the
    devices are — so read-only buys nothing and breaks tools that use
    /dev/full to exercise ENOSPC handling."""
    for node in ("/dev/null", "/dev/zero", "/dev/full"):
        device = Path(node)
        if device.exists():
            assert device in built.write, node


def test_the_entropy_sources_stay_read_only(built: policy_module.Policy) -> None:
    """The one place the permissive line is drawn: writing to these seeds
    the *host's* pool, which is a side effect on the machine rather than
    on the run."""
    for node in ("/dev/urandom", "/dev/random"):
        device = Path(node)
        if device.exists():
            assert device in built.read, node
            assert device not in built.write, node


def test_proc_and_sys_are_not_restricted(built: policy_module.Policy) -> None:
    """Real tools write them — /proc/self/oom_score_adj, coredump_filter,
    MPI and CUDA runtimes poking /sys — and none of it is a channel
    undeclared inputs arrive through. The kernel's own permissions stay
    the real gate; Landlock only ever removes access, never adds it."""
    for node in ("/proc", "/sys"):
        directory = Path(node)
        if directory.exists():
            assert directory in built.write, node


# ---- the exec scope -------------------------------------------------------


def test_allowlisted_utilities_are_granted_per_file(built: policy_module.Policy) -> None:
    bash = shutil.which("bash", path=policy_module._UTILITY_PATH)
    if bash is None:  # pragma: no cover - a host without bash
        pytest.skip("no bash in the utility path")
    resolved = Path(bash).resolve()
    assert resolved in built.execute, f"{bash} -> {resolved}; granted: {built.execute}"
    assert resolved.parent not in built.execute


def test_the_allowlist_is_resolved_off_the_ambient_path(
    built: policy_module.Policy, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A PATH fronted with a directory of undeclared tools must not widen
    the exec set — which is why resolution uses a fixed search path."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    impostor = shadow / "bash"
    impostor.write_text("#!/bin/sh\n")
    impostor.chmod(0o755)
    monkeypatch.setenv("PATH", str(shadow))

    project = tmp_path / "proj2"
    project.mkdir()
    with scope(policy_module.exec_policy(project)) as rebuilt:
        assert impostor.resolve() not in rebuilt.execute


def test_the_env_the_seam_execs_is_one_the_policy_granted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overlay is applied by exec'ing `env`, so it has to be in the
    exec set — and the *same* `env`.

    The search path is pointed at a copy here on purpose. Two answers to
    "where does env live" agree on a host whose copy sits at the usual
    place and disagree everywhere else, so a test run against the real
    path would pass while the bug shipped: `/usr` is readable and never
    executable, making a stale answer a denial on the first exec of
    every single run.
    """
    from lightcone.engine.sandbox.boundary import env_argv

    elsewhere = tmp_path / "bin"
    elsewhere.mkdir()
    real = shutil.which("env", path=policy_module._UTILITY_PATH)
    assert real is not None, "no `env` on the search path"
    shutil.copy(real, elsewhere / "env")
    monkeypatch.setattr(policy_module, "_UTILITY_PATH", str(elsewhere))

    project = tmp_path / "proj"
    project.mkdir()
    with scope(policy_module.exec_policy(project)) as built:
        spawned = Path(env_argv(built)[0])
        assert spawned == elsewhere / "env"
        assert built.grants(spawned, built.execute)


@pytest.mark.skipif(sys.platform != "linux", reason="the ELF loader tier is Linux-only")
def test_the_elf_loader_is_in_the_exec_set(built: policy_module.Policy) -> None:
    """Landlock checks EXECUTE on the loader's own open, so without this
    every dynamically linked binary — bash and python included — fails
    EACCES and the sandbox is unusable."""
    loaders = policy_module.elf_loaders()
    assert loaders, "no ELF loader found on this host"
    assert all(loader in built.execute for loader in loaders)


def test_the_venv_and_the_interpreter_behind_it_are_granted(tmp_path: Path) -> None:
    """`.venv/bin/python` is a symlink and Landlock evaluates the resolved
    path, so the target needs EXECUTE — as a *file* — and its install root
    needs READ for the stdlib beside it."""
    project = tmp_path / "proj"
    bin_dir = project / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    store = tmp_path / "store" / "cpython-3.13" / "bin"
    store.mkdir(parents=True)
    real = store / "python3"
    real.write_text("")
    real.chmod(0o755)
    (bin_dir / "python").symlink_to(real)

    with scope(policy_module.exec_policy(project)) as built:
        install_root = store.parent.resolve()
        # The *directory* is deliberately not granted: the tree is
        # writable, so that would be an exec grant on whatever is written
        # into `.venv/bin` next. See `test_the_venv_bin_is_granted_per_file`.
        assert bin_dir.resolve() not in built.execute
        assert real.resolve() in built.execute
        assert install_root in built.read
        # Its own tree, so EXECUTE too: a framework build re-execs itself
        # into `Resources/Python.app/Contents/MacOS/Python`, which the
        # binary-only grant would miss.
        assert install_root in built.execute


def test_the_venv_bin_is_granted_per_file_not_as_a_directory(tmp_path: Path) -> None:
    """The exec allowlist has to survive a writable project.

    `.venv/bin` is exec-granted and the tree is writable, so granting the
    *directory* would grant whatever is written there next — copy a host
    tool in and it runs, making the by-name denial of that same tool
    meaningless. The grants are taken per file when the policy is built.
    """
    project = tmp_path / "proj"
    bin_dir = project / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    script = bin_dir / "pytest"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)

    with scope(policy_module.exec_policy(project)) as built:
        assert script.resolve() in built.execute, "a console script that exists is granted"
        assert bin_dir.resolve() not in built.execute, "but never the directory"
        # What a run writes there afterwards is therefore not runnable.
        later = bin_dir / "smuggled"
        later.write_text("#!/bin/sh\n")
        later.chmod(0o755)
        assert not built.grants(later, built.execute)


def test_a_system_interpreter_does_not_make_the_whole_prefix_executable(
    tmp_path: Path,
) -> None:
    """The other side of the same rule. A venv built against the system
    python resolves to `/usr/bin/python3`, whose install root is `/usr` —
    a prefix shared with the whole host. Granting EXECUTE there would
    make every binary on the machine runnable, and because Landlock
    unions rights over ancestors that one grant silently outranks the
    entire per-file allowlist."""
    project = tmp_path / "proj"
    bin_dir = project / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    system = Path("/usr/bin/python3")
    if not system.exists():  # pragma: no cover - unusual host
        pytest.skip("no system python3")
    (bin_dir / "python").symlink_to(system)

    with scope(policy_module.exec_policy(project)) as built:
        assert Path("/usr") not in built.execute
        assert Path("/usr") in built.read
        assert system.resolve() in built.execute


def test_an_interpreter_in_home_does_not_make_home_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stdlib read root is derived from the interpreter's location, so
    an interpreter installed straight into `~/bin` would hand the read
    set `$HOME` itself — silently undoing the private-HOME design, which
    is the one guarantee the environment overlay exists to make. Reading
    the base prefix out of `pyvenv.cfg` would report the same directory,
    so the guard is the fix, not a better lookup."""
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    interpreter = home / "bin" / "python"
    interpreter.write_text("#!/bin/sh\n")
    interpreter.chmod(0o755)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    project = tmp_path / "proj"
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "python").symlink_to(interpreter)

    with scope(policy_module.exec_policy(project)) as built:
        assert not built.grants(home, built.read), built.read
        # The interpreter file itself is still runnable.
        assert built.grants(interpreter, built.execute)


def test_the_utility_path_covers_the_nix_system_profile() -> None:
    """The read baseline was widened for NixOS, but the allowlist is
    resolved off a fixed search path — if that stays FHS-only, `bash`
    never enters the exec set there and a bare `lc run` is denied with a
    nonsense remedy."""
    assert "/run/current-system/sw/bin" in policy_module._UTILITY_PATH


# ---- HOME, XDG, and hygiene -----------------------------------------------


def test_home_and_friends_point_into_the_write_scope(built: policy_module.Policy) -> None:
    """matplotlib, astropy, and R all want a HOME. Giving them a private
    one is what lets them work without the real `$HOME` being readable
    — mounting `$HOME` RO instead would reopen the
    dotfile-steering channel the layer exists to close."""
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "MPLCONFIGDIR"):
        assert Path(built.env[key]).is_relative_to(built.tmp_home), key


def test_bytecode_is_redirected_out_of_the_read_only_tree(
    built: policy_module.Policy,
) -> None:
    """Without it every `import` of an in-tree module fails trying to
    write its `__pycache__` into a tree it may not write."""
    assert Path(built.env["PYTHONPYCACHEPREFIX"]).is_relative_to(built.tmp_home)


def test_the_home_subdirs_exist(built: policy_module.Policy) -> None:
    """Created up front: a tool that wants `~/.config` should find it
    rather than fail trying to make it."""
    assert (built.tmp_home / ".config").is_dir()
    assert (built.tmp_home / ".cache").is_dir()


def test_every_path_is_realpathed(built: policy_module.Policy) -> None:
    """macOS resolves /tmp to /private/tmp and Landlock evaluates the
    resolved path, so an unresolved rule silently matches nothing."""
    for group in (built.read, built.write, built.execute):
        for path in group:
            assert path == path.resolve(), path


def test_nonexistent_paths_are_dropped(built: policy_module.Policy) -> None:
    """A rule cannot be added for a path that is not there, and a
    baseline entry missing on this OS is normal rather than fatal."""
    for group in (built.read, built.write, built.execute):
        assert all(path.exists() for path in group)


def test_the_allowlist_version_reaches_the_attestation(built: policy_module.Policy) -> None:
    """The exec allowlist is a maintained surface; an output stays
    interpretable after it grows only because the version rode along into
    the manifest."""
    from lightcone.engine.sandbox.landlock import LandlockBackend
    from lightcone.engine.sandbox.model import Capability

    backend = LandlockBackend(capability=Capability(kind="landlock", landlock_abi=1))
    assert backend.attest(built).exec_allowlist_version == EXEC_ALLOWLIST_VERSION


def test_path_is_the_exec_search_path_we_granted(
    built: policy_module.Policy, tmp_path: Path
) -> None:
    """The command must resolve tools through the same list the policy
    granted from. Otherwise a host whose ambient PATH fronts another copy
    of an allowlisted tool — homebrew's bash on macOS — gets that copy
    denied, and the denial blames the command for lc's own incoherence.
    """
    entries = built.env["PATH"].split(os.pathsep)
    assert entries[0] == str(tmp_path / "proj" / ".venv" / "bin"), "the project env comes first"
    assert entries[1:] == policy_module._UTILITY_PATH.split(os.pathsep)


def test_the_allowlist_is_resolved_with_the_real_which() -> None:
    """The autouse `tools` fixture fakes `shutil.which` — and because
    `project.shutil` *is* the global module, a blanket fake silently
    reached every test in the suite. The exec set became
    `/usr/bin/<tool>` for every tool, which exists on Linux and does not
    on macOS, so the enforcement tests ran against a policy no user could
    ever have. The fake must cover uv and git and nothing else, and must
    never invent a location for either.
    """
    assert shutil.which("uv") is not None, "convergence's substrate check must pass"
    # Anything else is the real `shutil.which`: a path that is really
    # there, or None — never one the fixture made up.
    found = shutil.which("sh")
    assert found is not None and Path(found).exists(), found
    assert shutil.which("lc-definitely-not-a-real-tool") is None
