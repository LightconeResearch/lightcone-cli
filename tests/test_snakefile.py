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
    snakefile, cfg = generate(tmp_path, universes=["u1"])[:2]

    assert (tmp_path / ".lightcone" / "Snakefile").exists()
    assert (tmp_path / ".lightcone" / "snakefile-config.json").exists()

    snake_text = snakefile.read_text()
    # Both rules emitted
    assert "rule foo:" in snake_text
    assert "rule bar:" in snake_text
    # rule all aggregates manifest paths for every (universe, output)
    assert "rule all:" in snake_text
    # bar declares foo as an input
    assert "results/{universe}/foo" in snake_text


def test_generate_universe_expansion(tmp_path: Path) -> None:
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo"}}]})
    _, cfg_path = generate(tmp_path, universes=["u1", "u2"])[:2]

    cfg = json.loads(cfg_path.read_text())
    # cfg has a per-rule entry indexed by universe
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
    snakefile, _ = generate(tmp_path, universes=["u1"])[:2]
    text = snakefile.read_text()
    # Sub-analysis output -> rule prefixed with analysis id (avoids collisions)
    assert "rule sub__real:" in text
    assert "rule alias:" not in text


def test_generate_writes_code_version_per_universe(tmp_path: Path) -> None:
    """code_version is part of the per-(rule, universe) cfg blob, so a
    decision change in one universe doesn't poison another."""
    _spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "echo"}}]},
    )
    _, cfg_path = generate(tmp_path, universes=["u1", "u2"])[:2]
    cfg = json.loads(cfg_path.read_text())
    assert "code_version" in cfg["foo"]["u1"]
    assert "code_version" in cfg["foo"]["u2"]


def test_generate_includes_recipe_in_cfg(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "python script.py --arg 1"}}]},
    )
    _, cfg_path = generate(tmp_path, universes=["u1"])[:2]
    cfg = json.loads(cfg_path.read_text())
    assert cfg["foo"]["u1"]["recipe"] == "python script.py --arg 1"


def test_generate_emits_container_directive_for_registry_image(
    tmp_path: Path,
) -> None:
    """A spec like ``container: python:3.12-slim`` must produce
    ``container: "docker://python:3.12-slim"`` in the generated Snakefile,
    plus signal uses_containers=True so lc run knows to pass --sdm apptainer.
    Regression for the bug where container_image was hashed into code_version
    but never wired into the rule body."""
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
    snakefile_path, _, uses_containers = generate(tmp_path, universes=["u1"])
    text = snakefile_path.read_text()
    assert 'container: "docker://python:3.12-slim"' in text
    assert uses_containers is True


def test_generate_no_container_directive_when_recipe_has_none(
    tmp_path: Path,
) -> None:
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo"}}]})
    snakefile_path, _, uses_containers = generate(tmp_path, universes=["u1"])
    text = snakefile_path.read_text()
    assert "container:" not in text
    assert uses_containers is False


def test_generate_passes_through_already_schemed_uri(tmp_path: Path) -> None:
    _spec(
        tmp_path,
        {
            "outputs": [
                {
                    "id": "foo",
                    "recipe": {
                        "command": "echo",
                        "container": "docker://library/python:3.11",
                    },
                }
            ]
        },
    )
    snakefile_path, _, _ = generate(tmp_path, universes=["u1"])
    text = snakefile_path.read_text()
    assert 'container: "docker://library/python:3.11"' in text


def test_generated_snakefile_parses_with_snakemake(tmp_path: Path) -> None:
    """End-to-end: the generated Snakefile must be syntactically valid
    Snakemake. We use ``snakemake -n -s ...`` (dry run)."""
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
            "-n",  # dry run
            "--cores",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    # Dry run should succeed (exit 0) and report what it would do.
    assert proc.returncode == 0, (
        f"snakemake -n failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
