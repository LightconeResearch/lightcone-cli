"""Tests for engine/snakefile.py — the Snakefile generator."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from lightcone.engine.snakefile import generate


def _spec(project_root: Path, spec: dict[str, Any]) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "astra.yaml").write_text(yaml.safe_dump(spec))


def test_generate_simple_spec(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {
            "outputs": [
                {"id": "foo", "recipe": {"command": "echo foo"}},
                {
                    "id": "bar",
                    "recipe": {"command": "echo bar", "inputs": ["foo"]},
                },
            ]
        },
    )
    snakefile, _ = generate(tmp_path, universes=["u1"])

    assert (tmp_path / ".lightcone" / "Snakefile").exists()
    assert (tmp_path / ".lightcone" / "snakefile-config.json").exists()

    snake_text = snakefile.read_text()
    assert "rule foo:" in snake_text
    assert "rule bar:" in snake_text
    assert "rule all:" in snake_text
    assert "results/{universe}/foo" in snake_text


def test_generate_universe_expansion(tmp_path: Path) -> None:
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo"}}]})
    _, cfg_path = generate(tmp_path, universes=["u1", "u2"])

    cfg = json.loads(cfg_path.read_text())
    assert "foo" in cfg
    assert set(cfg["foo"].keys()) == {"u1", "u2"}


def test_generate_skips_alias_outputs(tmp_path: Path) -> None:
    """Outputs without a recipe (aliases) are NOT emitted as rules."""
    _spec(
        tmp_path,
        {
            "outputs": [{"id": "alias", "from": "sub.real"}],
            "analyses": {
                "sub": {
                    "outputs": [{"id": "real", "recipe": {"command": "echo"}}],
                }
            },
        },
    )
    snakefile, _ = generate(tmp_path, universes=["u1"])
    text = snakefile.read_text()
    assert "rule sub__real:" in text
    assert "rule alias:" not in text


def test_generate_writes_code_version_per_universe(tmp_path: Path) -> None:
    """code_version is part of the per-(rule, universe) cfg blob, so a
    decision change in one universe doesn't poison another."""
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo"}}]})
    _, cfg_path = generate(tmp_path, universes=["u1", "u2"])
    cfg = json.loads(cfg_path.read_text())
    assert "code_version" in cfg["foo"]["u1"]
    assert "code_version" in cfg["foo"]["u2"]


def test_generate_includes_recipe_in_cfg(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "python script.py --arg 1"}}]},
    )
    _, cfg_path = generate(tmp_path, universes=["u1"])
    cfg = json.loads(cfg_path.read_text())
    # The unwrapped recipe (what the user wrote) is what goes into the
    # manifest's ``recipe`` field. ``shell_command`` is the runtime-wrapped
    # version — runtime="none" here, so they're equal.
    assert cfg["foo"]["u1"]["recipe"] == "python script.py --arg 1"
    assert cfg["foo"]["u1"]["shell_command"] == "python script.py --arg 1"


def test_generate_no_container_directive_emitted(tmp_path: Path) -> None:
    """We own container invocation; the Snakemake ``container:`` directive
    must never be emitted (we don't use --sdm apptainer)."""
    _spec(
        tmp_path,
        {
            "outputs": [
                {
                    "id": "foo",
                    "recipe": {"command": "echo", "container": "python:3.12-slim"},
                }
            ]
        },
    )
    snakefile_path, _ = generate(tmp_path, universes=["u1"], runtime="podman")
    text = snakefile_path.read_text()
    assert "container:" not in text


def test_generate_wraps_recipe_with_runtime(tmp_path: Path) -> None:
    """When a runtime is configured and the recipe has a container, the
    wrapped shell command in cfg invokes the runtime with the image."""
    _spec(
        tmp_path,
        {
            "outputs": [
                {
                    "id": "foo",
                    "recipe": {
                        "command": "echo hi > {output[0]}/data.txt",
                        "container": "python:3.12-slim",
                    },
                }
            ]
        },
    )
    _, cfg_path = generate(tmp_path, universes=["u1"], runtime="podman")
    cfg = json.loads(cfg_path.read_text())
    sh = cfg["foo"]["u1"]["shell_command"]
    assert sh.startswith("podman run --rm")
    assert "python:3.12-slim" in sh
    # Snakemake placeholders survive the wrap so they substitute at exec time.
    assert "{output[0]}" in sh


def test_generate_no_wrap_for_runtime_none(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {
            "outputs": [
                {
                    "id": "foo",
                    "recipe": {"command": "echo hi", "container": "python:3.12-slim"},
                }
            ]
        },
    )
    _, cfg_path = generate(tmp_path, universes=["u1"], runtime="none")
    cfg = json.loads(cfg_path.read_text())
    assert cfg["foo"]["u1"]["shell_command"] == "echo hi"


def test_generated_rules_use_run_block_with_write_manifest(tmp_path: Path) -> None:
    """Each rule body is a ``run:`` block calling shell() and write_manifest().
    No external finalize script — we own this path host-side."""
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo hi"}}]})
    snakefile, _ = generate(tmp_path, universes=["u1"])
    text = snakefile.read_text()
    assert "    run:" in text
    assert "    shell:" not in text
    assert "write_manifest(" in text
    assert "_lc_finalize" not in text


def test_no_finalizer_script_written(tmp_path: Path) -> None:
    """``_lc_finalize.py`` is gone — write_manifest runs on the host."""
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo"}}]})
    generate(tmp_path, universes=["u1"])
    assert not (tmp_path / ".lightcone" / "_lc_finalize.py").exists()


def test_cfg_includes_resolved_input_paths(tmp_path: Path) -> None:
    """Each cfg entry has its inputs resolved per-universe."""
    _spec(
        tmp_path,
        {
            "outputs": [
                {"id": "foo", "recipe": {"command": "echo"}},
                {"id": "bar", "recipe": {"command": "echo", "inputs": ["foo"]}},
            ]
        },
    )
    _, cfg_path = generate(tmp_path, universes=["u1"])
    cfg = json.loads(cfg_path.read_text())
    assert cfg["bar"]["u1"]["inputs"] == {"foo": "results/u1/foo"}


def test_generated_snakefile_parses_with_snakemake(tmp_path: Path) -> None:
    """End-to-end: the generated Snakefile must be valid Snakemake."""
    _spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "echo foo > {output[0]}/data.txt"}}]},
    )
    generate(tmp_path, universes=["u1"])

    import subprocess
    proc = subprocess.run(
        [
            "snakemake",
            "-s",
            str(tmp_path / ".lightcone" / "Snakefile"),
            "-d",
            str(tmp_path),
            "-n",
            "--cores",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"snakemake -n failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
