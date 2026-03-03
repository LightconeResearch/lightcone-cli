"""Asset factory — generates Dagster assets from astra.yaml output recipes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import dagster as dg
from astra.helpers import get_inputs, get_outputs, load_yaml

from prism.container import resolve_container_for_slurm, resolve_container_spec
from prism.dagster.runner import ASTRAContainerRunner

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
    spec: str | dict[str, Any] | None,
    project_path: Path,
    project_name: str,
    container_runtime: str | None = None,
) -> str | None:
    """Resolve a container spec, dispatching to the right builder.

    When *container_runtime* is set (i.e. we are targeting SLURM), uses
    ``resolve_container_for_slurm`` which handles podman-hpc build/migrate
    and podman-hpc migrate automatically.  Otherwise falls back to the
    default Docker-based ``resolve_container_spec``.
    """
    if container_runtime:
        return resolve_container_for_slurm(
            spec, project_path, project_name, container_runtime,
        )
    return resolve_container_spec(spec, project_path, project_name)


def build_asset_definitions(
    spec: dict[str, Any],
    runner: ASTRAContainerRunner | None = None,
    universe_id: str = "baseline",
    project_path: Path | None = None,
    project_name: str | None = None,
    no_build: bool = False,
    container_runtime: str | None = None,
) -> list[dg.AssetsDefinition | dg.AssetSpec]:
    """Generate one @asset per output with a recipe."""
    outputs = get_outputs(spec)

    # Resolve analysis-level container spec once.
    raw_default = spec.get("container")
    if raw_default is not None and not no_build:
        _name = project_name or spec.get("name") or "project"
        _path = project_path or Path.cwd()
        default_container = _resolve_container(
            raw_default, _path, _name, container_runtime,
        )
    else:
        default_container = raw_default if isinstance(raw_default, str) else None

    # Collect external inputs (inputs with filesystem source paths)
    external = get_external_inputs(spec)
    asset_specs = [
        dg.AssetSpec(inp_id, metadata={"source": source, "external": True})
        for inp_id, source in external.items()
    ]

    assets: list[dg.AssetsDefinition | dg.AssetSpec] = list(asset_specs)
    for output_def in outputs:
        output_id = output_def.get("id")
        recipe = output_def.get("recipe")
        if not output_id or not recipe:
            continue
        assets.append(
            _build_single_asset(
                output_id, recipe, runner, universe_id, project_path,
                project_name=project_name, default_container=default_container,
                no_build=no_build, container_runtime=container_runtime,
                external_inputs=external,
            )
        )

    return assets


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
    external_inputs: dict[str, str] | None = None,
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
    if raw_container is not None and not no_build:
        _name = project_name or "project"
        _path = project_path or Path.cwd()
        container = _resolve_container(raw_container, _path, _name, container_runtime)
    elif raw_container is not None and isinstance(raw_container, str):
        container = raw_container
    else:
        container = default_container
    resources = recipe.get("resources") or {}

    @dg.asset(
        name=output_id,
        deps=[dg.AssetKey(i) for i in input_ids],
        metadata={
            "command": command,
            "container": container or "default",
        },
    )
    def _asset(context) -> dg.MaterializeResult:
        params = _load_universe_params(project_path, universe_id)
        result = runner.execute(
            command=command,
            container=container,
            inputs=input_ids,
            output_id=output_id,
            universe_id=universe_id,
            resources=resources,
            params=params,
            external_inputs=recipe_external,
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
) -> dg.Definitions:
    """Build complete Dagster Definitions from an ASTRA project.

    This is the main entry point for the Dagster integration.  When a SLURM
    target is provided, container images are automatically built (podman-hpc)
    or pulled before asset definitions are constructed.
    """
    spec = load_yaml(project_path / "astra.yaml")
    project_name = spec.get("name") or project_path.name

    # Build runner config from target
    runner_config = None
    container_runtime: str | None = None
    backend = "docker"

    if target_config:
        backend = target_config.get("backend", "docker")
        container_runtime = target_config.get("container_runtime")
        # Transform flat target_config into the shape the runner expects
        runner_config = {"connection": target_config.get("connection", {})}
        scheduler = {}
        for key in ("account", "qos", "constraint", "node_type",
                     "container_runtime", "container_flags",
                     "nodes", "time_limit"):
            if target_config.get(key) is not None:
                scheduler[key] = target_config[key]
        if scheduler:
            runner_config["scheduler"] = scheduler

    # Resolve analysis-level container spec to a string for the runner.
    # For SLURM targets this triggers podman-hpc build/migrate or
    # podman-hpc migrate automatically.
    raw_container = spec.get("container")
    if not no_build:
        default_container = _resolve_container(
            raw_container, project_path, project_name, container_runtime,
        )
    else:
        default_container = raw_container if isinstance(raw_container, str) else None

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
            backend="docker",
            default_container=default_container,
        )

    assets = build_asset_definitions(
        spec, runner=runner, universe_id=universe_id, project_path=project_path,
        project_name=project_name, no_build=no_build,
        container_runtime=container_runtime,
    )

    return dg.Definitions(assets=assets)
