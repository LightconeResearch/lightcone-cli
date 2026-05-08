"""Generate ``.lightcone/Snakefile`` from ``astra.yaml``.

The Snakefile is a thin shell over the astra spec: one rule per output
with a recipe, parameterized by ``{universe}``. Each rule's body is a
``run:`` block that calls :func:`lightcone.engine.runner.run_rule` with
the per-(rule, universe) cfg blob — which already contains the rendered
and wrapped ``shell_command``. All template substitution and container
wrapping happen here, at generation time, where every value is concrete
for a given universe; the runner stays a thin executor.

ASTRA v0.0.7 moved ``inputs`` and ``decisions`` declarations from
``Recipe`` up to ``Output``. The recipe body is a *template* using a
small placeholder grammar — see :func:`render_recipe`. We don't use
Snakemake's ``container:`` directive or ``--sdm apptainer``: the
generator wraps with the configured runtime end-to-end (see
:mod:`lightcone.engine.container`).

The ``os.replace`` rename inside ``write_manifest`` (called by
``run_rule``) is the atomic commit point — either the rule produced
both data and manifest, or it failed and Snakemake reruns the rule.
"""
from __future__ import annotations

import json
import string
import subprocess
from pathlib import Path
from typing import Any

from astra.helpers import load_yaml, resolve_analysis_tree

from lightcone.engine.container import make_image_tag_resolver, wrap_recipe
from lightcone.engine.manifest import code_version
from lightcone.engine.tree import (
    TreeOutput,
    collect_tree_outputs,
    find_upstream_output,
    resolve_container_spec,
    resolve_external_input,
    resolve_universe_decisions,
)

LIGHTCONE_DIR = ".lightcone"
SNAKEFILE_NAME = "Snakefile"
CONFIG_NAME = "snakefile-config.json"

_FORMATTER = string.Formatter()


def render_recipe(
    template: str,
    *,
    inputs: dict[str, str],
    decisions: dict[str, str],
    output: str,
) -> str:
    """Substitute v0.0.7 recipe template placeholders.

    Recognized placeholders:

    * ``{output}``         — directory the artifact is written to.
    * ``{inputs}``         — space-separated values of every entry in
                             ``inputs`` (in declaration order).
    * ``{inputs.<id>}``    — the named upstream input's resolved value
                             (a sibling output's directory path or an
                             analysis-level Input's source string).
    * ``{decisions.<id>}`` — the active option ID for the named
                             decision in this universe.

    ``{{`` and ``}}`` collapse to literal ``{`` / ``}``. Unknown
    placeholders, undeclared references, and format-spec or conversion
    flags raise :class:`KeyError` / :class:`ValueError`. ``astra
    validate`` should already have caught these — the strict behaviour
    here is defense-in-depth.
    """
    pieces: list[str] = []
    for literal, field, spec, conv in _FORMATTER.parse(template):
        pieces.append(literal)
        if field is None:
            continue
        if spec or conv:
            raise ValueError(
                f"Recipe placeholder '{{{field}}}' must not use a format "
                "spec or conversion"
            )
        if field == "output":
            pieces.append(output)
            continue
        if field == "inputs":
            pieces.append(" ".join(inputs.values()))
            continue
        head, dot, tail = field.partition(".")
        if not dot:
            raise ValueError(
                f"Unknown recipe placeholder '{{{field}}}' "
                "(use {inputs}, {inputs.<id>}, {decisions.<id>}, or {output})"
            )
        if head == "inputs":
            if tail not in inputs:
                raise KeyError(
                    f"Recipe placeholder '{{inputs.{tail}}}' references an "
                    "input not declared on this Output"
                )
            pieces.append(inputs[tail])
        elif head == "decisions":
            if tail not in decisions:
                raise KeyError(
                    f"Recipe placeholder '{{decisions.{tail}}}' references "
                    "a decision not declared on this Output"
                )
            pieces.append(str(decisions[tail]))
        else:
            raise ValueError(
                f"Unknown recipe placeholder namespace '{head}' in '{{{field}}}'"
            )
    return "".join(pieces)


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


def _git_remote(project_path: Path) -> str | None:
    """URL of the ``origin`` git remote, if the project is a git clone.

    Captured into the manifest alongside ``git_sha`` so a published
    bundle can identify *which repository* the commit belongs to.
    SSH URLs (``git@host:owner/repo.git``) are normalised to ``https``
    form so consumers (e.g. WorkflowHub) can render them as clickable
    links.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(project_path),
             "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            return None
        url = out.stdout.strip()
        if not url:
            return None
        if url.startswith("git@"):
            host_path = url.removeprefix("git@").replace(":", "/", 1)
            url = f"https://{host_path}"
        if url.endswith(".git"):
            url = url.removesuffix(".git")
        return url
    except FileNotFoundError:
        return None


def _lc_version() -> str:
    try:
        from importlib.metadata import version

        return version("lightcone-cli")
    except Exception:
        return "unknown"


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


def _safe_input_key(raw_id: str) -> str:
    """Snakemake's ``input:`` keys must be valid Python identifiers,
    so ``sub.real`` becomes ``sub__real``. Raw IDs (with their dots
    intact) are still used as keys in the manifest's ``input_versions``
    map and inside the runner — verify and write_manifest see the same
    spelling the user wrote in ``Output.inputs``.
    """
    return raw_id.replace(".", "__")


def _scoped_decisions_for_output(
    tree_out: TreeOutput,
    universe_decisions: dict[str, Any],
) -> dict[str, Any]:
    """Pick the active option ID for each decision the Output declares.

    v0.0.7: ``Output.decisions`` lists the IDs of decisions that
    parameterize this output. The runner only needs (and the recipe
    template can only reference) those — anything else is out of scope.
    """
    declared = tree_out.output_def.get("decisions") or []
    if not declared:
        return {}
    scoped: dict[str, Any] = {}
    prefix = f"{tree_out.analysis_id}." if tree_out.analysis_id else ""
    for dec_id in declared:
        if prefix and (qualified := f"{prefix}{dec_id}") in universe_decisions:
            scoped[dec_id] = universe_decisions[qualified]
        elif dec_id in universe_decisions:
            scoped[dec_id] = universe_decisions[dec_id]
    return scoped


def _universe_decisions(
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

    Each rule descriptor has ``name`` (Snakemake-safe), ``key`` (cfg
    lookup), ``output_dir`` (wildcard pattern), and ``inputs`` (list of
    ``(raw_id, safe_key, path_pattern)`` triples — both sibling outputs
    and analysis-level Inputs, in declaration order). External Input
    patterns are static source strings; sibling-output patterns carry a
    ``{universe}`` wildcard.

    The rule body is a thin call into :func:`runner.run_rule`. The
    ``inputs`` dict it builds is keyed by the **raw** input IDs (with
    their dots intact) so that ``write_manifest``'s ``input_versions``
    matches what ``verify`` walks for chain integrity.
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
    lines.append("from lightcone.engine.runner import run_rule")
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
            for _raw, safe, pattern in r["inputs"]:
                lines.append(f'        {safe}="{pattern}",')
        lines.append("    output:")
        lines.append(f'        data=directory("{r["output_dir"]}"),')
        lines.append(f'        manifest="{r["output_dir"]}/.lightcone-manifest.json",')
        lines.append("    params:")
        lines.append(f'        cfg=lambda wc: CFG["{r["key"]}"][wc.universe],')
        lines.append("    run:")
        # Manifest input_versions are keyed by raw declared input IDs
        # (e.g. "sub.real"), not the safe Snakemake-input-directive key
        # ("sub__real"). Verify walks the raw IDs from Output.inputs;
        # this dict literal is what bridges the two.
        inp_pairs = ", ".join(
            f'"{raw}": Path(input.{safe})' for raw, safe, _pattern in r["inputs"]
        )
        lines.append("        run_rule(")
        lines.append(f'            rule_key="{r["key"]}",')
        lines.append("            universe=wildcards.universe,")
        lines.append("            output_dir=Path(output.data),")
        lines.append(f"            inputs={{{inp_pairs}}},")
        lines.append("            cfg=dict(params.cfg),")
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

    rules: list[dict[str, Any]] = []
    cfg: dict[str, dict[str, dict[str, Any]]] = {}

    git_sha = _git_sha(project_path)
    git_remote = _git_remote(project_path)
    lc_version = _lc_version()
    resolve_image = make_image_tag_resolver(project_path, project_name)

    for to in tree_outputs:
        recipe = to.output_def.get("recipe")
        if recipe is None:
            continue  # alias output (re-export via ``from:``)

        rule_key = _rule_key(to)
        rule_name = _rule_name(to)
        out_dir_pattern = _output_dir_pattern(to)

        # v0.0.7: declared upstream inputs live on the Output, not the
        # Recipe. Each ID resolves to either a sibling output (a
        # universe-templated path) or an analysis-level Input (a static
        # source string). Both flow through the same Snakemake ``input:``
        # slot so write_manifest fingerprints them and Snakemake gets to
        # enforce existence and detect mtime drift uniformly.
        declared_inputs = to.output_def.get("inputs") or []
        recipe_command = recipe.get("command", "")

        rule_inputs: list[tuple[str, str, str]] = []  # (raw_id, safe_key, pattern)
        for inp_id in declared_inputs:
            up = find_upstream_output(to, inp_id, tree_outputs)
            if up is not None:
                pattern = _output_dir_pattern(up)
            else:
                ext = resolve_external_input(to, inp_id, spec)
                if ext is None:
                    continue  # unresolvable; ``astra validate`` flags it.
                pattern = ext
            rule_inputs.append((inp_id, _safe_input_key(inp_id), pattern))

        container_image = resolve_container_spec(to, spec)
        image_tag = resolve_image(container_image)

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
            universe_decisions = _universe_decisions(u, project_path, spec)
            scoped_decisions = _scoped_decisions_for_output(to, universe_decisions)

            # Build the resolved input map in declaration order so
            # ``{inputs}`` joins paths in the same order the user wrote
            # them. The ``{universe}`` substitution is a no-op for static
            # external paths.
            resolved_inputs: dict[str, str] = {
                raw: pat.replace("{universe}", u) for raw, _, pat in rule_inputs
            }

            output_dir = out_dir_pattern.replace("{universe}", u)
            rendered = render_recipe(
                recipe_command,
                inputs=resolved_inputs,
                decisions=scoped_decisions,
                output=output_dir,
            )
            wrapped = wrap_recipe(rendered, image=image_tag, runtime=runtime)
            # ``image_tag`` (not the raw spec string) so a Containerfile
            # edit propagates through ``code_version`` to ``lc status``.
            cv = code_version(
                recipe=recipe_command,
                container_image=image_tag,
                decisions=scoped_decisions,
            )
            # Prefix the executed command with a no-op ``:`` builtin
            # carrying the code_version. This makes the wrapped command
            # differ when the recipe / container / decisions drift, so
            # (a) Snakemake's ``shellcmd`` trigger sees the change and
            # (b) any shell trace carries a breadcrumb. The trigger
            # that actually fires today is ``params`` (cfg is
            # per-universe and contains ``shell_command``) — see
            # ``lc run --rerun-triggers``.
            shell_command = f": lc_code_version={cv};\n{wrapped}"
            cfg[rule_key][u] = {
                "output_id": to.output_id,
                "output_type": to.output_def.get("type"),
                "universe_id": u,
                # Raw template, preserved so the manifest's ``recipe``
                # field records what the user authored.
                "recipe": recipe_command,
                "shell_command": shell_command,
                "container_image": container_image,
                "decisions": scoped_decisions,
                "code_version": cv,
                "git_sha": git_sha,
                "git_remote": git_remote,
                "lc_version": lc_version,
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


__all__ = [
    "LIGHTCONE_DIR",
    "discover_universes",
    "generate",
    "render_recipe",
]
