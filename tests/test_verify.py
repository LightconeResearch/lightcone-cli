"""Tests for engine/verify.py — the integrity checker."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lightcone.engine.manifest import (
    MANIFEST_FILENAME,
    code_version,
    write_manifest,
)
from lightcone.engine.verify import VerifyResult, verify_outputs


def _spec(project_root: Path, spec: dict[str, Any]) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "astra.yaml").write_text(yaml.safe_dump(spec))


def _materialize(
    project_root: Path,
    output_id: str,
    universe_id: str,
    *,
    recipe: str = "echo hi",
    inputs: dict[str, Path] | None = None,
) -> Path:
    out = project_root / "results" / universe_id / output_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.txt").write_text(f"output of {output_id}")
    write_manifest(
        output_dir=out,
        inputs=inputs or {},
        cfg={
            "output_id": output_id,
            "universe_id": universe_id,
            "recipe": recipe,
            "container_image": None,
            "decisions": {},
            "code_version": code_version(
                recipe=recipe, container_image=None, decisions={}
            ),
            "git_sha": "g",
            "lc_version": "0.0",
        },
    )
    return out


def test_verify_clean_chain_passes(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {
            "outputs": [
                {"id": "upstream", "recipe": {"command": "echo u"}},
                {
                    "id": "downstream",
                    "inputs": ["upstream"],
                    "recipe": {"command": "echo d"},
                },
            ]
        },
    )
    up = _materialize(tmp_path, "upstream", "u1", recipe="echo u")
    _materialize(tmp_path, "downstream", "u1", recipe="echo d", inputs={"upstream": up})

    results = list(verify_outputs(tmp_path, universe_id="u1"))
    assert all(r.passed for r in results), [r for r in results if not r.passed]
    assert {r.output_id for r in results} == {"upstream", "downstream"}


def test_verify_detects_tampered_data(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "echo f"}}]},
    )
    out = _materialize(tmp_path, "foo", "u1", recipe="echo f")
    # Tamper with the output AFTER the manifest was written.
    (out / "data.txt").write_text("agent forged this")
    [r] = list(verify_outputs(tmp_path, universe_id="u1"))
    assert not r.passed
    assert r.failure == "tampered_data"


def test_verify_detects_broken_chain(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {
            "outputs": [
                {"id": "upstream", "recipe": {"command": "echo u"}},
                {
                    "id": "downstream",
                    "inputs": ["upstream"],
                    "recipe": {"command": "echo d"},
                },
            ]
        },
    )
    up = _materialize(tmp_path, "upstream", "u1", recipe="echo u")
    _materialize(tmp_path, "downstream", "u1", recipe="echo d", inputs={"upstream": up})

    # Re-materialize upstream (gives it a NEW data_version) without
    # re-running downstream. Downstream's recorded input_version no longer
    # matches upstream's current data_version → broken chain.
    (up / "data.txt").write_text("upstream changed")
    write_manifest(
        output_dir=up,
        inputs={},
        cfg={
            "output_id": "upstream",
            "universe_id": "u1",
            "recipe": "echo u",
            "container_image": None,
            "decisions": {},
            "code_version": code_version(
                recipe="echo u", container_image=None, decisions={}
            ),
            "git_sha": "g",
            "lc_version": "0.0",
        },
    )

    results = {r.output_id: r for r in verify_outputs(tmp_path, universe_id="u1")}
    assert results["upstream"].passed
    assert not results["downstream"].passed
    assert results["downstream"].failure == "broken_chain"


def test_verify_detects_missing_manifest(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "echo f"}}]},
    )
    # Drop a fake output without going through write_manifest. This is the
    # agent-forged-file scenario.
    out = tmp_path / "results" / "u1" / "foo"
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.txt").write_text("forged")

    [r] = list(verify_outputs(tmp_path, universe_id="u1"))
    assert not r.passed
    assert r.failure == "missing_manifest"


def test_verify_detects_corrupt_manifest(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "echo f"}}]},
    )
    out = _materialize(tmp_path, "foo", "u1", recipe="echo f")
    (out / MANIFEST_FILENAME).write_text("not json")
    [r] = list(verify_outputs(tmp_path, universe_id="u1"))
    assert not r.passed
    assert r.failure == "missing_manifest"


def test_verify_skips_aliases(tmp_path: Path) -> None:
    """Outputs declared with `from:` (no recipe) are aliases. They have no
    own materialization, so verify must not flag them as failures."""
    _spec(
        tmp_path,
        {
            "outputs": [{"id": "alias", "from": "sub.real"}],
            "analyses": {
                "sub": {"outputs": [{"id": "real", "recipe": {"command": "echo r"}}]}
            },
        },
    )
    _materialize(tmp_path, "real", "u1", recipe="echo r")
    results = list(verify_outputs(tmp_path, universe_id="u1"))
    ids = {r.output_id for r in results}
    # Only the real output is verified; aliases are skipped.
    assert "real" in ids
    assert "alias" not in ids


def test_verify_detects_broken_chain_for_qualified_input(tmp_path: Path) -> None:
    """``Output.inputs`` referencing a sub-analysis output by qualified
    id (``sub.real``) must resolve through to the producing manifest so
    a drifted upstream surfaces as ``broken_chain``.
    """
    sub_dir = tmp_path / "sub"
    (sub_dir / "results" / "u1" / "real").mkdir(parents=True, exist_ok=True)
    _spec(
        tmp_path,
        {
            "outputs": [
                {
                    "id": "downstream",
                    "inputs": ["sub.real"],
                    "recipe": {"command": "echo d"},
                }
            ],
            "analyses": {
                "sub": {
                    "path": "./sub",
                    "outputs": [{"id": "real", "recipe": {"command": "echo r"}}],
                }
            },
        },
    )

    # Materialize the upstream sub-analysis output at its real on-disk path.
    up = sub_dir / "results" / "u1" / "real"
    (up / "data.txt").write_text("upstream v1")
    write_manifest(
        output_dir=up,
        inputs={},
        cfg={
            "output_id": "real",
            "universe_id": "u1",
            "recipe": "echo r",
            "container_image": None,
            "decisions": {},
            "code_version": code_version(
                recipe="echo r", container_image=None, decisions={}
            ),
            "git_sha": "g",
            "lc_version": "0.0",
        },
    )
    # Materialize downstream; its recorded input_version chains to up.
    _materialize(
        tmp_path, "downstream", "u1", recipe="echo d", inputs={"sub.real": up}
    )

    # Now mutate the upstream and rewrite its manifest so its
    # ``data_version`` drifts. Downstream's chain must detect this.
    (up / "data.txt").write_text("upstream v2")
    write_manifest(
        output_dir=up,
        inputs={},
        cfg={
            "output_id": "real",
            "universe_id": "u1",
            "recipe": "echo r",
            "container_image": None,
            "decisions": {},
            "code_version": code_version(
                recipe="echo r", container_image=None, decisions={}
            ),
            "git_sha": "g",
            "lc_version": "0.0",
        },
    )

    results = {r.output_id: r for r in verify_outputs(tmp_path, universe_id="u1")}
    assert not results["downstream"].passed
    assert results["downstream"].failure == "broken_chain", (
        "qualified-id (sub.real) inputs must be resolved through the "
        "tree, not silently treated as external."
    )


def test_verifyresult_dataclass(tmp_path: Path) -> None:
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo f"}}]})
    _materialize(tmp_path, "foo", "u1", recipe="echo f")
    [r] = list(verify_outputs(tmp_path, universe_id="u1"))
    assert isinstance(r, VerifyResult)
    assert r.output_id == "foo"
    assert r.passed
    assert r.failure is None
