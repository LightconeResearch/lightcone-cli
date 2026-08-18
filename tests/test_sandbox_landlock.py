"""Enforcement tests — a real kernel, real denials, no fakes.

Everything else in the suite checks what lc *says* it will enforce.
This file checks that the kernel agrees, by running commands through the
shim and looking at what happened. Skipped whole where Landlock is not
available, rather than mocked: a mocked sandbox proves nothing.

The policies here are built by hand rather than by
:func:`~lightcone.engine.sandbox.policy.probe_policy`, so a test can
grant exactly what it is testing. That is also what lets fixtures live
under ``tmp_path``: the real probe policy makes ``/tmp`` blanket
writable, which would mask every write denial below.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from lightcone import _sandbox_exec
from lightcone.engine.sandbox import policy as policy_module
from lightcone.engine.sandbox.landlock import LandlockBackend
from lightcone.engine.sandbox.model import Capability, Policy

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="Landlock is Linux-only"),
    pytest.mark.skipif(
        sys.platform == "linux" and _sandbox_exec.abi() == 0,
        reason="this kernel has no Landlock",
    ),
]


@pytest.fixture
def sandbox_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """A readable "project" and a writable "output" beside it."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "data.txt").write_text("original\n")
    outputs = tmp_path / "out"
    outputs.mkdir()
    return project, outputs


@pytest.fixture
def policy(sandbox_dirs: tuple[Path, Path]) -> Policy:
    project, outputs = sandbox_dirs
    interpreter = Path(sys.executable).resolve()
    return Policy(
        read=(
            project,
            Path("/usr"),
            Path("/etc"),
            # CPython cannot even preinitialize without it: it seeds hash
            # randomization before `main`. Load-bearing in the real
            # baseline for the same reason.
            Path("/dev/urandom"),
            interpreter.parent.parent,
            # `site` reads pyvenv.cfg at the venv root; the real probe
            # policy covers it via the project grant.
            Path(sys.executable).parent.parent,
            *(p for p in (Path("/lib"), Path("/lib64")) if p.exists()),
        ),
        write=(outputs,),
        execute=(
            interpreter,
            *(Path(p) for p in _bin("bash", "sort", "printf", "cat") if p),
            *policy_module.elf_loaders(),
        ),
        tmp_home=outputs,
        env={},
    )


def _bin(*names: str) -> list[str]:
    import shutil

    return [found for name in names if (found := shutil.which(name, path="/usr/bin:/bin"))]


def run(policy: Policy, script: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run *script* under bash, inside the boundary."""
    capability = Capability(kind="landlock", landlock_abi=_sandbox_exec.abi())
    argv = LandlockBackend(capability=capability).wrap(policy, ["bash", "-c", script])
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


# ---- the filesystem -------------------------------------------------------


def test_a_readable_path_can_be_read(policy: Policy, sandbox_dirs: tuple[Path, Path]) -> None:
    project, _ = sandbox_dirs
    result = run(policy, "cat data.txt", cwd=project)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "original\n"


def test_a_readable_path_cannot_be_written(
    policy: Policy, sandbox_dirs: tuple[Path, Path]
) -> None:
    """Read does not imply write — this is what stops a recipe from
    clobbering sibling outputs, manifests, or astra.yaml."""
    project, _ = sandbox_dirs
    result = run(policy, "printf clobbered > data.txt", cwd=project)
    assert result.returncode != 0
    assert (project / "data.txt").read_text() == "original\n", "the file was modified anyway"


def test_the_write_scope_is_writable(policy: Policy, sandbox_dirs: tuple[Path, Path]) -> None:
    project, outputs = sandbox_dirs
    result = run(policy, f"printf ok > {outputs}/result.txt", cwd=project)
    assert result.returncode == 0, result.stderr
    assert (outputs / "result.txt").read_text() == "ok"


def test_an_undeclared_path_cannot_be_read(
    policy: Policy, sandbox_dirs: tuple[Path, Path], tmp_path: Path
) -> None:
    project, _ = sandbox_dirs
    secret = tmp_path / "undeclared.txt"
    secret.write_text("secret\n")
    result = run(policy, f"cat {secret}", cwd=project)
    assert result.returncode != 0
    assert "secret" not in result.stdout


# ---- exec -----------------------------------------------------------------


def test_a_dynamically_linked_binary_runs(policy: Policy, sandbox_dirs: tuple[Path, Path]) -> None:
    """The ELF loader tier, proven rather than asserted: Landlock checks
    EXECUTE on the loader's own open, so if the loader were missing from
    the exec set this — and every other binary, bash included — would
    fail EACCES (spec §7, and the v6 review's must-fix)."""
    project, _ = sandbox_dirs
    result = run(policy, "printf 'b\\na\\n' | sort | cat", cwd=project)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "a\nb\n"


def test_a_readable_binary_still_cannot_be_executed(
    policy: Policy, sandbox_dirs: tuple[Path, Path]
) -> None:
    """The distinction the exec tier exists for: /usr is readable, so an
    undeclared tool can be *seen* — but running it is the leak, and it is
    denied."""
    project, _ = sandbox_dirs
    result = run(policy, "id", cwd=project)
    assert result.returncode != 0
    assert "Permission denied" in result.stderr


def test_the_interpreter_can_import_its_own_standard_library(
    policy: Policy, sandbox_dirs: tuple[Path, Path]
) -> None:
    """The stdlib sits beside the interpreter, outside the project and
    outside /usr when uv manages it — without a read grant there, python
    dies before `main` with "Failed to import encodings module"."""
    project, _ = sandbox_dirs
    result = run(policy, f"{sys.executable} -c 'import json; print(json.dumps(1))'", cwd=project)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_allocating_a_pty_needs_devpts_and_says_so_badly(
    sandbox_dirs: tuple[Path, Path], policy: Policy
) -> None:
    """Without devpts, `pty.openpty()` fails as "out of pty devices" — a
    message that names neither the sandbox nor a path, so the denial
    classifier cannot help and only the trailer would fire. That is the
    argument for granting it rather than discovering it in the field."""
    project, _ = sandbox_dirs
    script = f"{sys.executable} -c 'import pty; pty.openpty(); print(\"PTY-OK\")'"

    if not Path("/dev/ptmx").exists():  # pragma: no cover - unusual host
        pytest.skip("no /dev/ptmx on this host")

    denied = run(policy, script, cwd=project)
    assert denied.returncode != 0
    assert "out of pty devices" in denied.stderr

    widened = Policy(
        **{
            **policy.__dict__,
            "write": (*policy.write, Path("/dev/pts"), Path("/dev/ptmx").resolve()),
        }
    )
    allowed = run(widened, script, cwd=project)
    assert allowed.returncode == 0, allowed.stderr
    assert "PTY-OK" in allowed.stdout


# ---- the restriction cannot be shed ---------------------------------------


def test_the_restriction_is_inherited_by_grandchildren(
    policy: Policy, sandbox_dirs: tuple[Path, Path]
) -> None:
    """Landlock domains are inherited across every fork and exec and can
    only be tightened — which is why wrapping the outermost command is
    enough to bound everything it starts."""
    project, _ = sandbox_dirs
    result = run(policy, "bash -c 'bash -c \"cat /etc/shadow\"'", cwd=project)
    assert result.returncode != 0


# ---- what the run reports -------------------------------------------------


def test_the_probe_reports_this_kernels_abi() -> None:
    from lightcone.engine.sandbox.landlock import capability

    found = capability()
    assert found.kind == "landlock"
    assert found.landlock_abi is not None and found.landlock_abi >= 1


def test_detect_picks_landlock_here() -> None:
    from lightcone.engine.sandbox import detect

    assert detect().capability.kind == "landlock"
