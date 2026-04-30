"""Tests for engine/snakefile.py — the Snakefile generator."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from lightcone.engine.snakefile import generate, render_recipe


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
                    "inputs": ["foo"],
                    "recipe": {"command": "echo bar"},
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
    # The raw recipe template (what the user wrote) is preserved so
    # the manifest can record it. ``shell_command`` is the rendered +
    # runtime-wrapped version, prefixed with a no-op carrying the
    # code_version so drift is visible at the shell level.
    assert cfg["foo"]["u1"]["recipe"] == "python script.py --arg 1"
    sh = cfg["foo"]["u1"]["shell_command"]
    assert "python script.py --arg 1" in sh
    assert f"lc_code_version={cfg['foo']['u1']['code_version']}" in sh


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
    wrapped shell command in cfg invokes the runtime with the image —
    and the v0.0.7 ``{output}`` placeholder has been substituted to a
    concrete per-universe path before the wrap."""
    _spec(
        tmp_path,
        {
            "outputs": [
                {
                    "id": "foo",
                    "recipe": {
                        "command": "echo hi > {output}/data.txt",
                        "container": "python:3.12-slim",
                    },
                }
            ]
        },
    )
    _, cfg_path = generate(tmp_path, universes=["u1"], runtime="podman")
    cfg = json.loads(cfg_path.read_text())
    sh = cfg["foo"]["u1"]["shell_command"]
    assert "podman run --rm" in sh
    assert "python:3.12-slim" in sh
    # ``{output}`` is rendered at gen time to the concrete per-universe
    # path; no placeholder survives the wrap.
    assert "results/u1/foo/data.txt" in sh
    assert "{output}" not in sh
    # The code_version breadcrumb is prefixed onto the wrapped command.
    assert f"lc_code_version={cfg['foo']['u1']['code_version']}" in sh


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
    sh = cfg["foo"]["u1"]["shell_command"]
    assert sh.endswith("echo hi")
    assert f"lc_code_version={cfg['foo']['u1']['code_version']}" in sh


def test_generated_rules_delegate_to_run_rule(tmp_path: Path) -> None:
    """Each rule body is a ``run:`` block that calls ``run_rule()``.

    The recipe execution, manifest write, and validation hook all live
    inside :func:`lightcone.engine.runner.run_rule` — keeping the
    generated Snakefile slim and behaviour in Python rather than in
    shell strings.
    """
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo hi"}}]})
    snakefile, _ = generate(tmp_path, universes=["u1"])
    text = snakefile.read_text()
    assert "    run:" in text
    assert "    shell:" not in text
    assert "run_rule(" in text
    assert "from lightcone.engine.runner import run_rule" in text
    # Direct shell()/write_manifest()/validate_output() calls in the
    # generated body would mean we're double-executing or bypassing
    # run_rule's lockable output frame.
    assert "        shell(" not in text
    assert "        write_manifest(" not in text
    assert "_lc_finalize" not in text


def test_no_finalizer_script_written(tmp_path: Path) -> None:
    """``_lc_finalize.py`` is gone — write_manifest runs on the host."""
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo"}}]})
    generate(tmp_path, universes=["u1"])
    assert not (tmp_path / ".lightcone" / "_lc_finalize.py").exists()


def test_cfg_substitutes_inputs_per_universe(tmp_path: Path) -> None:
    """``{inputs.<id>}`` and ``{output}`` are substituted at gen time
    to concrete per-universe paths; sibling output paths track the
    universe wildcard so a ``u1`` rule can never reference ``u2`` data."""
    _spec(
        tmp_path,
        {
            "outputs": [
                {"id": "foo", "recipe": {"command": "echo > {output}/data.txt"}},
                {
                    "id": "bar",
                    "inputs": ["foo"],
                    "recipe": {"command": "cat {inputs.foo}/data.txt > {output}/out.txt"},
                },
            ]
        },
    )
    _, cfg_path = generate(tmp_path, universes=["u1", "u2"])
    cfg = json.loads(cfg_path.read_text())
    sh_u1 = cfg["bar"]["u1"]["shell_command"]
    sh_u2 = cfg["bar"]["u2"]["shell_command"]
    assert "results/u1/foo/data.txt" in sh_u1
    assert "results/u1/bar/out.txt" in sh_u1
    assert "results/u2/foo/data.txt" in sh_u2
    assert "results/u2/bar/out.txt" in sh_u2


def test_recipe_edit_changes_params_for_rerun_trigger(tmp_path: Path) -> None:
    """Editing a recipe must change ``params.cfg`` (which carries
    ``code_version`` and ``shell_command``) so Snakemake's ``params``
    rerun-trigger fires. The rule body source itself does NOT change —
    only ``params`` — which is why ``lc run`` defaults to including
    ``params`` in ``--rerun-triggers``.
    """
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo v1"}}]})
    snakefile_v1, cfg_path_v1 = generate(tmp_path, universes=["u1"])
    body_v1 = snakefile_v1.read_text()
    cfg_v1 = json.loads(cfg_path_v1.read_text())["foo"]["u1"]

    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo v2"}}]})
    snakefile_v2, cfg_path_v2 = generate(tmp_path, universes=["u1"])
    body_v2 = snakefile_v2.read_text()
    cfg_v2 = json.loads(cfg_path_v2.read_text())["foo"]["u1"]

    assert body_v1 == body_v2, (
        "Rule body is universe-parameterized and must not change on a "
        "recipe edit — that is the whole reason we rely on the params trigger."
    )
    assert cfg_v1["code_version"] != cfg_v2["code_version"]
    assert cfg_v1["shell_command"] != cfg_v2["shell_command"]


def test_containerfile_edit_changes_code_version(tmp_path: Path) -> None:
    """Editing a Containerfile changes ``code_version`` so ``lc status``
    reports stale and the manifest records the image content faithfully.
    """
    containerfile = tmp_path / "Containerfile"
    containerfile.write_text("FROM python:3.12-slim\n")
    _spec(
        tmp_path,
        {
            "outputs": [
                {
                    "id": "foo",
                    "recipe": {"command": "echo", "container": "Containerfile"},
                }
            ]
        },
    )
    _, cfg_path_v1 = generate(tmp_path, universes=["u1"], runtime="podman")
    cv_v1 = json.loads(cfg_path_v1.read_text())["foo"]["u1"]["code_version"]

    containerfile.write_text("FROM python:3.12-slim\nRUN pip install numpy\n")
    _, cfg_path_v2 = generate(tmp_path, universes=["u1"], runtime="podman")
    cv_v2 = json.loads(cfg_path_v2.read_text())["foo"]["u1"]["code_version"]

    assert cv_v1 != cv_v2, (
        "code_version must change when the Containerfile contents change "
        "so that lc status correctly reports stale."
    )


def test_validation_runs_via_run_rule(tmp_path: Path) -> None:
    """Validation now runs inside ``run_rule()`` rather than inline in
    the generated Snakefile. We just check that the runner is imported
    — the runner's own tests cover the validation-on-success path."""
    _spec(tmp_path, {"outputs": [{"id": "foo", "recipe": {"command": "echo"}}]})
    snakefile, _ = generate(tmp_path, universes=["u1"])
    text = snakefile.read_text()
    assert "from lightcone.engine.runner import run_rule" in text


def test_cfg_includes_output_type(tmp_path: Path) -> None:
    """Validation needs the declared output type — pass it through cfg."""
    _spec(
        tmp_path,
        {
            "outputs": [
                {"id": "foo", "type": "metric", "recipe": {"command": "echo"}},
            ]
        },
    )
    _, cfg_path = generate(tmp_path, universes=["u1"])
    cfg = json.loads(cfg_path.read_text())
    assert cfg["foo"]["u1"]["output_type"] == "metric"


def test_generated_snakefile_parses_with_snakemake(tmp_path: Path) -> None:
    """End-to-end: the generated Snakefile must be valid Snakemake.

    Recipe uses the v0.0.7 ``{output}`` placeholder, which is rendered
    to a concrete path by the generator — Snakemake never sees a
    placeholder. Any leftover Snakemake-style ``{output[0]}`` would
    have been a substitution failure inside ``render_recipe``.
    """
    _spec(
        tmp_path,
        {"outputs": [{"id": "foo", "recipe": {"command": "echo foo > {output}/data.txt"}}]},
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


# ============================================================================
# Unit tests for render_recipe — the v0.0.7 template substitution function
# ============================================================================


def test_render_substitutes_output() -> None:
    out = render_recipe(
        'python s.py --out {output}',
        inputs={},
        decisions={},
        output='results/u1/foo',
    )
    assert out == 'python s.py --out results/u1/foo'


def test_render_substitutes_named_input() -> None:
    out = render_recipe(
        'cat {inputs.upstream}',
        inputs={'upstream': 'results/u1/upstream'},
        decisions={},
        output='results/u1/foo',
    )
    assert out == 'cat results/u1/upstream'


def test_render_substitutes_decisions() -> None:
    out = render_recipe(
        'python s.py --scaling {decisions.scaling} --seed {decisions.seed}',
        inputs={},
        decisions={'scaling': 'standard', 'seed': '42'},
        output='out',
    )
    assert out == 'python s.py --scaling standard --seed 42'


def test_render_inputs_joined_in_declaration_order() -> None:
    out = render_recipe(
        'merge {inputs} > {output}/merged',
        inputs={'a': '/p/a', 'b': '/p/b', 'c': '/p/c'},
        decisions={},
        output='/p/out',
    )
    assert out == 'merge /p/a /p/b /p/c > /p/out/merged'


def test_render_handles_brace_escapes() -> None:
    out = render_recipe(
        'awk \'{{print $1}}\' {inputs.x}',
        inputs={'x': '/p/x'},
        decisions={},
        output='out',
    )
    assert out == "awk '{print $1}' /p/x"


def test_render_rejects_undeclared_input() -> None:
    with pytest.raises(KeyError, match='not declared'):
        render_recipe('cat {inputs.missing}', inputs={}, decisions={}, output='out')


def test_render_rejects_undeclared_decision() -> None:
    with pytest.raises(KeyError, match='not declared'):
        render_recipe(
            'python s.py --x {decisions.missing}', inputs={}, decisions={}, output='out'
        )


def test_render_rejects_unknown_namespace() -> None:
    with pytest.raises(ValueError, match='Unknown'):
        render_recipe('echo {wildcards.universe}', inputs={}, decisions={}, output='out')


def test_render_rejects_format_spec() -> None:
    with pytest.raises(ValueError, match='format'):
        render_recipe('echo {output:s}', inputs={}, decisions={}, output='out')


def test_generate_substitutes_decisions_into_shell_command(tmp_path: Path) -> None:
    """Output.decisions resolves through the universe to actual option
    IDs that get substituted into {decisions.<id>} placeholders in the
    rendered shell command."""
    _spec(
        tmp_path,
        {
            'outputs': [
                {
                    'id': 'foo',
                    'decisions': ['scaling'],
                    'recipe': {'command': 'python s.py --scaling {decisions.scaling}'},
                }
            ],
            'decisions': {
                'scaling': {
                    'label': 'scaling',
                    'default': 'standard',
                    'options': {'standard': {'label': 'std'}, 'minmax': {'label': 'mm'}},
                }
            },
        },
    )
    (tmp_path / 'universes').mkdir(exist_ok=True)
    (tmp_path / 'universes' / 'u1.yaml').write_text('decisions:\n  scaling: minmax\n')
    _, cfg_path = generate(tmp_path, universes=['u1'])
    cfg = json.loads(cfg_path.read_text())
    assert '--scaling minmax' in cfg['foo']['u1']['shell_command']
    assert cfg['foo']['u1']['decisions'] == {'scaling': 'minmax'}


def test_qualified_input_uses_raw_id_in_run_rule_call(tmp_path: Path) -> None:
    """Sub-analysis output references like 'sub.real' reach run_rule's
    inputs dict with their dots intact, so write_manifest's
    input_versions matches what verify walks. The Snakemake input slot
    itself uses the safe key 'sub__real' (must be a Python identifier)."""
    _spec(
        tmp_path,
        {
            'outputs': [
                {
                    'id': 'downstream',
                    'inputs': ['sub.real'],
                    'recipe': {'command': 'cat {inputs.sub.real}'},
                }
            ],
            'analyses': {
                'sub': {
                    'outputs': [{'id': 'real', 'recipe': {'command': 'echo r'}}],
                }
            },
        },
    )
    snakefile, _ = generate(tmp_path, universes=['u1'])
    text = snakefile.read_text()
    # Snakemake input directive uses the safe key.
    assert 'sub__real=' in text
    # The run_rule(inputs=...) dict literal uses the raw id.
    assert '"sub.real": Path(input.sub__real)' in text

