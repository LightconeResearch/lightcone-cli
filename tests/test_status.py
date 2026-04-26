"""Tests for engine/status.py — manifest-driven status walker."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lightcone.engine.manifest import code_version, write_manifest
from lightcone.engine.status import OutputStatus, get_output_status


def _write_spec(project_root: Path, spec: dict[str, Any]) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "astra.yaml").write_text(yaml.safe_dump(spec))


def _materialize(
    project_root: Path,
    output_id: str,
    universe_id: str,
    *,
    recipe: str,
    decisions: dict[str, Any] | None = None,
    container_image: str | None = None,
) -> Path:
    out = project_root / "results" / universe_id / output_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.txt").write_text("output bytes")
    cv = code_version(
        recipe=recipe,
        container_image=container_image,
        decisions=decisions or {},
    )
    write_manifest(
        output_dir=out,
        inputs={},
        cfg={
            "output_id": output_id,
            "universe_id": universe_id,
            "recipe": recipe,
            "container_image": container_image,
            "decisions": decisions or {},
            "code_version": cv,
            "git_sha": "abc",
            "lc_version": "0.0",
        },
    )
    return out


def test_status_missing_when_nothing_materialized(tmp_path: Path) -> None:
    _write_spec(
        tmp_path,
        {
            "outputs": [
                {"id": "foo", "recipe": {"command": "echo foo"}},
            ]
        },
    )
    statuses = list(get_output_status(tmp_path, universe_id="u1"))
    assert len(statuses) == 1
    assert statuses[0].output_id == "foo"
    assert statuses[0].status == "missing"


def test_status_ok_when_manifest_matches(tmp_path: Path) -> None:
    _write_spec(
        tmp_path,
        {
            "outputs": [
                {"id": "foo", "recipe": {"command": "echo foo"}},
            ]
        },
    )
    _materialize(tmp_path, "foo", "u1", recipe="echo foo")
    statuses = list(get_output_status(tmp_path, universe_id="u1"))
    assert statuses[0].status == "ok"
    assert statuses[0].manifest is not None


def test_status_stale_when_recipe_changed(tmp_path: Path) -> None:
    _write_spec(
        tmp_path,
        {
            "outputs": [
                {"id": "foo", "recipe": {"command": "echo NEW"}},
            ]
        },
    )
    # Materialize with the OLD recipe
    _materialize(tmp_path, "foo", "u1", recipe="echo old")
    statuses = list(get_output_status(tmp_path, universe_id="u1"))
    assert statuses[0].status == "stale"


def test_status_no_recipe_for_alias(tmp_path: Path) -> None:
    """Outputs declared with `from:` (no recipe) are aliases — they are
    materialized as a side-effect of their upstream and have no own status.
    """
    _write_spec(
        tmp_path,
        {
            "outputs": [
                {"id": "alias_out", "from": "sub.real_out"},
            ],
            "analyses": {
                "sub": {
                    "outputs": [
                        {"id": "real_out", "recipe": {"command": "echo r"}},
                    ]
                }
            },
        },
    )
    statuses = {s.output_id: s for s in get_output_status(tmp_path, universe_id="u1")}
    assert "alias_out" in statuses
    assert statuses["alias_out"].status == "alias"


def test_status_walks_subanalyses(tmp_path: Path) -> None:
    _write_spec(
        tmp_path,
        {
            "outputs": [
                {"id": "root_out", "recipe": {"command": "echo r"}},
            ],
            "analyses": {
                "sub": {
                    "outputs": [
                        {"id": "sub_out", "recipe": {"command": "echo s"}},
                    ]
                }
            },
        },
    )
    statuses = list(get_output_status(tmp_path, universe_id="u1"))
    ids = {s.output_id for s in statuses}
    assert ids == {"root_out", "sub_out"}


def test_status_outputstatus_dataclass(tmp_path: Path) -> None:
    """OutputStatus is the public dataclass; check its shape."""
    _write_spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "echo foo"}}]},
    )
    _materialize(tmp_path, "foo", "u1", recipe="echo foo")
    [s] = get_output_status(tmp_path, universe_id="u1")
    assert isinstance(s, OutputStatus)
    assert s.output_id == "foo"
    assert s.universe_id == "u1"
    assert s.status == "ok"
    assert s.output_dir.exists()


def test_status_universe_specific(tmp_path: Path) -> None:
    """Asking for one universe must not pick up materializations from another."""
    _write_spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "echo foo"}}]},
    )
    _materialize(tmp_path, "foo", "u1", recipe="echo foo")
    statuses = list(get_output_status(tmp_path, universe_id="u2"))
    assert statuses[0].status == "missing"
