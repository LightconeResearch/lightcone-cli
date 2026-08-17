"""End-to-end Landlock enforcement through the real shim.

These run unprivileged (Landlock needs no capabilities). Fixture
projects live under ``$HOME`` — NOT pytest's tmp_path — because the §7
policy grants ``/tmp`` blanket-RW, which would mask project-tree
denials for a project living inside it.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from lightcone.engine.boundary import ExecScope
from lightcone.engine.sandbox import _landlock
from lightcone.engine.sandbox.exec_boundary import SandboxExecBoundary

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="Landlock is Linux-only"),
    pytest.mark.skipif(
        sys.platform == "linux" and _landlock.abi() == 0,
        reason="Landlock unavailable on this kernel",
    ),
]


@pytest.fixture
def home_project() -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix="lc-sbx-", dir=Path.home()))
    try:
        project = root / "proj"
        (project / "results" / "u1" / "foo").mkdir(parents=True)
        (project / "data.txt").write_text("in-project data\n")
        (root / "outside.txt").write_text("secret outside the project\n")
        yield project
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run(project: Path, command: str, **scope_kwargs):  # type: ignore[no-untyped-def]
    scope_kwargs.setdefault("read_paths", ())
    scope = ExecScope(
        project_root=project,
        output_dir=project / "results" / "u1" / "foo",
        **scope_kwargs,
    )
    import os

    return SandboxExecBoundary().execute(command, scope, env=dict(os.environ))


class TestFilesystem:
    def test_write_in_own_output_allowed(self, home_project: Path) -> None:
        r = _run(home_project, "echo done > results/u1/foo/out.txt")
        assert r.returncode == 0, r.stderr
        assert (home_project / "results" / "u1" / "foo" / "out.txt").exists()

    def test_project_tree_write_denied(self, home_project: Path) -> None:
        """Sibling outputs, manifests, and astra.yaml are protected from
        a misbehaving recipe."""
        r = _run(home_project, "echo overwrite > data.txt")
        assert r.returncode != 0
        assert (home_project / "data.txt").read_text() == "in-project data\n"

    def test_project_read_allowed(self, home_project: Path) -> None:
        r = _run(home_project, "cat data.txt")
        assert r.returncode == 0
        assert "in-project data" in r.stdout

    def test_undeclared_read_denied(self, home_project: Path) -> None:
        outside = home_project.parent / "outside.txt"
        r = _run(home_project, f"cat {outside}")
        assert r.returncode != 0
        assert "secret" not in r.stdout

    def test_declared_input_read_allowed(self, home_project: Path) -> None:
        outside = home_project.parent / "outside.txt"
        r = _run(home_project, f"cat {outside}", read_paths=(outside,))
        assert r.returncode == 0
        assert "secret" in r.stdout

    def test_writable_project_escalation(self, home_project: Path) -> None:
        r = _run(home_project, "echo v2 > data.txt", writable_project=True)
        assert r.returncode == 0, r.stderr
        assert (home_project / "data.txt").read_text() == "v2\n"

    def test_real_home_not_readable(self, home_project: Path) -> None:
        probe_file = Path.home() / ".lc-sandbox-test-canary"
        probe_file.write_text("canary")
        try:
            r = _run(home_project, f"cat {probe_file}")
            assert r.returncode != 0
            assert "canary" not in r.stdout
        finally:
            probe_file.unlink()


class TestExec:
    def test_dynamically_linked_exec_succeeds(self, home_project: Path) -> None:
        """/bin/ls is dynamically linked: this passing proves the ELF
        loader tier — Landlock checks EXECUTE on the loader's open, so
        without it every dynamic binary fails EACCES."""
        r = _run(home_project, "ls results")
        assert r.returncode == 0, r.stderr

    def test_undeclared_tool_exec_denied(self, home_project: Path) -> None:
        # /usr/bin/id exists and is readable (OS baseline) but is not in
        # the exec allowlist.
        assert Path("/usr/bin/id").exists()
        r = _run(home_project, "/usr/bin/id")
        assert r.returncode != 0
        assert "uid=" not in r.stdout

    def test_allowlisted_pipeline_works(self, home_project: Path) -> None:
        r = _run(
            home_project,
            "printf 'b\\na\\n' | sort | head -1 > results/u1/foo/first.txt",
        )
        assert r.returncode == 0, r.stderr
        assert (home_project / "results/u1/foo/first.txt").read_text() == "a\n"


class TestPycache:
    def test_no_pycache_in_tree_and_import_succeeds(
        self, home_project: Path
    ) -> None:
        """PYTHONPYCACHEPREFIX (approved §7 amendment): in-tree imports
        work at full speed with the tree read-only, and no __pycache__
        appears in the project."""
        (home_project / "mymod.py").write_text("VALUE = 41 + 1\n")
        venv_bin = home_project / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(sys.executable)
        r = _run(
            home_project,
            '.venv/bin/python -c "import mymod; print(mymod.VALUE)"',
        )
        assert r.returncode == 0, r.stderr
        assert "42" in r.stdout
        assert not (home_project / "__pycache__").exists()


class TestAttestation:
    def test_landlock_attested(self, home_project: Path) -> None:
        r = _run(home_project, "true")
        assert r.attestation.mechanism == "landlock"
        assert r.attestation.fs == "declared"
        assert r.attestation.network == "unenforced"
        assert (r.attestation.landlock_abi or 0) >= 1

    def test_denial_message_renders_for_tool(self, home_project: Path) -> None:
        """The primary UI: a blocked exec explains itself with the
        two-remedy message."""
        r = _run(home_project, "/usr/bin/id")
        joined = "\n".join(r.notes)
        assert "blocked by lc sandbox" in joined
        assert "[tool.lightcone.image]" in joined
        assert "astra.yaml" in joined
        assert "lc run --sandbox-debug" in joined

    def test_trailer_fires_on_swallowed_error(self, home_project: Path) -> None:
        """A recipe that swallows the PermissionError and exits nonzero
        still gets the fixed trailer — a denial can never be invisible."""
        r = _run(
            home_project,
            "cat data.txt > /dev/null; echo overwrite > data.txt 2>/dev/null; exit 3",
        )
        assert r.returncode == 3
        joined = "\n".join(r.notes)
        assert "ran under the lc sandbox" in joined
        assert "--sandbox-debug" in joined
