"""Generate ``.lightcone/Snakefile`` from ``astra.yaml``.

The Snakefile is a thin shell over the astra spec: one rule per output
with a recipe, parameterized by ``{universe}``. Each rule's body is a
``shell:`` recipe that runs the user's command then invokes
``.lightcone/_lc_finalize.py`` to atomically write the provenance
manifest. Snakemake's ``container:`` directive (honored when
``--sdm apptainer`` is passed) wraps the entire shell — recipe AND
finalizer — inside the container, which means the manifest is committed
in the same containerized process that produced the data.

All per-(rule, universe) details — recipe text, container image,
resolved decisions, precomputed code_version, resolved input paths —
live in ``.lightcone/snakefile-config.json``, keyed by rule and universe.
The finalizer reads this file at runtime; the Snakefile itself stays
tiny.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from astra.helpers import get_inputs, load_yaml, resolve_analysis_tree

from lightcone.engine.manifest import code_version
from lightcone.engine.tree import (
    TreeOutput,
    collect_tree_outputs,
    resolve_universe_decisions,
)

LIGHTCONE_DIR = ".lightcone"
SNAKEFILE_NAME = "Snakefile"
CONFIG_NAME = "snakefile-config.json"
FINALIZER_NAME = "_lc_finalize.py"


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
    return root_spec.get("container")  # type: ignore[no-any-return]


def _input_path_for(
    tree_out: TreeOutput,
    inp_id: str,
    spec: dict[str, Any],
    output_dirs: dict[str, str],
) -> str | None:
    """For a recipe input id, return the wildcard path to the upstream
    output's directory, or ``None`` if the input refers to an external
    file (handled separately).

    The simple case — a sibling output id — covers the iris pipeline
    pattern: ``recipe.inputs: [features]`` resolves to the sibling
    output's directory.
    """
    # Cross-analysis dotted reference, e.g. "feature_extraction.features".
    if "." in inp_id:
        _, out_id = inp_id.split(".", 1)
        return output_dirs.get(inp_id)

    # Local-scope sibling output (most common in practice).
    if tree_out.analysis_id is not None:
        qualified = f"{tree_out.analysis_id}.{inp_id}"
        if qualified in output_dirs:
            return output_dirs[qualified]
    if inp_id in output_dirs:
        return output_dirs[inp_id]

    # Sub-analysis input declaration with `from:` -> resolve through it.
    analysis_inputs = {i.get("id"): i for i in get_inputs(tree_out.analysis_spec)}
    inp_def = analysis_inputs.get(inp_id)
    if inp_def and inp_def.get("from"):
        from_ref = inp_def["from"].removeprefix("../").removeprefix("/")
        if from_ref in output_dirs:
            return output_dirs[from_ref]
        if "." in from_ref and from_ref in output_dirs:
            return output_dirs[from_ref]

    return None


_APPTAINER_SCHEMES = (
    "docker://",
    "oras://",
    "library://",
    "shub://",
    "http://",
    "https://",
    "docker-daemon://",
    "oci-archive:",
    "oci:",
)


def _resolve_container_uri(spec_str: str | None, project_path: Path) -> str | None:
    """Translate an astra.yaml ``container:`` value into a URI that
    apptainer accepts (i.e. what we emit in Snakemake's ``container:``
    directive).

    - ``None`` or empty → ``None`` (no container directive emitted).
    - Already-schemed URI (``docker://...``, ``oras://...``, etc.) → unchanged.
    - Path to a Containerfile in the project → reference the built SIF at
      ``.lightcone/images/lc-<name>-<hash>.sif``. (User must run ``lc build``
      first; we don't try to discover the hash here because that requires the
      full build context.)
    - Bare image name like ``python:3.12-slim`` → prepend ``docker://``.
    """
    if not spec_str:
        return None
    if any(spec_str.startswith(s) for s in _APPTAINER_SCHEMES):
        return spec_str
    candidate = project_path / spec_str
    if candidate.is_file():
        # Containerfile — reference the SIF that lc build produced. We
        # use a glob-friendly placeholder; the user must have built it.
        # Build path: .lightcone/images/lc-<name>-<hash>.sif (computed in
        # engine.container.compute_image_tag). We inline it here.
        from lightcone.engine.container import compute_image_tag

        # Project name fallback chain: spec.name → directory name. We
        # accept both since the build step uses the same fallback.
        # The caller passes the raw spec_str; we recompute the tag here.
        try:
            from astra.helpers import load_yaml  # local import; kept private

            spec = load_yaml(project_path / "astra.yaml")
            project_name = (spec.get("name") or project_path.name).lower().replace(" ", "-")
        except Exception:
            project_name = project_path.name.lower().replace(" ", "-")
        tag = compute_image_tag(project_name, candidate, project_path)
        return f".lightcone/images/{tag}.sif"
    # Bare registry image name → docker pull.
    return f"docker://{spec_str}"


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
    declared input id → wildcard path; only for sibling outputs, NOT
    external file inputs).

    Each rule body is a ``shell:`` block:

    ::

        set -euo pipefail
        <recipe>
        python3 .lightcone/_lc_finalize.py <key> <universe> <output>

    The finalizer is invoked after the recipe in the same shell. The
    manifest's ``os.replace`` is the atomic commit point: either the
    rule produced both data and manifest, or it failed and Snakemake
    will rerun. ``set -euo pipefail`` ensures a recipe failure stops
    before the finalizer runs.
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
        if r.get("container_uri"):
            # Snakemake's container directive — when --sdm apptainer is set,
            # the entire shell: block (recipe + finalizer) runs in this image.
            lines.append(f'    container: "{r["container_uri"]}"')
        # The recipe is inlined verbatim at column 0 so Snakemake's
        # standard substitution (``{output[0]}``, ``{input.X}``,
        # ``{wildcards.universe}``) works unchanged AND so multi-line
        # Python in ``python -c`` keeps its required indentation. The
        # finalizer call sits at the bottom of the same shell block —
        # its ``os.replace`` is the atomic commit point.
        recipe_text = r["recipe"].rstrip("\n")
        lines.append("    shell:")
        lines.append('        r"""')
        lines.append("set -euo pipefail")
        # User-facing progress marker — printed to stderr at the start of
        # each rule execution. Picked up by `lc run` so the user sees what
        # is being materialized without any snakemake-isms leaking through.
        lines.append(
            f'printf "\\033[2m▶\\033[0m %s \\033[2m[%s]\\033[0m\\n" '
            f'"{r["key"]}" "{{wildcards.universe}}" >&2'
        )
        lines.append(recipe_text)
        lines.append(
            f"python3 .lightcone/{FINALIZER_NAME} "
            f"{r['key']} {{wildcards.universe}} {{output.data}}"
        )
        lines.append('"""')
        lines.append("")

    return "\n".join(lines) + "\n"


def generate(
    project_path: Path,
    *,
    universes: list[str],
) -> tuple[Path, Path, bool]:
    """Write ``.lightcone/Snakefile`` and ``.lightcone/snakefile-config.json``.

    Returns ``(snakefile_path, config_path, uses_containers)`` where
    ``uses_containers`` is ``True`` if any rule emits a ``container:``
    directive — the caller uses this to decide whether to pass
    ``--sdm apptainer`` to ``snakemake``.
    """
    project_path = Path(project_path).resolve()
    spec = resolve_analysis_tree(load_yaml(project_path / "astra.yaml"), project_path)

    tree_outputs = collect_tree_outputs(spec)
    # Index of cfg-key -> wildcard output dir (used to wire up rule inputs)
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

        # Resolve sibling-output input wildcards.
        rule_inputs: dict[str, str] = {}
        for inp_id in recipe_inputs:
            up = _input_path_for(to, inp_id, spec, output_dirs)
            if up is not None:
                # Snakemake input dict keys must be valid identifiers.
                key = inp_id.replace(".", "__")
                rule_inputs[key] = up

        # Resolve container: raw spec_str feeds code_version (so an edit to
        # astra.yaml changes the hash); container_uri is what we hand to
        # Snakemake's container: directive.
        container_image = _resolve_container_for(to, spec)
        container_uri = _resolve_container_uri(container_image, project_path)

        rules.append(
            {
                "name": rule_name,
                "key": rule_key,
                "output_dir": out_dir_pattern,
                "inputs": rule_inputs,
                "container_uri": container_uri,
                "recipe": recipe.get("command", ""),
            }
        )

        # Per-universe cfg blob — read by the finalizer at runtime to
        # construct the manifest. Includes the resolved input directory
        # paths so the finalizer can chain to upstream manifests without
        # needing to know the project's directory layout.
        cfg.setdefault(rule_key, {})
        for u in universes:
            decisions = _decisions_for_output(to, u, project_path, spec)
            cv = code_version(
                recipe=recipe.get("command", ""),
                container_image=container_image,
                decisions=decisions,
            )
            resolved_inputs = {
                k: v.replace("{universe}", u) for k, v in rule_inputs.items()
            }
            cfg[rule_key][u] = {
                "output_id": to.output_id,
                "universe_id": u,
                "recipe": recipe.get("command", ""),
                "container_image": container_image,
                "container_uri": container_uri,
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
    finalizer_path = lightcone_dir / FINALIZER_NAME

    snakefile_path.write_text(_render_snakefile(rules, universes))
    config_path.write_text(json.dumps(cfg, indent=2, sort_keys=True))
    # Copy the canonical finalizer into the project so the rule's shell
    # can invoke it as ``python3 .lightcone/_lc_finalize.py``. Overwritten
    # every generation — the wheel is the only place this script
    # meaningfully lives.
    from lightcone.engine import _lc_finalize as _finalizer_module

    finalizer_path.write_text(Path(_finalizer_module.__file__).read_text())

    uses_containers = any(r.get("container_uri") for r in rules)
    return snakefile_path, config_path, uses_containers


def discover_universes(project_path: Path) -> list[str]:
    """Discover universe ids from ``universes/*.yaml``. If none exist,
    returns ``["default"]`` as a sentinel that lets ``lc run`` proceed
    on a project that hasn't created any explicit universes yet.
    """
    universes_dir = project_path / "universes"
    if not universes_dir.exists():
        return ["default"]
    ids = sorted(p.stem for p in universes_dir.glob("*.yaml"))
    return ids or ["default"]


# Re-exported for the CLI.
__all__ = ["generate", "discover_universes", "LIGHTCONE_DIR"]
