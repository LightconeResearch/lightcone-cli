"""Command-line interface for lightcone-cli — the ASTRA-compliant agentic layer."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console

from lightcone.cli.plugin import get_plugin_source_dir

console = Console()
logger = logging.getLogger(__name__)

#: Permission tier definitions for Claude Code's ``.claude/settings.json``.
PERMISSION_TIERS: dict[str, dict[str, list[str]]] = {
    "yolo": {
        "allow": [
            "Bash(*)", "Edit", "Read", "Write", "WebSearch", "WebFetch", "mcp__*",
        ],
    },
    "recommended": {
        "allow": [
            "Read", "Edit", "Write", "Bash(*)", "WebSearch", "WebFetch",
        ],
        "deny": [
            "Edit(~/.ssh/**)", "Edit(~/.aws/**)", "Edit(~/.gnupg/**)",
            "Edit(//scratch/**)", "Edit(//pscratch/**)",
            "Bash(sudo *)", "Bash(rm -rf /*)",
            "Bash(git push *)", "Bash(git push)",
        ],
    },
    "minimal": {"allow": ["Read"]},
}


@click.group()
@click.version_option(package_name="lightcone-cli")
def main() -> None:
    """lightcone-cli — ASTRA-compliant Agentic Layer CLI."""


# =============================================================================
# Path helpers
# =============================================================================


def _find_lightcone_yaml(project_path: Path) -> Path | None:
    """Find lightcone.yaml, checking ``.lightcone/`` first then root."""
    candidate = project_path / ".lightcone" / "lightcone.yaml"
    if candidate.exists():
        return candidate
    candidate = project_path / "lightcone.yaml"
    return candidate if candidate.exists() else None


def _find_dagster_yaml(project_path: Path) -> Path | None:
    candidate = project_path / ".lightcone" / "dagster.yaml"
    if candidate.exists():
        return candidate
    candidate = project_path / "dagster.yaml"
    return candidate if candidate.exists() else None


def _scratch_dagster_dir(project_path: Path) -> Path | None:
    """Per-project Dagster instance dir on scratch, or ``None`` if no scratch."""
    for var in ("PSCRATCH", "SCRATCH"):
        v = os.environ.get(var)
        if v:
            return Path(v) / "lightcone" / "dagster" / project_path.name
    return None


def _resolve_dagster_instance_dir(project_path: Path) -> Path:
    """Return the directory to pass to ``DagsterInstance.from_config``.

    On compute nodes (``$SLURM_JOB_ID`` set), materializes a transient
    ``dagster.yaml`` on scratch with ``storage.sqlite.base_dir`` overridden
    to a scratch path — SQLite cannot reliably operate on NERSC's NFS
    home from compute nodes (it raises ``disk I/O error``).  The user's
    other ``dagster.yaml`` keys (loggers, run launcher, etc.) are
    preserved.

    On login nodes, returns the project's own ``dagster.yaml`` parent
    unchanged (auto-generating the default if absent).
    """
    dagster_yaml_path = _find_dagster_yaml(project_path)
    if dagster_yaml_path is None:
        lightcone_dir = project_path / ".lightcone"
        lightcone_dir.mkdir(parents=True, exist_ok=True)
        dagster_yaml_path = lightcone_dir / "dagster.yaml"
        dagster_yaml_path.write_text(
            yaml.dump(
                {"storage": {"sqlite": {"base_dir": "results/.dagster"}}},
                default_flow_style=False, sort_keys=False,
            )
        )

    if not os.environ.get("SLURM_JOB_ID"):
        return dagster_yaml_path.parent

    scratch_dir = _scratch_dagster_dir(project_path)
    if scratch_dir is None:
        # No scratch — fall through; SQLite may or may not work, but we
        # don't want to silently swap behaviour without a real alternative.
        return dagster_yaml_path.parent
    scratch_dir.mkdir(parents=True, exist_ok=True)

    user_config = yaml.safe_load(dagster_yaml_path.read_text()) or {}
    user_config.setdefault("storage", {})["sqlite"] = {"base_dir": str(scratch_dir)}
    (scratch_dir / "dagster.yaml").write_text(
        yaml.dump(user_config, default_flow_style=False, sort_keys=False),
    )
    return scratch_dir


def _load_lightcone_config(project_path: Path) -> dict:
    path = _find_lightcone_yaml(project_path)
    if path is None:
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


# =============================================================================
# Init command
# =============================================================================


@main.command()
@click.argument("directory", type=click.Path(path_type=Path), default=".")
@click.option("--no-git", is_flag=True, help="Don't initialize git repository")
@click.option("--no-venv", is_flag=True, help="Don't create Python virtual environment")
@click.option("--cluster", default=None, help="Default cluster for `lc run` (omit for local)")
@click.option(
    "--permissions",
    type=click.Choice(["yolo", "recommended", "minimal"]),
    default="recommended",
    help="Claude Code permission tier (default: recommended)",
)
@click.option(
    "--existing-project", "existing_project",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to existing code to migrate into ASTRA",
)
@click.option(
    "--sub-analysis", "sub_analysis", is_flag=True, default=False,
    help="Create a sub-analysis directory and wire it into the parent project",
)
def init(
    directory: Path, no_git: bool, no_venv: bool,
    cluster: str | None, permissions: str,
    existing_project: Path | None, sub_analysis: bool,
) -> None:
    """Create a new ASTRA analysis project with full agentic scaffolding."""
    if sub_analysis:
        _init_sub_analysis(directory)
        return

    if existing_project is not None:
        _init_existing_project(
            directory, source=existing_project,
            no_git=no_git, no_venv=no_venv,
            cluster=cluster, permissions=permissions,
        )
        return

    if (directory / "astra.yaml").exists():
        console.print(
            f"[red]Error:[/red] [cyan]{directory}[/cyan] is already an ASTRA project."
        )
        raise SystemExit(1)

    if directory != Path("."):
        if directory.exists() and any(directory.iterdir()):
            if not click.confirm(
                f"[yellow]{directory}[/yellow] already exists and is not empty. Continue?"
            ):
                raise SystemExit(0)
        directory.mkdir(parents=True, exist_ok=True)

    for subdir in ("universes", "scripts", "results", ".lightcone"):
        (directory / subdir).mkdir(parents=True, exist_ok=True)

    _create_dagster_yaml(directory)
    _create_or_append_gitignore(directory)
    _create_boilerplate_astra_yaml(directory)
    _create_claude_md(directory)
    _create_claude_settings(directory, permissions, cluster=cluster)
    _create_lightcone_config(directory, cluster=cluster, permissions=permissions)
    _create_venv(directory, no_venv)
    _init_git_repo(directory, no_git)

    console.print(f"[green]✓[/green] Created ASTRA analysis project: [cyan]{directory}[/cyan]")
    if cluster:
        console.print(f"  Cluster: [cyan]{cluster}[/cyan]")
        from lightcone.engine.clusters import load_cluster_config
        if load_cluster_config(cluster) is None:
            console.print(
                f"  [yellow]Note:[/yellow] cluster '{cluster}' is not configured. "
                f"Run [cyan]lc cluster add {cluster}[/cyan] before running `lc run`."
            )

    from lightcone.engine.container import detect_container_runtime
    rt = detect_container_runtime()
    if rt:
        console.print(f"  Container runtime: [cyan]{rt}[/cyan]")
    else:
        console.print(
            "\n[yellow]Note:[/yellow] No container runtime detected. "
            "Recipes will run in the project venv."
        )

    console.print(
        "\n[bold yellow]Note:[/bold yellow] Telemetry is enabled by default. "
        "To disable, set [cyan]TRACE_TO_LANGFUSE=false[/cyan]."
    )
    console.print(f"\n[bold]cd {directory}[/bold] && [bold]claude[/bold]")
    console.print("Then run [cyan]/lc-new[/cyan] to scope your research question.")


_GITIGNORE_LINES = [
    "results/", "results/.dagster/", "__pycache__/",
    "*.py[cod]", ".venv/", ".ipynb_checkpoints/",
    ".DS_Store", ".langfuse/",
]


def _create_dagster_yaml(directory: Path) -> None:
    content = {"storage": {"sqlite": {"base_dir": "results/.dagster"}}}
    (directory / ".lightcone").mkdir(parents=True, exist_ok=True)
    (directory / ".lightcone" / "dagster.yaml").write_text(
        yaml.dump(content, default_flow_style=False, sort_keys=False)
    )


def _create_or_append_gitignore(directory: Path) -> None:
    path = directory / ".gitignore"
    if path.exists():
        existing = {line.strip() for line in path.read_text().splitlines()}
        missing = [line for line in _GITIGNORE_LINES if line not in existing]
        if missing:
            with open(path, "a") as f:
                f.write("\n# lightcone-cli / ASTRA\n" + "\n".join(missing) + "\n")
    else:
        path.write_text("# ASTRA Analysis\n" + "\n".join(_GITIGNORE_LINES) + "\n")


def _init_existing_project(
    directory: Path, *, source: Path,
    no_git: bool, no_venv: bool,
    cluster: str | None, permissions: str,
) -> None:
    """Add lightcone-cli infrastructure to an existing project."""
    source = source.resolve()

    if source.resolve() != directory.resolve():
        directory.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            if item.name.startswith(".") or item.name == "__pycache__":
                continue
            dest = directory / item.name
            if dest.exists():
                continue
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".git",
                ))
            else:
                shutil.copy2(item, dest)
        console.print(
            f"[green]✓[/green] Copied project from [cyan]{source}[/cyan] "
            f"to [cyan]{directory}[/cyan]"
        )

    if (directory / "astra.yaml").exists():
        console.print(
            f"[red]Error:[/red] [cyan]{directory}[/cyan] already has an astra.yaml."
        )
        raise SystemExit(1)

    console.print(
        f"[bold]Adding lightcone-cli infrastructure to: [cyan]{directory}[/cyan][/bold]\n"
    )
    for subdir in ("universes", "results", ".lightcone"):
        (directory / subdir).mkdir(parents=True, exist_ok=True)
    _create_dagster_yaml(directory)
    _create_or_append_gitignore(directory)

    if not (directory / "CLAUDE.md").exists():
        _create_claude_md(directory)
    if not (directory / "Containerfile").exists():
        (directory / "Containerfile").write_text(
            "FROM python:3.12-slim\n\nWORKDIR /app\n\n"
            "COPY requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n\n"
            "COPY . .\n"
        )
    if not (directory / "requirements.txt").exists():
        (directory / "requirements.txt").write_text("")

    _create_claude_settings(directory, permissions, cluster=cluster)
    _create_lightcone_config(directory, cluster=cluster, permissions=permissions)
    _create_venv(directory, no_venv)
    _init_git_repo(directory, no_git)

    console.print(
        f"\n[green]✓[/green] Added lightcone-cli infrastructure to: [cyan]{directory}[/cyan]"
    )
    console.print("\n[bold]Next steps:[/bold]")
    if directory != Path("."):
        console.print(f"  [bold]cd {directory}[/bold]")
    console.print("  [bold]claude[/bold]")
    console.print("  [cyan]/lc-migrate[/cyan]")


def _create_boilerplate_astra_yaml(directory: Path) -> None:
    name = directory.name if directory != Path(".") else "My Analysis"
    (directory / "astra.yaml").write_text(f"""# ASTRA Analysis Specification
version: "1.0"
name: "{name}"
description: |
  TODO: What research question are you trying to answer?

container: Containerfile

inputs:
  - id: primary_data
    type: data
    description: "TODO: Describe your primary data source"

outputs:
  - id: main_result
    type: metric
    description: "TODO: Describe your primary output metric"
    recipe:
      command: python scripts/compute.py

  - id: conclusion
    type: report
    description: "Summary addressing the problem statement"
    recipe:
      command: python scripts/summarize.py
      inputs: [main_result]

decisions:
  example_method:
    label: "Example Method Choice"
    tags: [analysis]
    rationale: "TODO: Explain why this decision matters"
    default: option_a
    options:
      option_a:
        label: "Option A"
        description: "TODO: Describe option A"
      option_b:
        label: "Option B"
        description: "TODO: Describe option B"
""")
    (directory / "Containerfile").write_text(
        "FROM python:3.12-slim\n\nWORKDIR /app\n\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n\n"
        "COPY . .\n"
    )
    (directory / "requirements.txt").write_text("numpy\npandas\n")
    (directory / "universes" / "baseline.yaml").write_text(
        "# Baseline Universe\n"
        "id: baseline\n"
        "description: \"Default configuration using standard practices\"\n\n"
        "decisions:\n"
        "  example_method: option_a\n"
    )


def _init_sub_analysis(directory: Path) -> None:
    """Scaffold a sub-analysis directory and wire it into the parent project."""
    from astra.helpers import load_yaml, save_yaml

    sub_path = directory
    if sub_path == Path("."):
        console.print("[red]Error:[/red] Please provide a name or path for the sub-analysis.")
        raise SystemExit(1)
    if len(sub_path.parts) == 1:
        sub_path = Path("analyses") / sub_path
    name = sub_path.name

    project_root = Path.cwd()
    if not (project_root / "astra.yaml").exists():
        console.print(
            "[red]Error:[/red] No astra.yaml found. Run this from the project root."
        )
        raise SystemExit(1)

    abs_sub_path = project_root / sub_path
    if abs_sub_path.exists() and (abs_sub_path / "astra.yaml").exists():
        console.print(
            f"[red]Error:[/red] Sub-analysis already exists at [cyan]{sub_path}[/cyan]."
        )
        raise SystemExit(1)

    abs_sub_path.mkdir(parents=True, exist_ok=True)
    (abs_sub_path / "scripts").mkdir(exist_ok=True)
    (abs_sub_path / "scripts" / ".gitkeep").touch()
    (abs_sub_path / "universes").mkdir(exist_ok=True)
    (abs_sub_path / "results").mkdir(exist_ok=True)

    label = name.replace("_", " ").replace("-", " ").title()
    save_yaml(
        {"name": label, "description": "", "inputs": [], "outputs": [], "decisions": {}},
        abs_sub_path / "astra.yaml",
    )
    save_yaml(
        {"id": "baseline", "description": "Default configuration", "decisions": {}},
        abs_sub_path / "universes" / "baseline.yaml",
    )
    _create_claude_md(abs_sub_path)

    root_spec = load_yaml(project_root / "astra.yaml")
    if "analyses" not in root_spec or root_spec["analyses"] is None:
        root_spec["analyses"] = {}
    root_spec["analyses"][name] = {"path": f"./{sub_path}"}
    save_yaml(root_spec, project_root / "astra.yaml")

    universes_dir = project_root / "universes"
    if universes_dir.is_dir():
        for ufile in sorted(universes_dir.glob("*.yaml")):
            udata = load_yaml(ufile)
            if udata is None:
                continue
            if "analyses" not in udata or udata["analyses"] is None:
                udata["analyses"] = {}
            udata["analyses"][name] = {"universe": "baseline"}
            save_yaml(udata, ufile)

    console.print(
        f"[green]✓[/green] Created sub-analysis [cyan]{name}[/cyan] at [cyan]{sub_path}[/cyan]"
    )


def _create_claude_md(directory: Path) -> None:
    name = directory.name if directory != Path(".") else "My Analysis"
    plugin_source = get_plugin_source_dir()
    template_path = plugin_source / "templates" / "CLAUDE.md" if plugin_source else None

    if template_path and template_path.exists():
        content = template_path.read_text().replace("{{name}}", name)
    else:
        content = (
            f"# CLAUDE.md\n\n## Project: {name}\n\n"
            "This is an ASTRA analysis project. Read `astra.yaml` for the specification.\n\n"
            "Run `/lc-new` to scope a research question.\n\n"
            "---\n\n"
            "<!-- AUTOGENERATED: /lc-new populates below during specification -->\n"
            "## Analysis Context\n\n"
            "_Run `/lc-new` to scope the research question and populate this section._\n"
        )
    (directory / "CLAUDE.md").write_text(content)


def _create_lightcone_config(
    directory: Path, *, cluster: str | None, permissions: str,
) -> None:
    """Create ``.lightcone/lightcone.yaml`` with optional cluster + permissions tier."""
    config: dict[str, Any] = {"permissions": permissions}
    if cluster:
        config["cluster"] = cluster
    (directory / ".lightcone").mkdir(parents=True, exist_ok=True)
    (directory / ".lightcone" / "lightcone.yaml").write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )
    msg = f"cluster: {cluster}" if cluster else "local execution"
    console.print(f"[green]✓[/green] Created .lightcone/lightcone.yaml ({msg})")


def _create_claude_settings(
    directory: Path, tier: str = "recommended", *, cluster: str | None = None,
) -> None:
    """Create Claude Code settings with lightcone-cli skills, hooks, and permissions.

    If a cluster is configured, its site's scratch-deny rules are merged into the
    permissions block.
    """
    claude_dir = directory / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    plugin_source = get_plugin_source_dir()
    if plugin_source is None:
        console.print(
            "[yellow]Warning:[/yellow] Could not find lightcone-cli plugin source."
        )
        return

    for subdir in ("scripts", "hooks", "skills", "agents", "guides"):
        src = plugin_source / subdir
        dst = claude_dir / subdir
        if not src.exists():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        if subdir == "scripts":
            for f in dst.glob("*.sh"):
                f.chmod(f.stat().st_mode | 0o111)
        elif subdir == "hooks":
            for f in dst.glob("*.py"):
                f.chmod(f.stat().st_mode | 0o111)

    permissions: dict[str, list[str]] = {
        k: list(v) for k, v in PERMISSION_TIERS[tier].items()
    }
    # If a cluster is configured, merge its site's scratch-deny rules.
    if cluster and "deny" in permissions:
        from lightcone.engine.clusters import load_cluster_config
        from lightcone.engine.site_registry import get_site_scratch_deny_rules
        cfg = load_cluster_config(cluster)
        if cfg:
            site_deny = get_site_scratch_deny_rules(cfg.get("site") or "")
            existing = set(permissions["deny"])
            for rule in site_deny:
                if rule not in existing:
                    permissions["deny"].append(rule)

    abs_hooks = str(directory.resolve() / ".claude" / "hooks")
    settings: dict[str, Any] = {
        "permissions": permissions,
        "hooks": {
            "SessionStart": [{"hooks": [
                {"type": "command", "command": ".claude/scripts/activate-venv.sh", "timeout": 5},
                {"type": "command", "command": ".claude/scripts/session-start.sh", "timeout": 10},
            ]}],
            "Stop": [{"matcher": "", "hooks": [
                {"type": "command",
                 "command": f"python3 {abs_hooks}/langfuse_hook.py",
                 "timeout": 30},
            ]}],
            "SessionEnd": [{"matcher": "", "hooks": [
                {"type": "command",
                 "command": f"python3 {abs_hooks}/langfuse_hook.py",
                 "timeout": 30},
            ]}],
            "PreToolUse": [{"matcher": "", "hooks": [
                {"type": "command",
                 "command": f"python3 {abs_hooks}/langfuse_session_init_hook.py",
                 "timeout": 10},
            ]}],
            "PostToolUse": [
                {"matcher": "Write|Edit", "hooks": [
                    {"type": "command",
                     "command": ".claude/scripts/validate-on-save.sh",
                     "timeout": 15},
                ]},
                {"matcher": "Bash", "hooks": [
                    {"type": "command",
                     "command": ".claude/scripts/check-lc-run.sh",
                     "timeout": 15},
                    {"type": "command",
                     "command": f"python3 {abs_hooks}/langfuse_git_commit_hook.py",
                     "timeout": 15},
                ]},
            ],
        },
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    (claude_dir / "settings.local.json").write_text(json.dumps({
        "env": {
            "TRACE_TO_LANGFUSE": "true",
            "LANGFUSE_PUBLIC_KEY": (
                "ced0ca0cf048a05ac1f272cf1e70693233f6932722738eadd6a56fa361f213cf"
            ),
            "LANGFUSE_SECRET_KEY": "relay",
            "LANGFUSE_HOST": "https://telemetry.lightconeresearch.workers.dev",
        },
    }, indent=2) + "\n")


def _init_git_repo(directory: Path, no_git: bool) -> None:
    if no_git or (directory / ".git").exists():
        return
    try:
        subprocess.run(["git", "init"], cwd=directory, capture_output=True, check=True)
        console.print("[green]✓[/green] Initialized git repository")
        try:
            subprocess.run(["git", "add", "."], cwd=directory, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial ASTRA analysis structure"],
                cwd=directory, capture_output=True, check=True,
            )
        except subprocess.CalledProcessError:
            pass
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _create_venv(directory: Path, no_venv: bool) -> bool:
    if no_venv:
        return False
    venv_path = directory / ".venv"
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning:[/yellow] Failed to create venv: {e}")
        return False
    console.print("[green]✓[/green] Created virtual environment (.venv)")

    pip_path = venv_path / ("Scripts" if sys.platform == "win32" else "bin") / "pip"
    try:
        subprocess.run(
            [str(pip_path), "install", "lightcone-cli"],
            capture_output=True, check=True,
        )
        console.print("[green]✓[/green] Installed lightcone-cli in venv")
    except subprocess.CalledProcessError:
        console.print(
            "[yellow]Warning:[/yellow] Could not install lightcone-cli automatically."
        )
    return True


# =============================================================================
# Run command — dispatches local vs cluster
# =============================================================================


@main.command()
@click.argument("outputs", nargs=-1)
@click.option("--universe", "-u", default=None, help="Universe to materialize for")
@click.option("--cluster", default=None, help="Run on the named cluster's Dask cluster")
@click.option("--local", "force_local", is_flag=True,
              help="Force local execution even if a cluster is configured")
@click.option("--no-build", is_flag=True, help="Skip automatic container image builds")
def run(
    outputs: tuple[str, ...],
    universe: str | None,
    cluster: str | None,
    force_local: bool,
    no_build: bool,
) -> None:
    """Materialize ASTRA outputs via dagster-dask.

    Without ``--cluster`` or a project default, ``dagster-dask`` spins up
    an ephemeral ``distributed.LocalCluster`` per run.  When a cluster is
    resolved, dispatches assets to its live Dask scheduler instead.

    Examples:
        lc run                              # all outputs, default universe
        lc run accuracy                     # specific output
        lc run -u baseline                  # specific universe
        lc run --cluster perlmutter         # explicit cluster
        lc run --local                      # force local even with cluster configured
    """
    import dagster as dg
    from dagster_dask import dask_executor

    from lightcone.engine.assets import build_definitions
    from lightcone.engine.clusters import (
        find_attached_cluster_for_job,
        find_running_cluster,
        resolve_cluster,
    )
    from lightcone.engine.dask_entrypoint import get_cluster_job

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    output_names = list(outputs)
    universe_id = universe or "baseline"

    cluster_name: str | None = None
    cluster_config: dict | None = None
    if not force_local:
        # Resolution order:
        #   1. --cluster X flag (handled inside resolve_cluster)
        #   2. Attached cluster matching $SLURM_JOB_ID (if no --cluster)
        #   3. Project default / single configured (resolve_cluster fallback)
        if cluster is None:
            slurm_job_id = os.environ.get("SLURM_JOB_ID")
            if slurm_job_id:
                attached = find_attached_cluster_for_job(slurm_job_id)
                if attached is not None and attached.record is not None:
                    cluster_name = attached.record.name
                    cluster_config = None  # attached clusters have no yaml
        if cluster_name is None:
            try:
                resolution = resolve_cluster(project_path, cli_cluster=cluster)
            except FileNotFoundError as e:
                console.print(f"[red]Error:[/red] {e}")
                raise SystemExit(1)
            if resolution is not None:
                cluster_name, cluster_config = resolution

    if output_names:
        selection = [dg.AssetKey([universe_id] + o.split(".")) for o in output_names]
    else:
        # No explicit outputs — enumerate every recipe-backed asset.
        defs = build_definitions(
            project_path, cluster_config=cluster_config, universe_id=universe_id,
            no_build=no_build, executor_def=dask_executor,
        )
        selection = [
            spec.key for spec in defs.resolve_all_asset_specs()
            if not (spec.metadata or {}).get("external", False)
        ]

    instance_dir = _resolve_dagster_instance_dir(project_path)
    if os.environ.get("SLURM_JOB_ID") and _scratch_dagster_dir(project_path) is not None:
        console.print(
            f"  [dim]Dagster storage: {instance_dir} "
            "(redirected to scratch for compute-node SQLite)[/dim]"
        )
    instance = dg.DagsterInstance.from_config(str(instance_dir))

    os.environ["LIGHTCONE_PROJECT_PATH"] = str(project_path)
    os.environ["LIGHTCONE_UNIVERSE"] = universe_id

    if cluster_name is None:
        os.environ["LIGHTCONE_CLUSTER"] = ""
        cluster_mode: dict[str, Any] = {"local": {}}
        location_label = "local"
    else:
        info = find_running_cluster(cluster_name)
        if info is None:
            console.print(
                f"[red]Error:[/red] No active cluster for '{cluster_name}'.\n"
                f"  Start one with: [cyan]lc cluster start {cluster_name}[/cyan]"
            )
            raise SystemExit(1)
        os.environ["LIGHTCONE_CLUSTER"] = cluster_name
        cluster_mode = {"existing": {"address": info.scheduler_address}}
        location_label = f"cluster '{cluster_name}' ({info.scheduler_address})"

    run_config = {"execution": {"config": {"cluster": cluster_mode}}}
    op_selection = [_asset_key_to_op_name(k, universe_id) for k in selection]

    console.print(f"[bold]Materializing outputs on {location_label}...[/bold]")
    try:
        result = dg.execute_job(
            get_cluster_job(), instance=instance,
            run_config=run_config, op_selection=op_selection or None,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)
    if result.success:
        console.print("[green]✓[/green] Materialization complete")
    else:
        console.print("[red]✗[/red] Materialization failed")
        raise SystemExit(1)


def _asset_key_to_op_name(key: Any, universe_id: str) -> str:
    """Translate ``AssetKey([universe, ...])`` to Dagster's auto-generated op name.

    Dagster names asset ops by joining the key parts with two underscores.
    """
    parts = list(getattr(key, "path", []) or [])
    return "__".join(parts) if parts else str(key)


# =============================================================================
# Build command
# =============================================================================


@main.command()
@click.option("--force", is_flag=True, help="Rebuild images even if they already exist")
@click.option(
    "--runtime", "-r",
    type=click.Choice(["docker", "podman", "podman-hpc"]),
    default=None,
    help="Container runtime to build with (auto-detected from cluster config)",
)
def build(force: bool, runtime: str | None) -> None:
    """Build container images from Containerfile specs in astra.yaml."""
    from astra.helpers import get_outputs, load_yaml, resolve_analysis_tree

    from lightcone.engine.container import (
        ContainerBuildError,
        is_containerfile,
        resolve_container_for_slurm,
        resolve_container_spec,
    )

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    if runtime is None:
        from lightcone.engine.clusters import resolve_cluster
        try:
            cluster_resolution = resolve_cluster(project_path, cli_cluster=None)
        except FileNotFoundError:
            cluster_resolution = None
        if cluster_resolution is not None:
            _, cluster_config = cluster_resolution
            from lightcone.engine.site_registry import get_site_defaults
            site_defaults = get_site_defaults(cluster_config.get("site", "")) or {}
            runtime = cluster_config.get("container_runtime") or site_defaults.get(
                "container_runtime", "podman-hpc"
            )
        if runtime is None:
            from lightcone.engine.container import detect_container_runtime
            runtime = detect_container_runtime()
            if runtime is None:
                console.print(
                    "[red]Error:[/red] No container runtime found (Docker or Podman)."
                )
                raise SystemExit(1)

    spec = load_yaml(project_path / "astra.yaml")
    spec = resolve_analysis_tree(spec, project_path)
    project_name = spec.get("name") or project_path.name

    build_specs: list[tuple[str, str]] = []
    raw_default = spec.get("container")
    if raw_default is not None:
        if is_containerfile(raw_default, project_path):
            build_specs.append(("analysis-level", raw_default))
        elif runtime != "docker":
            build_specs.append(("analysis-level", raw_default))

    for output_def in get_outputs(spec):
        recipe = output_def.get("recipe")
        if not recipe:
            continue
        raw = recipe.get("container")
        if raw is not None:
            label = f"recipe:{output_def.get('id', '?')}"
            if is_containerfile(raw, project_path):
                build_specs.append((label, raw))
            elif runtime != "docker":
                build_specs.append((label, raw))

    if not build_specs:
        console.print("[dim]No container build specs found in astra.yaml.[/dim]")
        return

    console.print(f"[bold]Found {len(build_specs)} container spec(s) (runtime: {runtime})[/bold]\n")
    for label, bspec in build_specs:
        try:
            if runtime == "podman-hpc":
                tag = resolve_container_for_slurm(
                    bspec, project_path, project_name, runtime, force=force,
                )
            else:
                tag = resolve_container_spec(
                    bspec, project_path, project_name, force=force, runtime=runtime,
                )
            console.print(f"  [green]ready[/green]  {label} -> {tag}")
        except ContainerBuildError as e:
            console.print(f"  [red]fail[/red]   {label}: {e}")


# =============================================================================
# Status command
# =============================================================================


def _status_label(s: str) -> str:
    if s == "materialized":
        return "[green]ok[/green]"
    if s == "pending":
        return "[dim]pending[/dim]"
    if s == "alias":
        return "[cyan]alias[/cyan]"
    return "[yellow]no recipe[/yellow]"


def _display_tree_status(name: str, groups: dict, all_status: dict[str, dict[str, str]]) -> None:
    from rich.tree import Tree

    for uid, universe_status in all_status.items():
        tree = Tree(f"[bold]{name}[/bold]  universe: {uid}")
        for analysis_id, outputs in groups.items():
            if analysis_id is None:
                for out_id, _ in outputs:
                    s = universe_status.get(out_id, "no_recipe")
                    tree.add(f"{out_id:40s} {_status_label(s)}")
            else:
                branch = tree.add(f"[bold cyan]{analysis_id}/[/bold cyan]")
                for out_id, _ in outputs:
                    qualified = f"{analysis_id}/{out_id}"
                    s = universe_status.get(qualified, "no_recipe")
                    branch.add(f"{out_id:40s} {_status_label(s)}")
        console.print(tree)


def _display_flat_status(
    name: str, outputs: list[tuple[str, dict]], all_status: dict[str, dict[str, str]]
) -> None:
    from rich.table import Table

    table = Table(title=f"{name} -- Output Status")
    table.add_column("Output", style="cyan")
    for uid in all_status:
        table.add_column(uid)
    for out_id, _ in outputs:
        if not out_id:
            continue
        row = [out_id]
        for _uid, universe_status in all_status.items():
            s = universe_status.get(out_id, "no_recipe")
            row.append(_status_label(s))
        table.add_row(*row)
    console.print(table)


@main.command()
@click.option("--universe", "-u", default=None, help="Show status for a specific universe")
def status(universe: str | None) -> None:
    """Show materialization status of all outputs."""
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.status import get_all_universe_status, get_output_status

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    spec = load_yaml(project_path / "astra.yaml")
    spec = resolve_analysis_tree(spec, project_path)
    name = spec.get("name", "Unknown")

    if universe:
        all_status = {universe: get_output_status(project_path, universe)}
    else:
        all_status = get_all_universe_status(project_path)

    if not all_status:
        console.print("[yellow]No universes found.[/yellow]")
        return

    from collections import OrderedDict

    from lightcone.engine.tree import collect_tree_outputs

    tree_outputs = collect_tree_outputs(spec)
    groups: OrderedDict[str | None, list[tuple[str, dict]]] = OrderedDict()
    for tree_out in tree_outputs:
        gid = tree_out.analysis_id
        groups.setdefault(gid, []).append((tree_out.output_id, tree_out.output_def))

    has_sub = any(k is not None for k in groups)
    if has_sub:
        _display_tree_status(name, groups, all_status)
    else:
        _display_flat_status(name, groups.get(None, []), all_status)

    recipe_count = total_outputs = materialized_count = total_cells = 0
    for tree_out in tree_outputs:
        out_id = tree_out.output_id
        if not out_id:
            continue
        total_outputs += 1
        has_recipe = bool(tree_out.output_def.get("recipe"))
        if has_recipe:
            recipe_count += 1
        qualified = f"{tree_out.analysis_id}/{out_id}" if tree_out.analysis_id else out_id
        for _uid, universe_status in all_status.items():
            if has_recipe:
                total_cells += 1
            if universe_status.get(qualified) == "materialized":
                materialized_count += 1
    console.print(f"\n  Recipes: {recipe_count}/{total_outputs} outputs integrated")
    console.print(f"  Materialized: {materialized_count}/{total_cells} runs")

    from lightcone.engine.container import detect_container_runtime, get_container_status
    raw_container = spec.get("container")
    rt = detect_container_runtime() or "docker"
    cstatus = get_container_status(raw_container, project_path, name, runtime=rt)
    if cstatus.type == "prebuilt":
        console.print(f"  Container: prebuilt [cyan]{cstatus.image}[/cyan]")
    elif cstatus.type == "build":
        marker = "[green](built)[/green]" if cstatus.exists else "[yellow](not built)[/yellow]"
        console.print(f"  Container: build {cstatus.containerfile} {cstatus.image} {marker}")


# =============================================================================
# Dev command
# =============================================================================


@main.command()
@click.option("--port", "-p", default=3000, type=int, help="Port for Dagster webserver")
@click.option("--universe", "-u", default="baseline", help="Universe to load definitions for")
def dev(port: int, universe: str) -> None:
    """Launch Dagster webserver UI for the current project."""
    import tempfile

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    console.print(f"[bold]Starting Dagster webserver on port {port}...[/bold]")
    console.print(f"  Open [cyan]http://localhost:{port}[/cyan] in your browser")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    defs_code = (
        "from pathlib import Path\n"
        "from lightcone.engine.assets import build_definitions\n"
        f"defs = build_definitions(Path({str(project_path)!r}), "
        f"universe_id={universe!r}, no_build=True)\n"
    )

    defs_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="lightcone_defs_", delete=False,
        ) as f:
            f.write(defs_code)
            defs_file = f.name

        dagster_yaml_path = _find_dagster_yaml(project_path)
        dagster_home = str(dagster_yaml_path.parent) if dagster_yaml_path else str(project_path)
        env = {**os.environ, "DAGSTER_HOME": dagster_home}
        subprocess.run(
            ["dagster-webserver", "-f", defs_file, "-h", "0.0.0.0", "-p", str(port)],
            check=True, env=env,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Dagster webserver stopped[/dim]")
    except FileNotFoundError:
        console.print("[red]Error:[/red] dagster-webserver not found.")
        raise SystemExit(1)
    finally:
        if defs_file is not None:
            Path(defs_file).unlink(missing_ok=True)


# =============================================================================
# Cluster command group
# =============================================================================


@main.group()
def cluster() -> None:
    """Manage long-lived SLURM Dask clusters — `lc cluster --help` for subcommands."""


@cluster.command("list")
def cluster_list() -> None:
    """List configured + attached clusters and their live state."""
    from rich.table import Table

    from lightcone.engine.clusters import (
        cluster_info,
        list_attached_clusters,
        list_clusters,
    )

    yaml_names = list_clusters()
    attached = list_attached_clusters()
    if not yaml_names and not attached:
        console.print(
            "No clusters configured or attached. "
            "Run [cyan]lc cluster add[/cyan] to create one, or "
            "[cyan]lc cluster attach[/cyan] from inside a SLURM allocation."
        )
        return

    table = Table(title="Clusters")
    table.add_column("Name", style="cyan")
    table.add_column("Mode")
    table.add_column("Site")
    table.add_column("State")
    table.add_column("Job ID")
    table.add_column("Scheduler")

    rendered: set[str] = set()
    for name in yaml_names:
        info = cluster_info(name)
        if info is None:
            continue
        rendered.add(name)
        _render_cluster_row(table, name, info)
    for record in attached:
        if record.name in rendered:
            continue
        info = cluster_info(record.name)
        if info is None:
            continue
        _render_cluster_row(table, record.name, info)
    console.print(table)


def _render_cluster_row(table: Any, name: str, info: Any) -> None:
    """Append one row to the cluster list table."""
    state = info.state
    mode = info.record.mode if info.record else "—"
    job_id = info.record.job_id if info.record else "—"
    sched = info.scheduler_address or "—"
    colour = {
        "RUNNING": "green", "PENDING": "yellow", "NONE": "dim",
    }.get(state, "red")
    table.add_row(
        name, mode, info.spec.site,
        f"[{colour}]{state}[/{colour}]", job_id, sched,
    )


@cluster.command("add")
@click.argument("name", required=False)
@click.option("--site", default=None, help="Site key (defaults to detection from hostname)")
def cluster_add(name: str | None, site: str | None) -> None:
    """Create a new cluster YAML, prefilled from the site registry.

    The wizard asks only for the SLURM ``account``.  Everything else
    (qos, walltime, container_runtime, scratch_root, worker_init) comes
    from the site defaults.  Opens the file in ``$EDITOR`` so you can
    inspect or tweak it before running ``lc cluster start``.
    """
    import socket

    from lightcone.engine.clusters import save_cluster_config
    from lightcone.engine.site_registry import (
        SITE_DEFAULTS,
        detect_site,
        get_site_defaults,
        list_known_sites,
    )

    if site is None:
        site = detect_site(socket.gethostname()) or detect_site(name or "")
    if site is None:
        console.print("[bold]Known sites:[/bold]")
        for key, display in list_known_sites():
            console.print(f"  - {key}  ({display})")
        site = click.prompt("Site", type=click.Choice(list(SITE_DEFAULTS)))

    site_def = get_site_defaults(site) or {}
    # `lc cluster add` defaults to type=slurm today; the next k8s patch
    # will pick the right block based on the chosen type.
    cluster_defaults = site_def.get("slurm", {})
    suggested = site_def.get("suggested_options", {})

    cluster_name = name or site
    account = click.prompt("  SLURM account/allocation", default="")
    qos = cluster_defaults.get("default_qos") or (
        suggested.get("qos", {}).get("default") or "regular"
    )
    walltime = cluster_defaults.get("default_walltime") or "24h"
    constraint = (suggested.get("constraint") or {}).get("default")

    config: dict[str, Any] = {
        "type": "slurm",
        "site": site,
        "account": account,
        "qos": qos,
        "walltime": walltime,
        "workers": [
            {
                "nodes": 4,
                "threads_per_node": 64,
                "memory": "200GB",
                **({"constraint": constraint} if constraint else {}),
            },
        ],
    }
    path = save_cluster_config(cluster_name, config)
    console.print(f"\n[green]✓[/green] Wrote {path}")

    if click.confirm(f"Open {path} in $EDITOR for review?", default=True):
        editor = os.environ.get("EDITOR", "vi")
        subprocess.call([editor, str(path)])


@cluster.command("edit")
@click.argument("name")
def cluster_edit(name: str) -> None:
    """Open a cluster's YAML in ``$EDITOR``."""
    from lightcone.engine.clusters import get_clusters_dir, load_cluster_config

    if load_cluster_config(name) is None:
        console.print(f"[red]Error:[/red] No cluster named '{name}'.")
        raise SystemExit(1)
    path = get_clusters_dir() / f"{name}.yaml"
    editor = os.environ.get("EDITOR", "vi")
    subprocess.call([editor, str(path)])


@cluster.command("start")
@click.argument("name", required=False)
@click.option("--qos", default=None, help="Override the cluster's qos for this submission")
@click.option("--walltime", default=None, help="Override walltime (e.g. 30m, 24h)")
@click.option("--strategy", type=click.Choice(["fit", "switch"]), default="fit",
              help="QoS preflight strategy when limits are exceeded (default: fit)")
@click.option("--wait/--detach", "wait_for_ready", default=True,
              help="Block until the Dask scheduler is reachable (default: --wait)")
def cluster_start(
    name: str | None, qos: str | None, walltime: str | None,
    strategy: str, wait_for_ready: bool,
) -> None:
    """Submit a *named, configured* cluster via ``sbatch``.

    Idempotent — if the named cluster is already running, prints its
    address and exits.  For "use the SLURM allocation I'm already in",
    use [cyan]lc cluster attach[/cyan] instead.

    With no NAME, prints a status table of configured + attached clusters
    and exits with code 1 — pick one explicitly.
    """
    from lightcone.engine.clusters import start_cluster, wait_for_scheduler

    if name is None:
        _print_cluster_status_for_picker()
        raise SystemExit(1)

    overrides: dict[str, Any] = {}
    if qos:
        overrides["qos"] = qos
    if walltime:
        overrides["walltime"] = walltime

    try:
        info = start_cluster(name, overrides=overrides, strategy=strategy)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    job_id = info.record.job_id if info.record else "?"
    if info.state in {"RUNNING"} and info.scheduler_address:
        # Idempotent short-circuit.
        console.print(
            f"[green]✓[/green] Cluster '{name}' already RUNNING (job {job_id}). "
            f"Scheduler: [cyan]{info.scheduler_address}[/cyan]"
        )
        return

    console.print(f"[green]✓[/green] Submitted cluster '{name}' as job {job_id}")

    if wait_for_ready:
        console.print("  Waiting for Dask scheduler to come up...")
        try:
            info = wait_for_scheduler(name)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise SystemExit(1)
        console.print(
            f"[green]✓[/green] Cluster '{name}' RUNNING. Scheduler: "
            f"[cyan]{info.scheduler_address}[/cyan]"
        )


@cluster.command("attach")
@click.option("--wait/--detach", "wait_for_ready", default=True,
              help="Block until the Dask scheduler is reachable (default: --wait)")
def cluster_attach(wait_for_ready: bool) -> None:
    """Spawn a Dask cluster inside the current SLURM allocation.

    Run from inside a [cyan]salloc[/cyan] shell.  No name or yaml needed:
    worker layout is derived from SLURM env vars.  The cluster lives until
    the allocation ends or [cyan]lc cluster stop[/cyan] is called.
    """
    from lightcone.engine.clusters import attach_cluster, wait_for_scheduler

    try:
        info = attach_cluster()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    assert info.record is not None
    name = info.record.name
    job_id = info.record.job_id
    nodes = int(os.environ.get("SLURM_JOB_NUM_NODES", "1"))
    console.print(
        f"[green]✓[/green] Attaching cluster to SLURM allocation {job_id} "
        f"({nodes} node{'s' if nodes != 1 else ''})..."
    )

    if wait_for_ready:
        console.print("  Waiting for Dask scheduler to come up...")
        try:
            info = wait_for_scheduler(name)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise SystemExit(1)
        console.print(
            f"[green]✓[/green] Cluster '{name}' RUNNING. Scheduler: "
            f"[cyan]{info.scheduler_address}[/cyan]"
        )


def _print_cluster_status_for_picker() -> None:
    """Print a status table + hint when ``lc cluster start`` is called with no name."""
    from rich.table import Table

    from lightcone.engine.clusters import (
        cluster_info,
        list_attached_clusters,
        list_clusters,
    )

    yaml_names = list_clusters()
    attached = list_attached_clusters()
    in_allocation = bool(os.environ.get("SLURM_JOB_ID"))

    if not yaml_names and not attached:
        console.print(
            "[red]Error:[/red] No clusters configured or attached.\n"
            "  • [cyan]lc cluster add[/cyan] to create one\n"
            "  • [cyan]lc cluster attach[/cyan] from inside a SLURM allocation"
        )
        return

    table = Table(title="Clusters — pick one to start")
    table.add_column("Name", style="cyan")
    table.add_column("Mode")
    table.add_column("Site")
    table.add_column("State")
    table.add_column("Job ID")
    table.add_column("Scheduler")
    rendered: set[str] = set()
    for n in yaml_names:
        info = cluster_info(n)
        if info is None:
            continue
        rendered.add(n)
        _render_cluster_row(table, n, info)
    for record in attached:
        if record.name in rendered:
            continue
        info = cluster_info(record.name)
        if info is None:
            continue
        _render_cluster_row(table, record.name, info)
    console.print(table)

    attach_hint = (
        "use your current SLURM allocation"
        if in_allocation
        else "first do `salloc …`, then attach"
    )
    console.print(
        "\n[bold]Be explicit about which cluster to start:[/bold]\n"
        "  • [cyan]lc cluster start <name>[/cyan]  — submit a named cluster via sbatch\n"
        f"  • [cyan]lc cluster attach[/cyan]            — {attach_hint}\n"
    )


@cluster.command("stop")
@click.argument("name", required=False)
def cluster_stop(name: str | None) -> None:
    """Tear down a cluster.

    For ``sbatch`` clusters this ``scancel``s the job; for ``attached``
    clusters it kills the dask processes and leaves the salloc allocation
    intact.  With no NAME, picks the only running cluster if there is one.
    """
    from lightcone.engine.clusters import (
        list_attached_clusters,
        list_clusters,
        stop_cluster,
    )

    if name is None:
        candidates = list(list_clusters()) + [r.name for r in list_attached_clusters()]
        if len(candidates) != 1:
            console.print(
                "[red]Error:[/red] Specify a cluster name.\n"
                f"  Known: {', '.join(candidates) or 'none'}"
            )
            raise SystemExit(1)
        name = candidates[0]
    stop_cluster(name)
    console.print(f"[green]✓[/green] Cluster '{name}' stopped")


@cluster.command("status")
@click.argument("name", required=False)
def cluster_status(name: str | None) -> None:
    """Show the live state of a cluster (or all clusters + attached)."""
    from lightcone.engine.clusters import (
        cluster_info,
        list_attached_clusters,
        list_clusters,
    )

    if name is not None:
        names = [name]
    else:
        names = list(list_clusters())
        for r in list_attached_clusters():
            if r.name not in names:
                names.append(r.name)
    if not names:
        console.print("No clusters configured or attached.")
        return
    for n in names:
        info = cluster_info(n)
        if info is None:
            console.print(f"  [red]✗[/red] {n}: not configured")
            continue
        state = info.state
        mode = info.record.mode if info.record else "—"
        console.print(
            f"[bold]{n}[/bold]  mode={mode}  site={info.spec.site}  state={state}"
        )
        if info.record:
            console.print(f"  job_id: {info.record.job_id}")
            console.print(f"  submitted: {info.record.submitted_at}")
            if info.record.walltime_seconds:
                console.print(f"  walltime: {info.record.walltime_seconds // 60}m")
            console.print(f"  scheduler_file: {info.record.scheduler_file}")
            if info.record.mode == "attached" and info.record.process_pids:
                console.print(f"  pids: {info.record.process_pids}")
        if info.scheduler_address:
            console.print(f"  scheduler: [cyan]{info.scheduler_address}[/cyan]")


@cluster.command("logs")
@click.argument("name", required=False)
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
@click.option("-n", default=200, type=int, help="Number of lines to tail (default 200)")
def cluster_logs(name: str | None, follow: bool, n: int) -> None:
    """Tail the SLURM stdout log for a cluster."""
    from lightcone.engine.clusters import list_clusters, tail_cluster_logs

    if name is None:
        clusters = list_clusters()
        if len(clusters) != 1:
            console.print("[red]Error:[/red] Specify a cluster name.")
            raise SystemExit(1)
        name = clusters[0]
    try:
        tail_cluster_logs(name, follow=follow, lines=n)
    except (FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)


@cluster.command("refresh-cache")
@click.argument("site", required=False)
def cluster_refresh_cache(site: str | None) -> None:
    """Re-query SLURM (sacctmgr/scontrol) and rewrite the cluster cache for SITE."""
    from lightcone.engine.clusters import list_clusters, load_cluster_config, refresh_cluster_cache

    if site is None:
        sites: set[str] = {
            s for name in list_clusters()
            if (s := (load_cluster_config(name) or {}).get("site"))
        }
        if len(sites) != 1:
            console.print("[red]Error:[/red] Specify a site explicitly.")
            console.print(
                f"  Sites in use: {', '.join(sorted(sites)) or 'none'}"
            )
            raise SystemExit(1)
        site = next(iter(sites))
    assert site is not None
    info = refresh_cluster_cache(site)
    console.print(
        f"[green]✓[/green] Cluster cache refreshed for site '{site}' "
        f"({len(info.qos)} QoS records)"
    )


# =============================================================================
# Update command — sync plugin files into existing projects
# =============================================================================


_CLAUDE_MD_SEPARATOR = "## Analysis Context"


def _sync_project_plugins(project_dir: Path) -> bool:
    """Sync plugin files into a project's ``.claude/`` directory."""
    if not (project_dir / "astra.yaml").exists():
        console.print(f"  [red]✗[/red] {project_dir}: not an ASTRA project (no astra.yaml)")
        return False

    plugin_source = get_plugin_source_dir()
    if plugin_source is None:
        console.print("  [red]✗[/red] Could not find lightcone-cli plugin source.")
        return False

    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    for subdir in ("scripts", "hooks", "skills", "agents", "guides"):
        src = plugin_source / subdir
        dst = claude_dir / subdir
        if not src.exists():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        if subdir == "scripts":
            for f in dst.glob("*.sh"):
                f.chmod(f.stat().st_mode | 0o111)
        elif subdir == "hooks":
            for f in dst.glob("*.py"):
                f.chmod(f.stat().st_mode | 0o111)

    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        existing = claude_md.read_text()
        sep_idx = existing.find(_CLAUDE_MD_SEPARATOR)
        if sep_idx != -1:
            user_section = existing[sep_idx:]
        else:
            user_section = (
                f"{_CLAUDE_MD_SEPARATOR}\n\n"
                "_Run `/lc-new` to scope the research question and populate "
                "this section._\n"
            )
        name = project_dir.name
        template_path = plugin_source / "templates" / "CLAUDE.md"
        if template_path.exists():
            template = template_path.read_text().replace("{{name}}", name)
            template_sep_idx = template.find(_CLAUDE_MD_SEPARATOR)
            managed_section = (
                template[:template_sep_idx] if template_sep_idx != -1 else template + "\n"
            )
        else:
            managed_section = (
                f"# CLAUDE.md\n\n## Project: {name}\n\n"
                "ASTRA analysis project, built with lightcone-cli.\n\n---\n\n"
                "<!-- AUTOGENERATED: /lc-new populates below during specification -->\n"
            )
        claude_md.write_text(managed_section + user_section)

    console.print(f"  [green]✓[/green] {project_dir}")
    return True


@main.command()
@click.option("--sync", is_flag=True, help="Only sync plugin files (skip pip upgrade)")
@click.argument("projects", nargs=-1, type=click.Path(exists=True, path_type=Path))
def update(sync: bool, projects: tuple[Path, ...]) -> None:
    """Upgrade lightcone-cli and sync plugin files into PROJECTS.

    Examples:
        lc update                    # upgrade and prompt for projects to sync
        lc update --sync             # only sync (no upgrade)
        lc update --sync .           # sync the current project
    """
    if not sync:
        console.print("[bold]Upgrading lightcone-cli...[/bold]\n")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "lightcone-cli"],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            console.print("  [green]✓[/green] lightcone-cli upgraded")
        else:
            console.print(f"  [red]✗[/red] upgrade failed: {proc.stderr.strip()[:200]}")
            raise SystemExit(1)

    if projects:
        for p in projects:
            _sync_project_plugins(p.resolve())
        return

    raw = click.prompt(
        "\n  Enter project paths (comma-separated), or skip",
        default="skip",
    )
    if raw.strip().lower() in ("skip", "s", ""):
        return
    paths = [Path(p.strip()).expanduser().resolve() for p in raw.split(",") if p.strip()]
    for p in paths:
        _sync_project_plugins(p)


# =============================================================================
# Optional eval subgroup
# =============================================================================


try:
    from lightcone.eval.cli import eval_group

    main.add_command(eval_group, "eval")
except ImportError:
    pass


if __name__ == "__main__":
    main()
