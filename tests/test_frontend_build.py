"""Packaging must use the checkout's inputs or a complete prebuilt frontend."""

from __future__ import annotations

import os
import runpy
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

_HOOK = runpy.run_path(str(Path(__file__).parents[1] / "hatch_build.py"))["FrontendBuildHook"]


def hook(root: Path) -> Any:
    return _HOOK(str(root), {}, None, None, str(root / "dist"), "wheel")


def prebuilt(root: Path) -> Path:
    static = root / "src/lightcone/app/static"
    static.mkdir(parents=True)
    for name in ("bundle.js", "favicon.svg"):
        (static / name).write_text("prebuilt")
    return static


def test_prebuilt_sdist_never_invokes_npm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prebuilt(tmp_path)
    monkeypatch.setenv("PATH", "")
    hook(tmp_path).initialize("standard", {})


@pytest.mark.parametrize("missing", ["bundle.js", "favicon.svg"])
def test_incomplete_sdist_is_refused(tmp_path: Path, missing: str) -> None:
    (prebuilt(tmp_path) / missing).unlink()
    with pytest.raises(RuntimeError, match=missing):
        hook(tmp_path).initialize("standard", {})


@pytest.mark.parametrize(
    "input_path",
    [
        "src/index.tsx",
        "style/index.css",
        "style/favicon.svg",
        "package.json",
        "package-lock.json",
        "rspack.config.js",
        "tsconfig.json",
    ],
)
def test_checkout_rebuilds_even_with_existing_assets(
    tmp_path: Path, input_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every input, including restored older files, must reach the build."""
    prebuilt(tmp_path)
    source = tmp_path / "ui" / input_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("changed input")
    os.utime(source, (1, 1))
    run = Mock()
    monkeypatch.setattr("subprocess.run", run)
    hook(tmp_path).initialize("standard", {})
    assert [call.args[0] for call in run.call_args_list] == [
        ["npm", "ci"],
        ["npm", "run", "build"],
    ]
    assert all(
        call.kwargs == {"cwd": tmp_path / "ui", "check": True} for call in run.call_args_list
    )


def test_editable_python_install_needs_no_frontend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ui").mkdir()
    monkeypatch.setenv("PATH", "")
    hook(tmp_path).initialize("editable", {})
