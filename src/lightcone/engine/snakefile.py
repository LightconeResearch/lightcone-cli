"""Generate ``.lightcone/Snakefile`` from ``astra.yaml``.

The Snakefile is a thin shell over the astra spec: one rule per output
with a recipe, parameterized by ``{universe}``. Each rule's body is a
``run:`` block:

    shell(params.cfg["shell_command"])
    write_manifest(output_dir=Path(output.data), inputs={...}, cfg=params.cfg)

The ``shell_command`` is the recipe pre-wrapped at generation time —
when a container is configured, the wrap is something like
``podman run --rm -v "$PWD":"$PWD" -w "$PWD" <image> bash -c '<recipe>'``.
We deliberately do **not** use Snakemake's ``container:`` directive or
``--sdm apptainer``; we own the runtime end-to-end. See
:mod:`lightcone.engine.container`.

After the recipe shell exits, the host calls ``write_manifest`` directly.
The ``os.replace`` rename inside ``write_manifest`` is the atomic commit
point — either the rule produced both data and manifest or it failed and
Snakemake reruns the rule.

All per-(rule, universe) details — recipe, container image, decisions,
precomputed code_version, resolved input paths — live in
``.lightcone/snakefile-config.json`` keyed by ``(rule_key, universe)``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from astra.helpers import get_inputs, load_yaml, resolve_analysis_tree

from lightcone.engine.container import resolve_image_for_run, wrap_recipe
from lightcone.engine.manifest import code_version
from lightcone.engine.tree import (
    TreeOutput,
    collect_tree_outputs,
    resolve_universe_decisions,
)

LIGHTCONE_DIR = ".lightcone"
SNAKEFILE_NAME = "Snakefile"
CONFIG_NAME = "snakefile-config.json"


def _git_sha(project_path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(project_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except FileNotFoundError:
        pass
    return None


def _lc_version() -> str:
    try:
        from importlib.metadata import version

        return version("lightcone-cli")
    except Exception:
        return "unknown"


def _resolve_container_for(
    tree_out: TreeOutput,
    root_spec: dict[str, Any],
) -> str | None:
    """Spec-string of the container that applies to this output.

    Recipe-level beats sub-analysis-level beats root-level. We use the
    raw spec string (Containerfile path or image tag) as the identity —
    the same string any future build will hash to the same image.
    """
    recipe = tree_out.output_def.get("recipe") or {}
    if "container" in recipe:
        return recipe["container"]  # type: ignore[no-any-return]
    if tree_out.analysis_id is not None:
        sub = tree_out.analysis_spec.get("container")
        if sub is not None:
            return sub  # type: ignore[no-any-return]
    return root_spec.get("container")


def _input_path_for(
    tree_out: TreeOutput,
    inp_id: str,
    spec: dict[str, Any],
    output_dirs: dict[str, str],
) -> str | None:
    """For a recipe input id, return the wildcard path to the upstream
    output's directory, or ``None`` if the input refers to an external
    file (handled separately).
    """
    if "." in inp_id:
        return output_dirs.get(inp_id)

    if tree_out.analysis_id is not None:
        qualified = f"{tree_out.analysis_id}.{inp_id}"
        if qualified in output_dirs:
            return output_dirs[qualified]
    if inp_id in output_dirs:
        return output_dirs[inp_id]

    analysis_inputs = {i.get("id"): i for i in get_inputs(tree_out.analysis_spec)}
    inp_def = analysis_inputs.get(inp_id)
    if inp_def and inp_def.get("from"):
        from_ref = inp_def["from"].removeprefix("../").removeprefix("/")
        if from_ref in output_dirs:
            return output_dirs[from_ref]
        if "." in from_ref and from_ref in output_dirs:
            return output_dirs[from_ref]

    return None


def _output_dir_pattern(tree_out: TreeOutput) -> str:
    """Wildcard path to this output's directory.

    Root + inline sub-analyses: ``results/{universe}/<output_id>``
    Path-rooted sub-analyses: ``<sub_path>/results/{universe}/<output_id>``
    """
    if tree_out.analysis_path:
        base = tree_out.analysis_path.lstrip("./")
        return f"{base}/results/{{universe}}/{tree_out.output_id}"
    return f"results/{{universe}}/{tree_out.output_id}"


def _rule_key(tree_out: TreeOutput) -> str:
    """Unique key into the cfg JSON. Avoids collisions when two
    sub-analyses share an output_id."""
    if tree_out.analysis_id is None:
        return tree_out.output_id
    return f"{tree_out.analysis_id}.{tree_out.output_id}"


def _rule_name(tree_out: TreeOutput) -> str:
    """Snakemake rule name. Mirrors the cfg key but with
    Snakemake-friendly identifier characters (``.`` → ``__``)."""
    return _rule_key(tree_out).replace(".", "__")


def _decisions_for_output(
    tree_out: TreeOutput,
    universe_id: str,
    project_path: Path,
    spec: dict[str, Any],
) -> dict[str, Any]:
    universe_yaml = project_path / "universes" / f"{universe_id}.yaml"
    if not universe_yaml.exists():
        return {}
    try:
        return resolve_universe_decisions(project_path, spec, universe_id)
    except (FileNotFoundError, KeyError):
        return {}


def _render_snakefile(
    rules: list[dict[str, Any]],
    universes: list[str],
) -> str:
    """Render the Snakefile string from rule descriptors.

    Each rule descriptor has: ``name`` (Snakemake-safe), ``key`` (cfg
    lookup), ``output_dir`` (wildcard pattern), ``inputs`` (dict of
    declared input id → wildcard path, only for sibling outputs).

    Rule body is a ``run:`` block that calls ``shell()`` on the
    pre-wrapped command and then ``write_manifest()`` host-side.
    """
    universes_repr = repr(universes)
    rule_all_inputs = []
    for r in rules:
        rule_all_inputs.append(
            f'        expand("{r["output_dir"]}/.lightcone-manifest.json", '
            f"universe=UNIVERSES),"
        )
    rule_all_block = "\n".join(rule_all_inputs) or "        []"

    lines: list[str] = []
    lines.append('"""Auto-generated from astra.yaml — do not edit by hand."""')
    lines.append("import json")
    lines.append("from pathlib import Path")
    lines.append("from lightcone.engine.manifest import write_manifest")
    lines.append("")
    lines.append("PROJECT = Path(workflow.basedir).parent")
    lines.append(
        'CFG = json.loads((PROJECT / ".lightcone" / "snakefile-config.json").read_text())'
    )
    lines.append(f"UNIVERSES = {universes_repr}")
    lines.append("")
    lines.append("rule all:")
    lines.append("    input:")
    lines.append(rule_all_block)
    lines.append("")

    for r in rules:
        lines.append(f'rule {r["name"]}:')
        if r["inputs"]:
            lines.append("    input:")
            for k, v in r["inputs"].items():
                lines.append(f'        {k}="{v}",')
        lines.append("    output:")
        lines.append(f'        data=directory("{r["output_dir"]}"),')
        lines.append(f'        manifest="{r["output_dir"]}/.lightcone-manifest.json",')
        lines.append("    params:")
        lines.append(f'        cfg=lambda wc: CFG["{r["key"]}"][wc.universe],')
        lines.append("    run:")
        # User-facing progress marker — printed to stderr at the start of
        # each rule. Picked up by ``lc run`` so the user sees the rule key
        # and universe rather than snakemake-isms.
        lines.append(
            f'        shell(\'printf "\\033[2m▶\\033[0m {r["key"]} '
            f'\\033[2m[%s]\\033[0m\\n" "{{wildcards.universe}}" >&2\')'
        )
        lines.append('        shell(params.cfg["shell_command"])')
        if r["inputs"]:
            inp_pairs = ", ".join(
                f'"{k}": Path(input.{k})' for k in r["inputs"]
            )
            lines.append("        write_manifest(")
            lines.append("            output_dir=Path(output.data),")
            lines.append(f"            inputs={{{inp_pairs}}},")
            lines.append("            cfg=params.cfg,")
            lines.append("        )")
        else:
            lines.append("        write_manifest(")
            lines.append("            output_dir=Path(output.data),")
            lines.append("            inputs={},")
            lines.append("            cfg=params.cfg,")
            lines.append("        )")
        lines.append("")

    return "\n".join(lines) + "\n"


def generate(
    project_path: Path,
    *,
    universes: list[str],
    runtime: str = "none",
) -> tuple[Path, Path]:
    """Write ``.lightcone/Snakefile`` and ``.lightcone/snakefile-config.json``.

    Args:
        project_path: Project root containing ``astra.yaml``.
        universes: Universe ids to expand rules over.
        runtime: Container runtime to wrap recipes with. One of
            ``docker | podman | podman-hpc | none``. ``none`` runs
            recipes on the host without isolation. Resolution is done
            here once, not per-rule, so all rules use a consistent
            runtime. See :func:`lightcone.engine.container.load_runtime`.

    Returns ``(snakefile_path, config_path)``.
    """
    project_path = Path(project_path).resolve()
    spec = resolve_analysis_tree(load_yaml(project_path / "astra.yaml"), project_path)
    project_name = (spec.get("name") or project_path.name).lower().replace(" ", "-")

    tree_outputs = collect_tree_outputs(spec)
    output_dirs: dict[str, str] = {}
    for to in tree_outputs:
        if to.output_def.get("recipe") is None:
            continue
        output_dirs[_rule_key(to)] = _output_dir_pattern(to)

    rules: list[dict[str, Any]] = []
    cfg: dict[str, dict[str, dict[str, Any]]] = {}

    git_sha = _git_sha(project_path)
    lc_version = _lc_version()

    for to in tree_outputs:
        recipe = to.output_def.get("recipe")
        if recipe is None:
            continue  # alias output

        rule_key = _rule_key(to)
        rule_name = _rule_name(to)
        out_dir_pattern = _output_dir_pattern(to)
        recipe_inputs = recipe.get("inputs") or []
        recipe_command = recipe.get("command", "")

        rule_inputs: dict[str, str] = {}
        for inp_id in recipe_inputs:
            up = _input_path_for(to, inp_id, spec, output_dirs)
            if up is not None:
                # Snakemake input dict keys must be valid identifiers.
                key = inp_id.replace(".", "__")
                rule_inputs[key] = up

        # Container resolution: raw spec_str feeds code_version (so an
        # edit to astra.yaml changes the hash); image_tag is what the
        # runtime invokes. Both are computed here so the Snakefile body
        # itself stays runtime-agnostic.
        container_image = _resolve_container_for(to, spec)
        image_tag = resolve_image_for_run(
            container_image, project_path=project_path, project_name=project_name
        )

        rules.append(
            {
                "name": rule_name,
                "key": rule_key,
                "output_dir": out_dir_pattern,
                "inputs": rule_inputs,
            }
        )

        cfg.setdefault(rule_key, {})
        for u in universes:
            decisions = _decisions_for_output(to, u, project_path, spec)
            cv = code_version(
                recipe=recipe_command,
                container_image=container_image,
                decisions=decisions,
            )
            shell_command = wrap_recipe(
                recipe_command, image=image_tag, runtime=runtime
            )
            resolved_inputs = {
                k: v.replace("{universe}", u) for k, v in rule_inputs.items()
            }
            cfg[rule_key][u] = {
                "output_id": to.output_id,
                "universe_id": u,
                "recipe": recipe_command,
                "shell_command": shell_command,
                "container_image": container_image,
                "decisions": decisions,
                "code_version": cv,
                "git_sha": git_sha,
                "lc_version": lc_version,
                "inputs": resolved_inputs,
            }

    lightcone_dir = project_path / LIGHTCONE_DIR
    lightcone_dir.mkdir(parents=True, exist_ok=True)
    snakefile_path = lightcone_dir / SNAKEFILE_NAME
    config_path = lightcone_dir / CONFIG_NAME

    snakefile_path.write_text(_render_snakefile(rules, universes))
    config_path.write_text(json.dumps(cfg, indent=2, sort_keys=True))

    return snakefile_path, config_path


def discover_universes(project_path: Path) -> list[str]:
    """Discover universe ids from ``universes/*.yaml``. If none exist,
    returns ``["default"]``.
    """
    universes_dir = project_path / "universes"
    if not universes_dir.exists():
        return ["default"]
    ids = sorted(p.stem for p in universes_dir.glob("*.yaml"))
    return ids or ["default"]


__all__ = ["generate", "discover_universes", "LIGHTCONE_DIR"]
