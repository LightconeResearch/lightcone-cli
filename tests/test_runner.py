"""Tests for the per-rule run_rule helper.

The helper is invoked from the generated Snakefile's ``run:`` block; we
exercise it directly with a synthetic cfg, capturing stdout to assert on
the sentinel-prefixed framing the executor relies on.
"""
from __future__ import annotations

import io
import re
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

from lightcone.engine.runner import SENTINEL, run_rule

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


def _cfg(output_id: str = "foo", *, shell_command: str = "echo hi") -> dict:
    """Minimal cfg matching what the Snakefile generator writes.

    ``manifest.write_manifest`` reads several keys; we provide the ones
    it touches without standing up a real container/decision pipeline.
    The runner reads ``shell_command`` directly — substitution and
    container wrapping happen at generation time.
    """
    return {
        "output_id": output_id,
        "output_type": "data",
        "universe_id": "u1",
        "recipe": "echo hi",
        "shell_command": shell_command,
        "container_image": None,
        "decisions": {},
        "code_version": "abc",
        "git_sha": None,
        "lc_version": "test",
    }


def test_emit_lines_carry_sentinel(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(shell_command="echo hello"),
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


def test_failed_recipe_raises_and_emits_cross(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(shell_command="false"),
        )
    )
    assert isinstance(err, subprocess.CalledProcessError)
    body = _strip_ansi("\n".join(line[len(SENTINEL):] for line in output.splitlines()))
    assert "▶ foo" in body
    assert "✗ foo" in body
    assert "exit=1" in body


def test_no_manifest_on_failure(tmp_path: Path) -> None:
    """A failing recipe must not leave a manifest behind — it would
    poison ``lc verify``'s chain check by claiming completion of an
    incomplete rule."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(shell_command="false"),
        )
    )
    assert err is not None
    assert not (out_dir / ".lightcone-manifest.json").exists()


def test_manifest_written_on_success(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(shell_command=f"touch {out_dir}/data.txt"),
        )
    )
    assert err is None
    assert (out_dir / ".lightcone-manifest.json").is_file()


def test_recipe_stdout_and_stderr_both_forwarded(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output, err = _capture(
        lambda: run_rule(
            rule_key="foo",
            universe="u1",
            output_dir=out_dir,
            inputs={},
            cfg=_cfg(shell_command="echo on-stdout; echo on-stderr 1>&2"),
        )
    )
    assert err is None
    body = output
    assert "on-stdout" in body
    assert "on-stderr" in body
