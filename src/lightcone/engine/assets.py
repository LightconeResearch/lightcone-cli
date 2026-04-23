"""Asset factory — generates Dagster assets from astra.yaml output recipes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import dagster as dg
from astra.helpers import get_inputs, load_yaml, resolve_analysis_tree

from lightcone.engine.container import resolve_container_for_slurm, resolve_container_spec
from lightcone.engine.runner import ASTRAContainerRunner
from lightcone.engine.tree import (
    TreeOutput,
    collect_tree_outputs,
)

logger = logging.getLogger(__name__)


def get_external_inputs(spec: dict[str, Any]) -> dict[str, str]:
    """Return {input_id: source_path} for inputs with a filesystem source."""
    result = {}
    for inp in get_inputs(spec):
        source = inp.get("source")
        if source and isinstance(source, str) and source.startswith("/"):
            result[inp["id"]] = source
    return result


def _resolve_container(
    spec: str | None,
    project_path: Path,
    project_name: str,
    container_runtime: str | None = None,
    local_runtime: str | None = None,
) -> str | None:
    """Resolve a container spec, dispatching to the right builder.

    When *container_runtime* is set (i.e. we are targeting SLURM), uses
    ``resolve_container_for_slurm`` which handles podman-hpc build/migrate
    and podman-hpc migrate automatically.  When *local_runtime* is set
    (Docker or Podman detected locally), uses ``resolve_container_spec``
    with that runtime.  Returns ``None`` if no runtime is available.
    """
    if container_runtime:
        return resolve_container_for_slurm(
            spec, project_path, project_name, container_runtime,
        )
    if local_runtime:
        return resolve_container_spec(
            spec, project_path, project_name, runtime=local_runtime,
        )
    # No container runtime available — skip building
    return None


def build_asset_definitions(
    spec: dict[str, Any],
    runner: ASTRAContainerRunner | None = None,
    universe_id: str = "baseline",
    project_path: Path | None = None,
    project_name: str | None = None,
    no_build: bool = False,
    container_runtime: str | None = None,
    local_runtime: str | None = None,
) -> list[dg.AssetsDefinition | dg.AssetSpec]:
    """Generate one @asset per output with a recipe.

    Walks the full analysis tree (including sub-analyses) to create assets
    with hierarchical keys like ``[universe, sub_analysis_id, output_id]``.
    """
    # Resolve analysis-level container spec once.
    raw_default = spec.get("container")
    if raw_default is not None and not no_build and (container_runtime or local_runtime):
        _name = project_name or spec.get("name") or "project"
        _path = project_path or Path.cwd()
        default_container = _resolve_container(
            raw_default, _path, _name, container_runtime, local_runtime,
        )
    else:
        default_container = raw_default

    # Collect external inputs (inputs with filesystem source paths)
    external = get_external_inputs(spec)
    asset_specs = [
        dg.AssetSpec(
            key=dg.AssetKey([universe_id, inp_id]),
            metadata={"source": source, "external": True},
        )
        for inp_id, source in external.items()
    ]

    assets: list[dg.AssetsDefinition | dg.AssetSpec] = list(asset_specs)

    # Collect outputs from the full tree (root + sub-analyses)
    tree_outputs = collect_tree_outputs(spec)

    for tree_out in tree_outputs:
        output_id = tree_out.output_id
        output_def = tree_out.output_def
        recipe = output_def.get("recipe")

        if not output_id or not recipe:
            # Root-level alias outputs (from: sub.output) become AssetSpecs
            from_ref = output_def.get("from")
            if output_id and from_ref and tree_out.analysis_id is None:
                # Alias: root output referencing a sub-analysis output
                if "." in from_ref:
                    sub_id, sub_out = from_ref.split(".", 1)
                    assets.append(dg.AssetSpec(
                        key=dg.AssetKey([universe_id, output_id]),
                        deps=[dg.AssetKey([universe_id, sub_id, sub_out])],
                        metadata={"alias_for": from_ref},
                    ))
            continue

        # Determine asset key prefix based on sub-analysis membership
        if tree_out.analysis_id:
            key_prefix = [universe_id, tree_out.analysis_id]
            group_name = tree_out.analysis_id
        else:
            key_prefix = [universe_id]
            group_name = None

        # Resolve container for this sub-analysis (inheritance chain)
        sub_container = _resolve_sub_container(
            tree_out, spec, default_container, project_path,
            project_name, no_build, container_runtime, local_runtime,
        )

        # Resolve dependencies: recipe.inputs may reference cross-sub-analysis outputs
        dep_keys = _resolve_recipe_deps(
            recipe, tree_out, spec, universe_id,
        )

        # Build the external inputs relevant to this sub-analysis
        recipe_input_ids = recipe.get("inputs") or []
        sub_external = {
            k: v for k, v in external.items() if k in recipe_input_ids
        } or None

        assets.append(
            _build_single_asset(
                output_id, recipe, runner, universe_id, project_path,
                project_name=project_name, default_container=sub_container,
                no_build=no_build, container_runtime=container_runtime,
                local_runtime=local_runtime, external_inputs=sub_external,
                key_prefix=key_prefix, group_name=group_name,
                dep_keys=dep_keys, tree_output=tree_out, spec=spec,
            )
        )

    return assets


def _resolve_sub_container(
    tree_out: TreeOutput,
    spec: dict[str, Any],
    default_container: str | None,
    project_path: Path | None,
    project_name: str | None,
    no_build: bool,
    container_runtime: str | None,
    local_runtime: str | None,
) -> str | None:
    """Resolve container for a tree output with inheritance.

    Order: recipe-level > sub-analysis-level > root-level (default_container).
    """
    # Check sub-analysis level container
    if tree_out.analysis_id:
        sub_raw = tree_out.analysis_spec.get("container")
        if sub_raw is not None and not no_build and (container_runtime or local_runtime):
            _name = project_name or "project"
            _path = project_path or Path.cwd()
            return _resolve_container(
                sub_raw, _path, _name, container_runtime, local_runtime,
            )
        elif sub_raw is not None:
            return sub_raw

    return default_container


def _resolve_recipe_deps(
    recipe: dict[str, Any],
    tree_out: TreeOutput,
    spec: dict[str, Any],
    universe_id: str,
) -> list[dg.AssetKey] | None:
    """Resolve recipe input dependencies to Dagster asset keys.

    Handles:
    - Simple IDs within the same analysis scope
    - ``from:`` references on sub-analysis inputs that point to siblings
    """
    input_ids = recipe.get("inputs") or []
    if not input_ids:
        return None

    deps: list[dg.AssetKey] = []
    analysis_inputs = {
        inp.get("id"): inp
        for inp in get_inputs(tree_out.analysis_spec)
    }

    for inp_id in input_ids:
        inp_def = analysis_inputs.get(inp_id)
        if inp_def and inp_def.get("from"):
            from_ref = inp_def["from"]
            # ../sibling.output_id -> [universe, sibling, output_id]
            ref = from_ref.removeprefix("../").removeprefix("/")
            if "." in ref:
                sub_id, sub_out = ref.split(".", 1)
                deps.append(dg.AssetKey([universe_id, sub_id, sub_out]))
                continue

        # Dot-notation cross-analysis reference (e.g. hod_fitting.galaxy_mesh)
        if "." in inp_id:
            sub_id, sub_out = inp_id.split(".", 1)
            deps.append(dg.AssetKey([universe_id, sub_id, sub_out]))
        elif tree_out.analysis_id:
            deps.append(dg.AssetKey([universe_id, tree_out.analysis_id, inp_id]))
        else:
            deps.append(dg.AssetKey([universe_id, inp_id]))

    return deps


def _load_universe_params(
    project_path: Path | None, universe_id: str
) -> dict[str, Any]:
    """Load universe decisions as params dict."""
    if project_path is None:
        return {}
    universe_file = project_path / "universes" / f"{universe_id}.yaml"
    if not universe_file.exists():
        return {}
    universe_data = load_yaml(universe_file)
    return universe_data.get("decisions", {})


def _build_single_asset(
    output_id: str,
    recipe: dict[str, Any],
    runner: ASTRAContainerRunner | None = None,
    universe_id: str = "baseline",
    project_path: Path | None = None,
    project_name: str | None = None,
    default_container: str | None = None,
    no_build: bool = False,
    container_runtime: str | None = None,
    local_runtime: str | None = None,
    external_inputs: dict[str, str] | None = None,
    key_prefix: list[str] | None = None,
    group_name: str | None = None,
    dep_keys: list[dg.AssetKey] | None = None,
    tree_output: TreeOutput | None = None,
    spec: dict[str, Any] | None = None,
) -> dg.AssetsDefinition:
    """Build a single Dagster asset from an output recipe."""
    input_ids = recipe.get("inputs") or []
    command = recipe["command"]
    # Filter external inputs to those referenced by this recipe
    recipe_external = {
        k: v for k, v in (external_inputs or {}).items() if k in input_ids
    } or None
    raw_container = recipe.get("container")
    # Resolve per-recipe container spec; fall back to analysis-level default.
    if raw_container is not None and not no_build and (container_runtime or local_runtime):
        _name = project_name or "project"
        _path = project_path or Path.cwd()
        container = _resolve_container(
            raw_container, _path, _name, container_runtime, local_runtime,
        )
    elif raw_container is not None:
        container = raw_container
    else:
        container = default_container
    resources = recipe.get("resources") or {}

    # Use provided key_prefix or default to [universe_id]
    effective_prefix = key_prefix or [universe_id]
    # Use provided dep_keys or build from input_ids
    effective_deps = dep_keys or [dg.AssetKey([universe_id, i]) for i in input_ids]

    asset_kwargs: dict[str, Any] = {
        "name": output_id,
        "key_prefix": effective_prefix,
        "deps": effective_deps,
        "metadata": {
            "command": command,
            "container": container or "default",
        },
    }
    if group_name:
        asset_kwargs["group_name"] = group_name

    @dg.asset(**asset_kwargs)
    def _asset(context) -> dg.MaterializeResult:
        params = _load_universe_params(project_path, universe_id)

        # Determine working directory for sub-analysis recipes
        cwd_override = None
        if tree_output and tree_output.analysis_path and project_path:
            cwd_override = str(
                (project_path / tree_output.analysis_path).resolve()
            )

        result = runner.execute(
            command=command,
            container=container,
            inputs=input_ids,
            output_id=output_id,
            universe_id=universe_id,
            resources=resources,
            params=params,
            external_inputs=recipe_external,
            cwd_override=cwd_override,
        )
        if result.metadata.get("stdout"):
            context.log.info(result.metadata["stdout"])
        if result.exit_code != 0:
            stderr = result.metadata.get("stderr", "")
            raise RuntimeError(
                f"Recipe for '{output_id}' failed (exit code {result.exit_code})"
                f"{': ' + stderr if stderr else ''}"
            )
        return dg.MaterializeResult(
            metadata={
                "exit_code": result.exit_code,
                "output_path": str(result.output_path),
                "backend": result.metadata.get("backend", "unknown"),
            }
        )

    return _asset


def build_definitions(
    project_path: Path,
    target_config: dict[str, Any] | None = None,
    universe_id: str = "baseline",
    no_build: bool = False,
    cli_overrides: dict[str, Any] | None = None,
    target_name: str | None = None,
) -> dg.Definitions:
    """Build complete Dagster Definitions from an ASTRA project.

    This is the main entry point for the Dagster integration.  When a SLURM
    target is provided, container images are automatically built (podman-hpc)
    or pulled before asset definitions are constructed.

    *cli_overrides* are runtime option overrides from CLI flags (e.g.
    ``--qos``, ``--constraint``, ``--time-limit``).  They are merged with
    the target's defaults via :func:`resolve_run_config`.

    *target_name* is threaded to the runner for cluster cache lookup.
    """
    spec = load_yaml(project_path / "astra.yaml")
    # Resolve sub-analysis tree: expand path: references
    spec = resolve_analysis_tree(spec, project_path)
    project_name = spec.get("name") or project_path.name

    # Build runner config from target
    runner_config = None
    container_runtime: str | None = None
    local_runtime: str | None = None
    backend = "docker"

    if target_config:
        backend = target_config.get("backend", "docker")
        container_runtime = target_config.get("container_runtime")
        runner_config = {"connection": target_config.get("connection", {})}

        if backend == "slurm":
            from lightcone.engine.targets import (
                get_cache_key_overrides,
                get_option_choices,
                get_option_default,
                resolve_run_config,
            )

            resolved = resolve_run_config(target_config, cli_overrides or {})
            scheduler: dict[str, Any] = {
                "site": target_config.get("site"),
                "container_runtime": container_runtime,
            }
            # Environment axes (qos/constraint/partition/account) live in
            # `scheduler`.  `time_limit` is a *resource request* — the runner
            # merges it into the recipe's resources dict so validation,
            # clamping, and sbatch emission all agree on one value.  Keep CLI
            # and target-default separate so precedence stays CLI > recipe >
            # target default.
            for key in ("qos", "constraint", "account", "partition"):
                if resolved.get(key) is not None:
                    scheduler[key] = resolved[key]
            cli_time_limit = (cli_overrides or {}).get("time_limit")
            if cli_time_limit is not None:
                scheduler["_cli_time_limit"] = cli_time_limit
            default_time_limit = get_option_default(target_config, "time_limit")
            if default_time_limit is not None:
                scheduler["_default_time_limit"] = default_time_limit

            # Runner metadata for QoS validation and auto-adjust.
            if target_name:
                scheduler["_target_name"] = target_name
            scheduler["_strategy"] = (
                (cli_overrides or {}).get("strategy")
                or target_config.get("strategy", "fit")
            )
            qos_choices = list(get_option_choices(target_config, "qos"))
            if qos_choices:
                scheduler["_qos_choices"] = qos_choices
            overrides = get_cache_key_overrides(target_config)
            if overrides:
                scheduler["_cache_key_overrides"] = overrides

            if target_config.get("extra_slurm_args"):
                scheduler["extra_slurm_args"] = target_config["extra_slurm_args"]
            if target_config.get("extra_container_flags"):
                scheduler["extra_container_flags"] = target_config[
                    "extra_container_flags"
                ]

            runner_config["scheduler"] = scheduler
            runner_config["resource_limits"] = target_config.get(
                "resource_limits", {}
            )
            runner_config["poll"] = target_config.get("poll", {})
        else:
            # Non-scheduler backend — only forward what matters to the runner.
            scheduler = {}
            for key in ("site", "container_runtime"):
                if target_config.get(key) is not None:
                    scheduler[key] = target_config[key]
            if scheduler:
                runner_config["scheduler"] = scheduler
    else:
        # No target config → local target.  Detect available container runtime.
        from lightcone.engine.container import detect_container_runtime
        local_runtime = detect_container_runtime()
        backend = "docker" if local_runtime else "venv"

    # Resolve analysis-level container spec to a string for the runner.
    # For SLURM targets this triggers podman-hpc build/migrate or
    # podman-hpc migrate automatically.  Skipped entirely when no
    # container runtime is available.
    raw_container = spec.get("container")
    if not no_build and (container_runtime or local_runtime):
        default_container = _resolve_container(
            raw_container, project_path, project_name,
            container_runtime, local_runtime,
        )
    else:
        default_container = raw_container

    # Build runner from target config
    if runner_config:
        runner = ASTRAContainerRunner(
            project_root=str(project_path),
            backend=backend,
            default_container=default_container,
            target_config=runner_config,
        )
    else:
        runner = ASTRAContainerRunner(
            project_root=str(project_path),
            backend=backend,
            default_container=default_container,
            container_runtime=local_runtime,
        )

    assets = build_asset_definitions(
        spec, runner=runner, universe_id=universe_id, project_path=project_path,
        project_name=project_name, no_build=no_build,
        container_runtime=container_runtime, local_runtime=local_runtime,
    )

    return dg.Definitions(assets=assets)
