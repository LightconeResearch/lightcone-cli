"""Tests for the per-rule run_rule helper — the worker sequence.

run_rule is invoked from the generated Snakefile's ``run:`` block with
cwd == project root; we exercise it directly against fixture projects,
capturing stdout to assert on the sentinel-prefixed framing the
executor relies on.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from conftest import make_project

from lightcone.engine import runner
from lightcone.engine.environment import load_environment
from lightcone.engine.runner import SENTINEL, RuleGateError, run_rule

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _capture(fn) -> tuple[str, BaseException | None]:
    buf = io.StringIO()
    err: BaseException | None = None
    try:
        with redirect_stdout(buf):
            fn()
    except BaseException as e:  # noqa: BLE001 — we want CalledProcessError too
        err = e
    return buf.getvalue(), err


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = make_project(tmp_path / "proj")
    monkeypatch.chdir(p)
    return p


@pytest.fixture(autouse=True)
def _skip_uv_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env check shells out to `uv sync --check` against a real
    project — fixture projects have no materialized .venv, so stub the
    check (its own behaviour is covered by TestGates)."""
    monkeypatch.setattr(runner, "_env_check", lambda root, job: None)


def _cfg(project: Path, output_id: str = "foo", *, shell_command: str = "echo hi") -> dict:
    """Minimal cfg matching what the Snakefile generator writes."""
    return {
        "output_id": output_id,
        "output_type": "data",
        "universe_id": "u1",
        "recipe": "echo hi",
        "shell_command": shell_command,
        "decisions": {},
        "code_version": "abc",
        "env_version": load_environment(project).env_version,
        "git_sha": None,
        "lc_version": "test",
    }


def test_emit_lines_carry_sentinel(project: Path) -> None:
    out_dir = project / "out"
    out_dir.mkdir()
    output, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(project, shell_command="echo hello"),
        )
    )
    assert err is None
    # Every line we emit is sentinel-prefixed and column-0 anchored.
    for line in output.splitlines():
        assert line.startswith(SENTINEL), line
    # And the recipe's stdout reaches us framed.
    body = _strip_ansi("\n".join(line[len(SENTINEL):] for line in output.splitlines()))
    assert "▶ foo" in body
    assert "hello" in body
    assert "✓ foo" in body


def test_failed_recipe_raises_and_emits_cross(project: Path) -> None:
    out_dir = project / "out"
    out_dir.mkdir()
    output, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(project, shell_command="false"),
        )
    )
    assert isinstance(err, subprocess.CalledProcessError)
    body = _strip_ansi("\n".join(line[len(SENTINEL):] for line in output.splitlines()))
    assert "▶ foo" in body
    assert "✗ foo" in body
    assert "exit=1" in body


def test_no_manifest_on_failure(project: Path) -> None:
    """A failing recipe must not leave a manifest behind — it would
    poison ``lc verify``'s chain check by claiming completion of an
    incomplete rule."""
    out_dir = project / "out"
    out_dir.mkdir()
    _, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(project, shell_command="false"),
        )
    )
    assert err is not None
    assert not (out_dir / ".lightcone-manifest.json").exists()


def test_manifest_written_on_success(project: Path) -> None:
    out_dir = project / "out"
    out_dir.mkdir()
    _, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(project, shell_command=f"touch {out_dir}/data.txt"),
        )
    )
    assert err is None
    assert (out_dir / ".lightcone-manifest.json").is_file()


def test_recipe_stdout_and_stderr_both_forwarded(project: Path) -> None:
    out_dir = project / "out"
    out_dir.mkdir()
    output, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(project, shell_command="echo on-stdout; echo on-stderr 1>&2"),
        )
    )
    assert err is None
    assert "on-stdout" in output
    assert "on-stderr" in output


def test_manifest_records_hermeticity_and_attestation(project: Path) -> None:
    """run_rule executes through the boundary and records what actually
    ran: the passthrough boundary attests mechanism none, and the
    worker-side runtime attestation is merged into the manifest."""
    out_dir = project / "out"
    out_dir.mkdir()
    _, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(project, shell_command=f"touch {out_dir}/data.txt"),
        )
    )
    assert err is None
    m = json.loads((out_dir / ".lightcone-manifest.json").read_text())
    assert m["hermeticity"] == {
        "mechanism": "none", "fs": "open", "network": "allowed",
    }
    assert m["platform"]["arch"]
    assert m["python_build"].startswith("CPython")
    assert m["worker_runtime"] == "host"


# ---- the gates -------------------------------------------------------------


class TestGates:
    def test_pre_gate_aborts_on_env_drift(self, project: Path) -> None:
        """A lock edited between generation and execution aborts before
        the recipe runs."""
        out_dir = project / "out"
        out_dir.mkdir()
        cfg = _cfg(project, shell_command=f"touch {out_dir}/ran")
        (project / "uv.lock").write_text(
            (project / "uv.lock").read_text() + "# relock\n"
        )
        output, err = _capture(
            lambda: run_rule(
                rule_key="foo", universe="u1", output_dir=out_dir,
                inputs={}, cfg=cfg,
            )
        )
        assert isinstance(err, RuleGateError)
        assert "environment changed mid-run" in str(err)
        assert not (out_dir / "ran").exists(), "recipe must not have run"
        assert not (out_dir / ".lightcone-manifest.json").exists()

    def test_post_gate_blocks_manifest_on_mid_recipe_relock(
        self, project: Path
    ) -> None:
        """A recipe (or concurrent edit) that changes the lock during
        execution must not get a manifest — the double gate brackets the
        recipe."""
        out_dir = project / "out"
        out_dir.mkdir()
        cfg = _cfg(
            project,
            shell_command=(
                f"touch {out_dir}/data.txt && echo '# drift' >> uv.lock"
            ),
        )
        _, err = _capture(
            lambda: run_rule(
                rule_key="foo", universe="u1", output_dir=out_dir,
                inputs={}, cfg=cfg,
            )
        )
        assert isinstance(err, RuleGateError)
        assert not (out_dir / ".lightcone-manifest.json").exists()

    def test_env_check_runs_uv_sync_check(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 2 (direct): a true no-write env-vs-lock verification."""
        from lightcone.engine.job import RuleJob

        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))

            class R:
                returncode = 0
                stderr = ""
            return R()

        (project / ".venv").mkdir()
        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        job = RuleJob.from_cfg(_cfg(project))
        _real_env_check(project, job)
        assert calls and calls[0][:5] == ["uv", "sync", "--locked", "--exact", "--check"]

    def test_env_check_fails_without_venv(self, project: Path) -> None:
        from lightcone.engine.job import RuleJob

        job = RuleJob.from_cfg(_cfg(project))
        with pytest.raises(RuleGateError, match="never converged"):
            _real_env_check(project, job)


# The autouse fixture stubs runner._env_check; keep a handle to the real
# implementation for the tests that exercise it directly.
_real_env_check = runner._env_check


# ---- offline overlay + sandbox flags ---------------------------------------


def test_recipe_env_has_offline_overlay_and_scrub(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Converge once, then never write: recipes see UV_OFFLINE=1 and
    never the ambient UV_* steering surface."""
    monkeypatch.setenv("UV_INDEX_URL", "https://evil.example/simple")
    out_dir = project / "out"
    out_dir.mkdir()
    output, err = _capture(
        lambda: run_rule(
            rule_key="foo", universe="u1", output_dir=out_dir,
            inputs={},
            cfg=_cfg(project, shell_command="env | sort"),
        )
    )
    assert err is None
    assert "UV_OFFLINE=1" in output
    assert "UV_PYTHON_DOWNLOADS=never" in output
    assert "UV_INDEX_URL" not in output


def test_require_sandbox_refuses_before_exec(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker-side enforcement: with only the passthrough boundary
    available, --require-sandbox must refuse (mechanism is none) and the
    recipe must never run."""
    monkeypatch.setenv(runner.REQUIRE_SANDBOX_ENV, "any")
    out_dir = project / "out"
    out_dir.mkdir()
    _, err = _capture(
        lambda: run_rule(
            rule_key="foo", universe="u1", output_dir=out_dir,
            inputs={},
            cfg=_cfg(project, shell_command=f"touch {out_dir}/ran"),
        )
    )
    assert isinstance(err, RuleGateError)
    assert "--require-sandbox" in str(err)
    assert not (out_dir / "ran").exists()
