"""Pure-function tests for the denial UX renderer."""
from __future__ import annotations

from pathlib import Path

from lightcone.engine.sandbox.denial import explain_failure, trailer
from lightcone.engine.sandbox.hints import HINT_TABLE_VERSION, HINTS, apt_hint
from lightcone.engine.sandbox.model import SandboxPolicy


def _policy(tmp_path: Path) -> SandboxPolicy:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return SandboxPolicy(
        read=(project,),
        write=(tmp_path / "out",),
        execute=(project / ".venv" / "bin",),
        tmp_home=tmp_path / "home",
        env={},
        fs_scope="declared",
        exec_allowlist_version=1,
    )


class TestExplainFailure:
    def test_tool_denial_renders_two_remedies(self, tmp_path: Path) -> None:
        lines = explain_failure(
            stdout="",
            stderr="bash: line 1: /usr/bin/id: Permission denied",
            policy=_policy(tmp_path),
        )
        joined = "\n".join(lines)
        assert "blocked by lc sandbox" in joined
        assert "cannot execute /usr/bin/id" in joined
        # Both remedies always shown; tool-first ordering here.
        assert "[tool.lightcone.image]" in joined
        assert "astra.yaml" in joined
        assert joined.index("[tool.lightcone.image]") < joined.index("astra.yaml")
        # Cost stated up front; escape hatches subdued at the end.
        assert "podman required" in joined
        assert "lc run --no-sandbox" in joined

    def test_known_tool_gets_apt_hint(self, tmp_path: Path) -> None:
        # Rscript exists on this system? Use a synthetic bin-dir path that
        # exists: fall back to a real allowlisted-tool-like case via
        # /usr/bin/Rscript existence check.
        target = Path("/usr/bin/Rscript")
        if not target.exists():
            import pytest

            pytest.skip("Rscript not installed on this host")
        lines = explain_failure(
            stdout="",
            stderr=f"bash: line 1: {target}: Permission denied",
            policy=_policy(tmp_path),
        )
        assert any("r-base-core" in line for line in lines)

    def test_data_denial_orders_input_remedy_first(self, tmp_path: Path) -> None:
        data = tmp_path / "external" / "table.csv"
        data.parent.mkdir()
        data.write_text("x\n")
        lines = explain_failure(
            stdout=f"PermissionError: [Errno 13] Permission denied: '{data}'",
            stderr="",
            policy=_policy(tmp_path),
        )
        joined = "\n".join(lines)
        assert f"cannot read {data}" in joined
        assert joined.index("astra.yaml") < joined.index("[tool.lightcone.image]")

    def test_in_policy_path_not_flagged(self, tmp_path: Path) -> None:
        """A path inside the granted sets is an ordinary recipe error,
        not a denial — no message (the trailer still fires elsewhere)."""
        policy = _policy(tmp_path)
        inside = policy.read[0] / "missing-but-in-project.txt"
        lines = explain_failure(
            stdout=f"FileNotFoundError: '{inside}'",
            stderr="",
            policy=policy,
        )
        assert lines == []

    def test_nonexistent_path_not_flagged(self, tmp_path: Path) -> None:
        """Re-stat separates denial from typo: a path that doesn't exist
        on the host is not a sandbox denial."""
        lines = explain_failure(
            stdout="FileNotFoundError: '/no/such/path/anywhere'",
            stderr="",
            policy=_policy(tmp_path),
        )
        assert lines == []

    def test_command_not_found_resolved_via_which(self, tmp_path: Path) -> None:
        """`foo: command not found` for a tool that exists outside the
        sandbox PATH classifies as a tool denial."""
        lines = explain_failure(
            stdout="",
            stderr="bash: line 1: id: command not found",
            policy=_policy(tmp_path),
        )
        assert any("blocked by lc sandbox" in line for line in lines)


class TestTrailer:
    def test_trailer_names_mechanism(self) -> None:
        t = trailer("landlock")
        assert "lc sandbox (landlock)" in t
        assert "lc run --sandbox-debug" in t


class TestHints:
    def test_capped_and_versioned(self) -> None:
        assert HINT_TABLE_VERSION == 1
        assert len(HINTS) <= 20, "the hint table is capped, never open-ended"
        assert apt_hint("latex") == "texlive-latex-base"
        assert apt_hint("some-unknown-tool") is None
