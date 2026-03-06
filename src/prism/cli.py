"""Command-line interface for Prism — the ASTRA-compliant agentic layer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console

console = Console()

PERMISSION_TIERS = {
    "yolo": {
        "allow": [
            "Bash(*)",
            "Edit",
            "Read",
            "Write",
            "WebSearch",
            "WebFetch",
            "mcp__*",
        ],
    },
    "recommended": {
        "allow": [
            "Bash(astra:*)",
            "Bash(prism:*)",
            "Bash(python:*)",
            "Bash(pip:*)",
            "Bash(git status:*)",
            "Bash(git log:*)",
            "Bash(git diff:*)",
            "Bash(git add:*)",
            "Bash(git commit:*)",
            "Bash(git branch:*)",
            "Bash(git checkout:*)",
            "Bash(git switch:*)",
            "Edit",
            "WebSearch",
            "WebFetch",
        ],
    },
    "minimal": {
        "allow": ["Read"],
    },
}


def _get_plugin_source_dir() -> Path | None:
    """Find the Prism plugin source directory.

    Looks for the plugin files in:
    1. Bundled location (installed package): prism/claude/prism/
    2. Development location (repo): claude/prism/ relative to repo root
    """
    import prism

    package_dir = Path(prism.__file__).parent
    bundled_plugin = package_dir / "claude" / "prism"
    if bundled_plugin.exists():
        return bundled_plugin

    # Try development location (running from repo)
    repo_root = package_dir.parent.parent
    dev_plugin = repo_root / "claude" / "prism"
    if dev_plugin.exists():
        return dev_plugin

    return None


@click.group()
@click.version_option()
@click.pass_context
def main(ctx: click.Context) -> None:
    """Prism - ASTRA-compliant Agentic Layer CLI."""
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand in ("setup", "target", "update"):
        return
    from prism.dagster.targets import get_config_path
    if not get_config_path().exists():
        console.print(
            "\n[bold yellow]No execution environment configured.[/bold yellow]"
        )
        console.print(
            "  Prism needs a default target configured before you can use it.\n"
        )
        ctx.invoke(setup)
        return

    # Check for updates (cached, non-blocking)
    try:
        from prism.updater import check_for_updates
        msg = check_for_updates()
        if msg:
            console.print(f"\n  [yellow]↑[/yellow] {msg}\n")
    except Exception:
        pass  # Never let update checks break normal usage


# =============================================================================
# Init command
# =============================================================================


@main.command()
@click.argument("directory", type=click.Path(path_type=Path), default=".")
@click.option("--no-git", is_flag=True, help="Don't initialize git repository")
@click.option("--no-venv", is_flag=True, help="Don't create Python virtual environment")
@click.option("--target", "-t", default=None, help="Execution target name")
@click.option(
    "--permissions",
    type=click.Choice(["yolo", "recommended", "minimal"]),
    default=None,
    help="Claude Code permission tier (default: prompt or saved default)",
)
def init(
    directory: Path, no_git: bool, no_venv: bool,
    target: str | None, permissions: str | None,
) -> None:
    """Create a new ASTRA analysis project with full agentic scaffolding.

    Creates the project with ASTRA specification files, Claude Code plugin
    configuration, skills, hooks, and a Python virtual environment.

    DIRECTORY is the project folder to create (default: current directory).

    Examples:
        prism init my-analysis
        prism init my-analysis --target perlmutter-gpu
        prism init my-analysis --no-git --no-venv
    """
    # Check if this is already an ASTRA project
    if (directory / "astra.yaml").exists():
        console.print(
            f"[red]Error:[/red] [cyan]{directory}[/cyan] is already an ASTRA project "
            f"(astra.yaml exists)."
        )
        console.print("Use [cyan]astra validate[/cyan] to check it, or delete astra.yaml to re-init.")
        raise SystemExit(1)

    # Create project directory
    if directory != Path("."):
        if directory.exists() and any(directory.iterdir()):
            if not click.confirm(
                f"[yellow]{directory}[/yellow] already exists and is not empty. Continue?"
            ):
                raise SystemExit(0)
        directory.mkdir(parents=True, exist_ok=True)

    # Create directory structure
    subdirs = [
        "universes",
        "scripts",
        "results",
        "plans",
    ]
    for subdir in subdirs:
        (directory / subdir).mkdir(parents=True, exist_ok=True)

    # Create dagster.yaml for Dagster instance configuration
    dagster_yaml_content = {
        "storage": {
            "sqlite": {
                "base_dir": "results/.dagster",
            },
        },
    }
    (directory / "dagster.yaml").write_text(
        yaml.dump(dagster_yaml_content, default_flow_style=False, sort_keys=False)
    )

    # Create .gitignore
    gitignore = """# ASTRA Analysis
results/
results/.dagster/
__pycache__/
*.py[cod]
.venv/
.ipynb_checkpoints/
.DS_Store
"""
    (directory / ".gitignore").write_text(gitignore)

    # Create boilerplate astra.yaml
    _create_boilerplate_astra_yaml(directory)

    # Create CLAUDE.md with project conventions
    _create_claude_md(directory)

    # Resolve permission tier and create Claude Code settings
    tier = _resolve_permission_tier(permissions)
    _create_claude_settings(directory, tier)

    # Write prism.yaml project config — use --target flag, or fall back to
    # the user's default target from ~/.prism/config.yaml
    effective_target = target
    if not effective_target:
        from prism.dagster.targets import load_user_config
        effective_target = load_user_config().get("default_target", "local")
    _create_prism_config(directory, effective_target)

    # If user explicitly passed --target, ensure it's been configured
    if target and target != "local":
        from prism.dagster.targets import load_target
        if load_target(target) is None:
            console.print(
                f"\n[yellow]Target [cyan]{target}[/cyan] "
                "is not configured yet.[/yellow]"
            )
            console.print(
                "  Run [cyan]prism setup[/cyan] to configure execution targets."
            )

    # Create virtual environment
    _create_venv(directory, no_venv)

    # Initialize git repository
    _init_git_repo(directory, no_git)

    # Print success message
    console.print(f"[green]✓[/green] Created ASTRA analysis project: [cyan]{directory}[/cyan]")
    if target:
        console.print(f"  Target: [cyan]{target}[/cyan]")

    console.print(
        "\n[bold yellow]Note:[/bold yellow] Telemetry is enabled by default. "
        "Claude Code sessions in this project will be traced to Langfuse.\n"
        "  To disable, set [cyan]TRACE_TO_LANGFUSE=false[/cyan] "
        "in [cyan].claude/settings.local.json[/cyan]."
    )

    console.print(f"\n[bold]cd {directory}[/bold] && [bold]claude[/bold]")
    console.print("Then run [cyan]/prism-new[/cyan] to scope your research question.")


def _create_boilerplate_astra_yaml(directory: Path) -> None:
    """Create boilerplate astra.yaml with TODOs."""

    name = directory.name if directory != Path(".") else "My Analysis"

    astra_yaml = f"""# ASTRA Analysis Specification
# Documentation: https://github.com/LightconeResearch/ASTRA

version: "1.0"
name: "{name}"
description: |
  TODO: What research question are you trying to answer?

container:
  build: Containerfile

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
"""
    (directory / "astra.yaml").write_text(astra_yaml)

    # Create Containerfile
    containerfile = """\
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
"""
    (directory / "Containerfile").write_text(containerfile)

    # Create requirements.txt
    requirements = """\
numpy
pandas
"""
    (directory / "requirements.txt").write_text(requirements)

    # Create baseline universe
    baseline_universe = """# Baseline Universe
# Default configuration using standard practices

id: baseline
description: "Default configuration using standard practices"

decisions:
  example_method: option_a
"""
    (directory / "universes" / "baseline.yaml").write_text(baseline_universe)

    # Create README
    _create_readme(directory, name)


def _create_readme(directory: Path, name: str) -> None:
    """Create a README.md for the project."""
    readme = f"""# {name}

An ASTRA (Agentic Schema for Transparent Research Analysis) analysis project, built with Prism.

## Quick Start

```bash
# Open in Claude Code
claude

# Scope the analysis
/prism-new

# Then start building (Claude reads CLAUDE.md for conventions)
```

## Structure

- `astra.yaml` — Analysis specification (source of truth)
- `prism.yaml` — Prism config (compute profiles)
- `CLAUDE.md` — Build conventions and project context for Claude Code
- `universes/` — Decision selections (one YAML per universe)
- `scripts/` — Implementation scripts
- `results/` — Execution outputs (gitignored)

## Documentation

See [ASTRA documentation](https://github.com/LightconeResearch/ASTRA) for the specification.
See [Prism documentation](https://github.com/LightconeResearch/Prism) for the agentic layer.
"""
    (directory / "README.md").write_text(readme)


def _create_claude_md(directory: Path) -> None:
    """Create CLAUDE.md from the template in the plugin source."""
    name = directory.name if directory != Path(".") else "My Analysis"

    # Find the template
    plugin_source = _get_plugin_source_dir()
    template_path = plugin_source / "templates" / "CLAUDE.md" if plugin_source else None

    if template_path and template_path.exists():
        content = template_path.read_text()
        content = content.replace("{{name}}", name)
    else:
        # Fallback: minimal CLAUDE.md if template not found
        content = (
            f"# CLAUDE.md\n\n## Project: {name}\n\n"
            "This is an ASTRA analysis project. Read `astra.yaml` for the specification.\n\n"
            "Run `/prism-new` to scope a research question.\n\n"
            "---\n\n"
            "<!-- AUTOGENERATED: /prism-new populates below during specification -->\n"
            "## Analysis Context\n\n"
            "_Run `/prism-new` to scope the research question and populate this section._\n"
        )

    (directory / "CLAUDE.md").write_text(content)


def _create_prism_config(directory: Path, target_name: str) -> None:
    """Create prism.yaml with target reference."""
    config = {
        "target": target_name,
    }
    (directory / "prism.yaml").write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )
    console.print(f"[green]✓[/green] Created prism.yaml (target: {target_name})")



def _prompt_permission_tier() -> str:
    """Interactively prompt the user to choose a permission tier.

    Returns one of: 'yolo', 'recommended', 'minimal'.
    Saves the choice as the default for future projects.
    """
    console.print("\n[bold]Claude Code permission level[/bold]")
    console.print("  Controls what Claude can do without asking.\n")
    console.print("    1. yolo — Everything auto-allowed. No prompts.")
    console.print("    2. recommended — Prism workflow auto-allowed. Prompts for the rest.")
    console.print("    3. minimal — Only file reading. Everything else prompts.")

    choice_map = {"1": "yolo", "2": "recommended", "3": "minimal"}
    raw = click.prompt(
        "\n  Select permission level",
        type=click.Choice(["1", "2", "3"]),
        default="2",
    )
    tier = choice_map.get(raw, "recommended")

    from prism.dagster.targets import load_user_config, save_user_config
    global_config = load_user_config()
    global_config["default_permission_tier"] = tier
    save_user_config(global_config)
    console.print(f"  [green]✓[/green] Permissions: {tier}")

    return tier


def _resolve_permission_tier(flag_value: str | None) -> str:
    """Resolve which permission tier to use.

    Priority:
    1. --permissions flag (explicit override)
    2. Saved default in ~/.prism/config.yaml
    3. Interactive prompt (first time only)
    """
    # 1. Explicit flag
    if flag_value is not None:
        console.print(f"  Permissions: [cyan]{flag_value}[/cyan] (--permissions flag)")
        return flag_value

    # 2. Saved default
    from prism.dagster.targets import load_user_config
    global_config = load_user_config()
    saved = global_config.get("default_permission_tier")
    if saved:
        console.print(f"  Permissions: [cyan]{saved}[/cyan] (saved default)")
        return saved

    # 3. Interactive prompt
    return _prompt_permission_tier()


def _create_claude_settings(directory: Path, tier: str = "recommended") -> None:
    """Create Claude Code settings with Prism skills and agents."""
    claude_dir = directory / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Find the plugin source directory
    plugin_source = _get_plugin_source_dir()
    if plugin_source is None:
        console.print(
            "[yellow]Warning:[/yellow] Could not find Prism plugin source files. "
            "Claude Code skills will not be available."
        )
        return

    # Copy scripts
    scripts_src = plugin_source / "scripts"
    scripts_dst = claude_dir / "scripts"
    if scripts_src.exists():
        if scripts_dst.exists():
            shutil.rmtree(scripts_dst)
        shutil.copytree(scripts_src, scripts_dst)
        # Make scripts executable
        for script in scripts_dst.glob("*.sh"):
            script.chmod(script.stat().st_mode | 0o111)

    # Copy hooks
    hooks_src = plugin_source / "hooks"
    hooks_dst = claude_dir / "hooks"
    if hooks_src.exists():
        if hooks_dst.exists():
            shutil.rmtree(hooks_dst)
        shutil.copytree(hooks_src, hooks_dst)
        # Make .py files executable
        for hook in hooks_dst.glob("*.py"):
            hook.chmod(hook.stat().st_mode | 0o111)

    # Copy skills
    skills_src = plugin_source / "skills"
    skills_dst = claude_dir / "skills"
    if skills_src.exists():
        if skills_dst.exists():
            shutil.rmtree(skills_dst)
        shutil.copytree(skills_src, skills_dst)

    permissions = PERMISSION_TIERS[tier]

    # Build absolute paths for hook commands
    abs_hooks = str(directory.resolve() / ".claude" / "hooks")

    # Create settings.json with hooks configured directly
    settings: dict[str, Any] = {
        "permissions": permissions,
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": ".claude/scripts/activate-venv.sh",
                            "timeout": 5,
                        },
                        {
                            "type": "command",
                            "command": ".claude/scripts/session-start.sh",
                            "timeout": 10,
                        },
                    ],
                },
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {abs_hooks}/langfuse_hook.py",
                            "timeout": 30,
                        },
                    ],
                },
            ],
            "SessionEnd": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {abs_hooks}/langfuse_hook.py",
                            "timeout": 30,
                        },
                    ],
                },
            ],
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {abs_hooks}/langfuse_session_init_hook.py",
                            "timeout": 10,
                        },
                    ],
                },
            ],
            "PostToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": ".claude/scripts/validate-on-save.sh",
                            "timeout": 15,
                        },
                    ],
                },
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": ".claude/scripts/check-prism-run.sh",
                            "timeout": 15,
                        },
                        {
                            "type": "command",
                            "command": f"python3 {abs_hooks}/langfuse_git_commit_hook.py",
                            "timeout": 15,
                        },
                    ],
                },
            ],
        },
    }

    settings_file = claude_dir / "settings.json"
    settings_file.write_text(json.dumps(settings, indent=2) + "\n")

    # Create settings.local.json with telemetry environment variables
    settings_local: dict[str, Any] = {
        "env": {
            "TRACE_TO_LANGFUSE": "true",
            "LANGFUSE_PUBLIC_KEY": (
                "ced0ca0cf048a05ac1f272cf1e70693233f6932722738eadd6a56fa361f213cf"
            ),
            "LANGFUSE_SECRET_KEY": "relay",
            "LANGFUSE_HOST": "https://prism-telemetry.lightconeresearch.workers.dev",
        },
    }
    settings_local_file = claude_dir / "settings.local.json"
    settings_local_file.write_text(json.dumps(settings_local, indent=2) + "\n")


def _init_git_repo(directory: Path, no_git: bool) -> None:
    """Initialize git repository if requested."""
    if no_git or (directory / ".git").exists():
        return

    try:
        subprocess.run(
            ["git", "init"],
            cwd=directory,
            capture_output=True,
            check=True,
        )
        console.print("[green]✓[/green] Initialized git repository")
        try:
            subprocess.run(["git", "add", "."], cwd=directory, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial ASTRA analysis structure"],
                cwd=directory,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            pass
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _get_lightcone_venv_site_packages() -> Path | None:
    """Read ~/.lightcone/.config to find the Lightcone venv's site-packages."""
    config_file = Path.home() / ".lightcone" / ".config"
    if not config_file.exists():
        return None

    # Parse the simple KEY="VALUE" config file
    venv_path_str = None
    for line in config_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("VENV_PATH="):
            venv_path_str = line.split("=", 1)[1].strip().strip('"')
            break

    if not venv_path_str:
        return None

    venv_path = Path(venv_path_str)
    if not venv_path.is_dir():
        return None

    # Find site-packages inside the Lightcone venv
    candidates = list(venv_path.glob("lib/python*/site-packages"))
    if not candidates:
        candidates = list(venv_path.glob("Lib/site-packages"))  # Windows
    return candidates[0] if candidates else None


def _create_venv(directory: Path, no_venv: bool) -> bool:
    """Create a virtual environment with astra and prism installed."""
    if no_venv:
        return False

    venv_path = directory / ".venv"

    try:
        subprocess.run(
            [sys.executable, "-m", "venv", "--system-site-packages", str(venv_path)],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning:[/yellow] Failed to create virtual environment: {e}")
        return False

    console.print("[green]✓[/green] Created virtual environment (.venv)")

    # --system-site-packages only exposes the *real* system Python's packages.
    # If Lightcone was installed into its own venv (the common case), we need
    # to bridge the new project venv to the Lightcone venv's site-packages
    # via a .pth file.
    lightcone_sp = _get_lightcone_venv_site_packages()
    if lightcone_sp:
        inner_sp_dirs = list(venv_path.glob("lib/python*/site-packages"))
        if not inner_sp_dirs and sys.platform == "win32":
            inner_sp_dirs = list(venv_path.glob("Lib/site-packages"))
        if inner_sp_dirs:
            pth_file = inner_sp_dirs[0] / "_lightcone.pth"
            pth_file.write_text(str(lightcone_sp) + "\n")

    # Check if prism is now importable in the new venv
    if sys.platform == "win32":
        python_path = venv_path / "Scripts" / "python"
    else:
        python_path = venv_path / "bin" / "python"

    try:
        subprocess.run(
            [str(python_path), "-c", "import prism"],
            capture_output=True,
            check=True,
        )
        console.print("[green]✓[/green] prism available (via Lightcone environment)")
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not available — install it
        pip_path = python_path.parent / "pip"
        lightcone_dir = Path.home() / ".lightcone"
        env = {**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": "0.1.0"}
        try:
            if (lightcone_dir / "ASTRA").is_dir() and (lightcone_dir / "Prism").is_dir():
                install_targets = ["-e", str(lightcone_dir / "ASTRA")]
                install_targets += ["-e", str(lightcone_dir / "Prism")]
                subprocess.run(
                    [str(pip_path), "install", *install_targets],
                    capture_output=True,
                    check=True,
                    env=env,
                )
            else:
                subprocess.run(
                    [str(pip_path), "install",
                     "git+https://github.com/LightconeResearch/Prism.git"],
                    capture_output=True,
                    check=True,
                    env=env,
                )
            console.print("[green]✓[/green] Installed prism in virtual environment")
        except subprocess.CalledProcessError:
            console.print(
                "[yellow]Warning:[/yellow] Could not install prism automatically. "
                "You can install manually with: .venv/bin/pip install prism"
            )

    return True


# =============================================================================
# Dagster execution commands
# =============================================================================


@main.command()
@click.argument("outputs", nargs=-1)
@click.option("--universe", "-u", default=None, help="Universe to materialize for")
@click.option("--target", "-t", default=None, help="Execution target name")
@click.option("--no-build", is_flag=True, help="Skip automatic container image builds")
def run(
    outputs: tuple[str, ...],
    universe: str | None,
    target: str | None,
    no_build: bool,
) -> None:
    """Materialize ASTRA outputs via Dagster.

    Runs recipes to produce outputs. Without arguments, materializes all
    outputs for all universes. Container build specs are automatically
    built before execution unless --no-build is given.

    Examples:
        prism run                           # all outputs, all universes
        prism run accuracy                  # specific output
        prism run --universe baseline       # specific universe
        prism run accuracy -u baseline      # specific output + universe
        prism run --target perlmutter-gpu   # run on SLURM
        prism run --no-build                # skip container builds
    """
    from prism.dagster.assets import build_definitions
    from prism.dagster.targets import load_target

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    # Resolve target: --target flag > prism.yaml > default from user config
    target_name = target
    if not target_name:
        prism_yaml = project_path / "prism.yaml"
        if prism_yaml.exists():
            with open(prism_yaml) as f:
                prism_data = yaml.safe_load(f) or {}
            target_name = prism_data.get("target")
        if not target_name:
            from prism.dagster.targets import load_user_config
            target_name = load_user_config().get("default_target")

    # Load target config directly — no merging
    target_config = None
    if target_name and target_name != "local":
        target_config = load_target(target_name)

    universe_id = universe or "baseline"
    defs = build_definitions(
        project_path, target_config=target_config, universe_id=universe_id,
        no_build=no_build,
    )

    console.print("[bold]Materializing outputs...[/bold]")

    import dagster as dg

    # Select assets to materialize (exclude external/input-only assets)
    all_assets = list(defs.get_all_asset_specs())
    if outputs:
        selection = list(outputs)
    else:
        selection = [
            spec.key.path[-1] for spec in all_assets
            if not (spec.metadata or {}).get('external', False)
        ]

    # Use a persistent DagsterInstance so run history is recorded for the
    # Dagster webserver (prism dev) to display.
    dagster_yaml = project_path / "dagster.yaml"
    if dagster_yaml.exists():
        instance = dg.DagsterInstance.from_config(str(project_path))
    else:
        instance = None

    # Execute
    try:
        result = dg.materialize(
            assets=list(defs.assets),
            selection=selection,
            instance=instance,
        )
        if result.success:
            console.print("[green]✓[/green] Materialization complete")
        else:
            console.print("[red]✗[/red] Materialization failed")
            raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)


@main.command()
@click.option("--force", is_flag=True, help="Rebuild images even if they already exist")
@click.option(
    "--runtime", "-r",
    type=click.Choice(["docker", "podman-hpc"]),
    default=None,
    help="Container runtime to build with (auto-detected from target config)",
)
def build(force: bool, runtime: str | None) -> None:
    """Build container images from Containerfile specs in astra.yaml.

    Scans the analysis specification for container build specs (both
    analysis-level and per-recipe) and builds any missing images.
    Images are content-addressed — rebuilds only happen when the
    Containerfile or dependency files change.

    The container runtime is auto-detected from the project's target
    config (prism.yaml → ~/.prism/targets/). Use --runtime to override.

    Examples:
        prism build                      # auto-detect runtime from target
        prism build --runtime podman-hpc # force podman-hpc
        prism build --runtime docker     # force docker
        prism build --force              # rebuild all images
    """
    from astra.helpers import get_outputs, load_yaml

    from prism.container import (
        ContainerBuildError,
        resolve_container_for_slurm,
        resolve_container_spec,
    )

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    # Resolve runtime from target config if not explicitly provided
    if runtime is None:
        from prism.dagster.targets import load_target, load_user_config
        target_name = None
        prism_yaml = project_path / "prism.yaml"
        if prism_yaml.exists():
            with open(prism_yaml) as f:
                prism_data = yaml.safe_load(f) or {}
            target_name = prism_data.get("target")
        if not target_name:
            target_name = load_user_config().get("default_target")
        if target_name and target_name != "local":
            target_config = load_target(target_name)
            if target_config:
                runtime = target_config.get("container_runtime", "docker")
        if runtime is None:
            runtime = "docker"

    spec = load_yaml(project_path / "astra.yaml")
    project_name = spec.get("name") or project_path.name

    # Collect all unique container build specs.
    build_specs: list[tuple[str, str | dict]] = []  # (label, spec)
    raw_default = spec.get("container")
    if raw_default is not None:
        if isinstance(raw_default, dict) and "build" in raw_default:
            build_specs.append(("analysis-level", raw_default))
        elif isinstance(raw_default, str) and runtime != "docker":
            # Pre-built images need pull/migrate for HPC runtimes
            build_specs.append(("analysis-level", raw_default))

    for output_def in get_outputs(spec):
        recipe = output_def.get("recipe")
        if not recipe:
            continue
        raw = recipe.get("container")
        if raw is not None:
            if isinstance(raw, dict) and "build" in raw:
                label = f"recipe:{output_def.get('id', '?')}"
                build_specs.append((label, raw))
            elif isinstance(raw, str) and runtime != "docker":
                label = f"recipe:{output_def.get('id', '?')}"
                build_specs.append((label, raw))

    if not build_specs:
        console.print("[dim]No container build specs found in astra.yaml.[/dim]")
        return

    console.print(
        f"[bold]Found {len(build_specs)} container spec(s) "
        f"(runtime: {runtime})[/bold]\n"
    )

    for label, bspec in build_specs:
        try:
            if runtime == "podman-hpc":
                tag = resolve_container_for_slurm(
                    bspec, project_path, project_name, runtime, force=force,
                )
            else:
                tag = resolve_container_spec(
                    bspec, project_path, project_name, force=force,
                )
            console.print(f"  [green]ready[/green]  {label} -> {tag}")
        except ContainerBuildError as e:
            console.print(f"  [red]fail[/red]   {label}: {e}")


@main.command()
@click.option("--universe", "-u", default=None, help="Show status for specific universe")
def status(universe: str | None) -> None:
    """Show materialization status of all outputs.

    Displays a table of outputs vs universes with materialization state.

    Examples:
        prism status
        prism status --universe baseline
    """
    from astra.helpers import get_outputs, load_yaml

    from prism.dagster.status import get_all_universe_status, get_output_status

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    spec = load_yaml(project_path / "astra.yaml")
    name = spec.get("name", "Unknown")
    outputs = get_outputs(spec)

    if universe:
        all_status = {universe: get_output_status(project_path, universe)}
    else:
        all_status = get_all_universe_status(project_path)

    if not all_status:
        console.print("[yellow]No universes found.[/yellow]")
        return

    from rich.table import Table

    table = Table(title=f"{name} — Output Status")
    table.add_column("Output", style="cyan")
    for uid in all_status:
        table.add_column(uid)

    recipe_count = 0
    total_outputs = len(outputs)
    materialized = 0
    total_cells = 0
    for out in outputs:
        out_id = out.get("id")
        if not out_id:
            continue
        has_recipe = bool(out.get("recipe"))
        if has_recipe:
            recipe_count += 1
        row = [out_id]
        for uid, universe_status in all_status.items():
            s = universe_status.get(out_id, "no_recipe")
            if has_recipe:
                total_cells += 1
            if s == "materialized":
                materialized += 1
                row.append("[green]ok[/green]")
            elif s == "pending":
                row.append("[dim]pending[/dim]")
            else:
                row.append("[yellow]no recipe[/yellow]")
        table.add_row(*row)

    console.print(table)
    console.print(f"\n  Recipes: {recipe_count}/{total_outputs} outputs integrated")
    console.print(f"  Materialized: {materialized}/{total_cells} runs")

    # Show container status
    from prism.container import get_container_status

    raw_container = spec.get("container")
    cstatus = get_container_status(raw_container, project_path, name)
    if cstatus.type == "prebuilt":
        console.print(f"  Container: prebuilt [cyan]{cstatus.image}[/cyan]")
    elif cstatus.type == "build":
        if cstatus.extra.get("error"):
            console.print(
                f"  Container: build [red]{cstatus.extra['error']}[/red]"
            )
        elif cstatus.exists:
            console.print(
                f"  Container: build {cstatus.containerfile} "
                f"[green]{cstatus.image} (built)[/green]"
            )
        else:
            console.print(
                f"  Container: build {cstatus.containerfile} "
                f"[yellow]{cstatus.image} (not built)[/yellow]"
            )


@main.command()
@click.option("--port", "-p", default=3000, type=int, help="Port for Dagster webserver")
@click.option("--universe", "-u", default="baseline", help="Universe to load definitions for")
def dev(port: int, universe: str) -> None:
    """Launch Dagster webserver UI for the current project.

    Opens a web UI showing the asset graph, run history, and
    materialization status.

    Examples:
        prism dev
        prism dev --port 8080
        prism dev --universe experiment1
    """
    import tempfile

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    console.print(f"[bold]Starting Dagster webserver on port {port}...[/bold]")
    console.print(f"  Open [cyan]http://localhost:{port}[/cyan] in your browser")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    # Generate a temporary Python file that builds Dagster Definitions from
    # the current ASTRA project.  dagster-webserver discovers assets via -f.
    defs_code = (
        "from pathlib import Path\n"
        "from prism.dagster.assets import build_definitions\n"
        f"defs = build_definitions(Path({str(project_path)!r}), "
        f"universe_id={universe!r}, no_build=True)\n"
    )

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="prism_defs_", delete=False,
        ) as f:
            f.write(defs_code)
            defs_file = f.name

        env = {**os.environ, "DAGSTER_HOME": str(project_path)}
        subprocess.run(
            ["dagster-webserver", "-f", defs_file, "-h", "0.0.0.0", "-p", str(port)],
            check=True,
            env=env,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Dagster webserver stopped[/dim]")
    except FileNotFoundError:
        console.print("[red]Error:[/red] dagster-webserver not found.")
        console.print("  Install with: [cyan]pip install prism\\[dagster][/cyan]")
        raise SystemExit(1)
    finally:
        # Clean up the temporary definitions file
        try:
            Path(defs_file).unlink(missing_ok=True)
        except NameError:
            pass


# =============================================================================
# Target command
# =============================================================================


@main.group(invoke_without_command=True)
@click.option("--set", "set_target", default=None, help="Set project target")
@click.option("--list", "list_flag", is_flag=True, help="List available targets")
@click.option("--show", "show_name", default=None, help="Show a target's config")
@click.pass_context
def target(
    ctx: click.Context,
    set_target: str | None,
    list_flag: bool,
    show_name: str | None,
) -> None:
    """Show or manage execution targets for this project."""
    if ctx.invoked_subcommand is not None:
        return

    from prism.dagster.targets import list_targets, load_target

    if set_target:
        # Update target key in prism.yaml
        project_path = Path.cwd()
        prism_yaml = project_path / "prism.yaml"
        if not prism_yaml.exists():
            console.print("[red]Error:[/red] No prism.yaml found. Run 'prism init' first.")
            raise SystemExit(1)

        # Verify target exists (or is "local")
        if set_target != "local" and load_target(set_target) is None:
            console.print(f"[red]Error:[/red] No configured target '{set_target}'.")
            console.print(
                f"  Available: {', '.join(list_targets()) or 'none'}"
            )
            raise SystemExit(1)

        with open(prism_yaml) as f:
            data = yaml.safe_load(f) or {}
        data["target"] = set_target
        with open(prism_yaml, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        console.print(f"[green]✓[/green] Project target set to '{set_target}'")
        return

    if show_name:
        config = load_target(show_name)
        if config is None:
            console.print(f"[red]Error:[/red] No configured target '{show_name}'.")
            raise SystemExit(1)
        console.print(f"[bold]Target: {show_name}[/bold]\n")
        console.print(yaml.dump(config, default_flow_style=False, sort_keys=False))
        return

    if list_flag:
        from prism.dagster.targets import load_user_config
        saved = list_targets()
        user_config = load_user_config()
        default = user_config.get("default_target", "")

        console.print("[bold]Available targets:[/bold]")
        local_marker = " [green](default)[/green]" if default == "local" else ""
        console.print(f"  - local  (built-in){local_marker}")
        if saved:
            for t in saved:
                marker = " [green](default)[/green]" if t == default else ""
                console.print(f"  - {t}{marker}")
        else:
            console.print("  [dim](no additional targets configured)[/dim]")
        console.print(
            "\nRun [cyan]prism target add[/cyan] to create a new target."
        )
        return

    # Default: show current project target
    project_path = Path.cwd()
    prism_yaml = project_path / "prism.yaml"
    if not prism_yaml.exists():
        console.print("No prism.yaml found. Run [cyan]prism init[/cyan] first.")
        return

    with open(prism_yaml) as f:
        data = yaml.safe_load(f) or {}
    current = data.get("target", "not set")
    console.print(f"  Current target: [cyan]{current}[/cyan]")

    # Check if it's configured
    if current != "local" and current != "not set":
        config = load_target(current)
        if config:
            console.print(f"  Backend: {config.get('backend', 'unknown')}")
            conn = config.get("connection", {})
            if conn.get("hostname"):
                console.print(f"  Host: {conn['hostname']}")
        else:
            console.print("  [yellow]Warning: target not found in ~/.prism/targets/[/yellow]")
    console.print("\n  Use [cyan]prism target --set <name>[/cyan] to change.")


@target.command("add")
@click.argument("name", required=False)
def target_add(name: str | None) -> None:
    """Create a new execution target."""
    from prism.dagster.site_registry import get_site_defaults, list_known_sites
    from prism.dagster.targets import save_target

    console.print("\n[bold]Create New Target[/bold]\n")

    # --- Site selection ---
    known = list_known_sites()
    hpc_sites = [(k, d) for k, d in known if k != "local"]

    console.print("  [bold]Site type:[/bold]")
    console.print("    1. Local (Docker)")
    for i, (_key, display) in enumerate(hpc_sites, 2):
        console.print(f"    {i}. {display}")

    choices = [str(i) for i in range(1, len(hpc_sites) + 2)]
    choice = click.prompt(
        "\n  Select site type",
        type=click.Choice(choices),
        default="1",
    )

    if choice == "1":
        # Local target
        target_name = name or "local"
        config: dict[str, Any] = {
            "site": "local",
            "backend": "local",
            "connection": {},
        }
    else:
        site_key = hpc_sites[int(choice) - 2][0]
        site = get_site_defaults(site_key) or {}
        hostname = site.get("connection", {}).get("hostname", "")

        # --- Connection ---
        username = click.prompt(
            "  Username",
            default=os.environ.get("USER", ""),
        )
        account = click.prompt("  Account/allocation")

        # --- Container runtime ---
        site_runtimes = site.get("container_runtimes", [])
        if len(site_runtimes) > 1:
            console.print("\n  [bold]Container runtime:[/bold]")
            for i, rt in enumerate(site_runtimes, 1):
                console.print(f"    {i}. {rt}")
            rt_choices = [str(i) for i in range(1, len(site_runtimes) + 1)]
            rt_idx = click.prompt(
                "  Select runtime",
                type=click.Choice(rt_choices),
                default="1",
            )
            container_runtime = site_runtimes[int(rt_idx) - 1]
        elif site_runtimes:
            container_runtime = site_runtimes[0]
        else:
            container_runtime = site.get(
                "scheduler", {},
            ).get("container_runtime", "docker")

        # --- Node type selection ---
        node_types = site.get("node_types", {})
        if node_types:
            console.print("\n  [bold]Node type:[/bold]")
            nt_list = list(node_types.items())
            for i, (nt_key, nt_info) in enumerate(nt_list, 1):
                console.print(f"    {i}. {nt_key} — {nt_info.get('description', '')}")
            nt_choices = [str(i) for i in range(1, len(nt_list) + 1)]
            nt_idx = click.prompt(
                "  Select node type",
                type=click.Choice(nt_choices),
                default="1",
            )
            nt_key, nt_info = nt_list[int(nt_idx) - 1]
            constraint = nt_info.get("constraint", nt_key)
        else:
            nt_key = "default"
            constraint = ""

        target_name = name or f"{site_key}-{nt_key}"

        # QOS selection
        qos_options = site.get("qos_options", {})
        qos = "regular"
        if qos_options:
            console.print("\n  [bold]QOS:[/bold]")
            qos_list = list(qos_options.items())
            for i, (qos_key, qos_info) in enumerate(qos_list, 1):
                console.print(f"    {i}. {qos_key} — {qos_info.get('description', '')}")
            qos_choices = [str(i) for i in range(1, len(qos_list) + 1)]
            qos_idx = click.prompt(
                "  Select QOS",
                type=click.Choice(qos_choices),
                default="1",
            )
            qos = qos_list[int(qos_idx) - 1][0]

        resource_limits = site.get("resource_limits", {})
        config = {
            "site": site_key,
            "backend": site.get("backend", "slurm"),
            "connection": {
                "hostname": hostname,
                "username": username,
            },
            "account": account,
            "container_runtime": container_runtime,
            "constraint": constraint,
            "qos": qos,
        }

        # --- Resource limits ---
        console.print("\n  [bold]Resource limits[/bold]")
        console.print("  (these cap what Claude can request per job)\n")

        config["max_nodes"] = click.prompt(
            "  Max nodes per job",
            type=int,
            default=resource_limits.get("max_nodes", 4),
        )
        config["max_walltime_minutes"] = click.prompt(
            "  Max walltime (minutes)",
            type=int,
            default=resource_limits.get("max_walltime_minutes", 360),
        )
        config["max_concurrent_jobs"] = click.prompt(
            "  Max concurrent jobs",
            type=int,
            default=resource_limits.get("max_concurrent_jobs", 8),
        )

    path = save_target(target_name, config)
    console.print(f"\n  [green]✓[/green] Created target '{target_name}' at {path}")


@target.command("edit")
@click.argument("name")
def target_edit(name: str) -> None:
    """Edit an existing execution target."""
    from prism.dagster.targets import load_target, save_target

    config = load_target(name)
    if config is None:
        console.print(f"[red]Error:[/red] No configured target '{name}'.")
        raise SystemExit(1)

    console.print(f"\n[bold]Edit Target: {name}[/bold]")
    console.print("  Press Enter to keep current value.\n")

    # Edit each field
    backend = click.prompt("  Backend", default=config.get("backend", "local"))
    config["backend"] = backend

    conn = config.get("connection", {})
    if backend != "local":
        hostname = click.prompt("  Hostname", default=conn.get("hostname", ""))
        username = click.prompt("  Username", default=conn.get("username", ""))
        config["connection"] = {"hostname": hostname, "username": username}

        account = click.prompt("  Account", default=config.get("account", ""))
        if account:
            config["account"] = account

        runtime = click.prompt(
            "  Container runtime",
            default=config.get("container_runtime", "docker"),
        )
        config["container_runtime"] = runtime

        constraint = click.prompt(
            "  Constraint",
            default=config.get("constraint", ""),
        )
        if constraint:
            config["constraint"] = constraint

        qos = click.prompt("  QOS", default=config.get("qos", ""))
        if qos:
            config["qos"] = qos

        # --- Resource limits ---
        console.print("\n  [bold]Resource limits:[/bold]")
        config["max_nodes"] = click.prompt(
            "  Max nodes per job",
            type=int,
            default=config.get("max_nodes", 4),
        )
        config["max_walltime_minutes"] = click.prompt(
            "  Max walltime (minutes)",
            type=int,
            default=config.get("max_walltime_minutes", 360),
        )
        config["max_concurrent_jobs"] = click.prompt(
            "  Max concurrent jobs",
            type=int,
            default=config.get("max_concurrent_jobs", 8),
        )

    path = save_target(name, config)
    console.print(f"\n  [green]✓[/green] Updated target '{name}' at {path}")


# =============================================================================
# Setup command
# =============================================================================


def _run_setup_menu() -> None:
    """Show the setup management menu when config already exists."""
    from prism.dagster.targets import list_targets, load_user_config, save_user_config

    user_config = load_user_config()
    default = user_config.get("default_target", "local")
    tier = user_config.get("default_permission_tier", "recommended")
    targets = list_targets()
    target_names = ["local"] + [t for t in targets if t != "local"]

    console.print("\n[bold]Prism Setup[/bold]")
    console.print(f"  Default target:    {default}")
    console.print(f"  Permission level:  {tier}")
    console.print(f"  Targets:           {', '.join(target_names)}")

    console.print("\n  1. Change permission level")
    console.print("  2. Add a target")
    console.print("  3. Edit a target")
    console.print("  4. Change default target")
    console.print("  5. Re-run setup wizard")
    console.print("  6. Exit")

    choice = click.prompt(
        "\n  Select action",
        type=click.Choice(["1", "2", "3", "4", "5", "6"]),
        default="6",
    )

    if choice == "6":
        return
    elif choice == "1":
        _prompt_permission_tier()
    elif choice == "2":
        ctx = click.get_current_context()
        ctx.invoke(target_add)
    elif choice == "3":
        console.print("\n  [bold]Targets:[/bold]")
        for i, t in enumerate(target_names, 1):
            console.print(f"    {i}. {t}")
        idx = click.prompt(
            "  Select target to edit",
            type=click.Choice([str(i) for i in range(1, len(target_names) + 1)]),
            default="1",
        )
        chosen = target_names[int(idx) - 1]
        ctx = click.get_current_context()
        ctx.invoke(target_edit, name=chosen)
    elif choice == "4":
        console.print("\n  [bold]Targets:[/bold]")
        for i, t in enumerate(target_names, 1):
            console.print(f"    {i}. {t}")
        idx = click.prompt(
            "  Select new default",
            type=click.Choice([str(i) for i in range(1, len(target_names) + 1)]),
            default="1",
        )
        chosen = target_names[int(idx) - 1]
        user_config["default_target"] = chosen
        save_user_config(user_config)
        console.print(f"  [green]✓[/green] Default target: {chosen}")
    elif choice == "5":
        _run_setup_wizard()


def _run_setup_wizard() -> list[Path]:
    """Run the interactive setup wizard.

    Creates one target per node type for HPC sites, plus a local target.
    Returns the list of paths where target configs were saved.
    """
    from prism.dagster.site_registry import get_site_defaults, list_known_sites
    from prism.dagster.targets import load_user_config, save_target, save_user_config

    console.print("\n[bold]Prism Setup — Target Configuration[/bold]")
    console.print(
        "  These settings are stored in [cyan]~/.prism/targets/[/cyan] and "
        "referenced by projects via prism.yaml.\n"
    )

    saved_paths: list[Path] = []
    default_target = "local"

    # --- Configure HPC? ---
    configure_hpc = click.confirm(
        "  Configure a remote execution site (HPC)?",
        default=False,
    )

    if configure_hpc:
        # --- HPC site selection ---
        known = list_known_sites()
        hpc_sites = [(k, d) for k, d in known if k != "local"]
        console.print("\n  [bold]Known HPC sites:[/bold]")
        for i, (_key, display) in enumerate(hpc_sites, 1):
            console.print(f"    {i}. {display}")

        site_choices = [str(i) for i in range(1, len(hpc_sites) + 1)]
        site_idx = click.prompt(
            "\n  Select site",
            type=click.Choice(site_choices),
            default="1",
        )
        site_key = hpc_sites[int(site_idx) - 1][0]
        site = get_site_defaults(site_key) or {}

        display = site.get("display_name", site_key)
        hostname = site.get("connection", {}).get("hostname", "")
        console.print(
            f"  Detected: [cyan]{display}[/cyan] ({hostname})\n"
        )

        # --- Connection ---
        username = click.prompt(
            "  Username",
            default=os.environ.get("USER", ""),
        )
        account = click.prompt("  Account/allocation")

        # --- Container runtime ---
        site_runtimes = site.get("container_runtimes", [])
        if len(site_runtimes) > 1:
            console.print("\n  [bold]Container runtime:[/bold]")
            for i, rt in enumerate(site_runtimes, 1):
                console.print(f"    {i}. {rt}")

            rt_choices = [
                str(i) for i in range(1, len(site_runtimes) + 1)
            ]
            rt_idx = click.prompt(
                "  Select runtime",
                type=click.Choice(rt_choices),
                default="1",
            )
            container_runtime = site_runtimes[int(rt_idx) - 1]
        elif site_runtimes:
            container_runtime = site_runtimes[0]
        else:
            container_runtime = site.get(
                "scheduler", {},
            ).get("container_runtime", "docker")

        # --- Node type selection ---
        node_types = site.get("node_types", {})
        resource_limits = site.get("resource_limits", {})
        nt_keys = list(node_types.keys())

        if len(nt_keys) > 1:
            console.print("\n  [bold]Node types:[/bold]")
            for i, nt_key in enumerate(nt_keys, 1):
                desc = node_types[nt_key].get("description", nt_key)
                console.print(f"    {i}. {nt_key} — {desc}")

            nt_choices = [str(i) for i in range(1, len(nt_keys) + 1)]
            nt_idx = click.prompt(
                "  Select node type",
                type=click.Choice(nt_choices),
                default="1",
            )
            selected_nt = nt_keys[int(nt_idx) - 1]
        elif nt_keys:
            selected_nt = nt_keys[0]
        else:
            selected_nt = "default"

        # --- QOS selection ---
        qos_options = site.get("qos_options", {})
        qos_keys = list(qos_options.keys())

        if len(qos_keys) > 1:
            # Find default QOS
            qos_default_idx = "1"
            for i, qk in enumerate(qos_keys, 1):
                if qos_options[qk].get("default"):
                    qos_default_idx = str(i)
                    break

            console.print("\n  [bold]QOS:[/bold]")
            for i, qk in enumerate(qos_keys, 1):
                desc = qos_options[qk].get("description", qk)
                console.print(f"    {i}. {qk} — {desc}")

            qos_choices = [str(i) for i in range(1, len(qos_keys) + 1)]
            qos_idx = click.prompt(
                "  Select QOS",
                type=click.Choice(qos_choices),
                default=qos_default_idx,
            )
            selected_qos = qos_keys[int(qos_idx) - 1]
        elif qos_keys:
            selected_qos = qos_keys[0]
        else:
            selected_qos = site.get("safe_defaults", {}).get("qos", "regular")

        # --- Target name ---
        nt_info = node_types.get(selected_nt, {})
        default_name = f"{site_key}-{selected_nt}"
        target_name = click.prompt("  Target name", default=default_name)

        target_config: dict[str, Any] = {
            "site": site_key,
            "backend": site.get("backend", "slurm"),
            "connection": {
                "hostname": hostname,
                "username": username,
            },
            "account": account,
            "container_runtime": container_runtime,
            "constraint": nt_info.get("constraint", selected_nt),
            "qos": selected_qos,
        }

        # --- Resource limits ---
        console.print("\n  [bold]Resource limits[/bold]")
        console.print("  (these cap what Claude can request per job)\n")

        max_nodes = click.prompt(
            "  Max nodes per job",
            type=int,
            default=resource_limits.get("max_nodes", 4),
        )
        target_config["max_nodes"] = max_nodes

        max_walltime = click.prompt(
            "  Max walltime (minutes)",
            type=int,
            default=resource_limits.get("max_walltime_minutes", 360),
        )
        target_config["max_walltime_minutes"] = max_walltime

        max_concurrent = click.prompt(
            "  Max concurrent jobs",
            type=int,
            default=resource_limits.get("max_concurrent_jobs", 8),
        )
        target_config["max_concurrent_jobs"] = max_concurrent


        path = save_target(target_name, target_config)
        saved_paths.append(path)
        console.print(f"  [green]✓[/green] Created target: {target_name}")

        default_target = target_name

    # --- Always create local target ---
    local_config: dict[str, Any] = {
        "site": "local",
        "backend": "local",
        "connection": {},
    }
    path = save_target("local", local_config)
    saved_paths.append(path)
    console.print("  [green]✓[/green] Created target: local")

    # --- Set default ---
    user_config = load_user_config()
    user_config["default_target"] = default_target
    save_user_config(user_config)
    console.print(f"\n  [green]✓[/green] Default target: {default_target}")

    console.print(
        "\n  To list configured targets:  [cyan]prism target --list[/cyan]"
        "\n  To add more targets:         [cyan]prism target add[/cyan]"
        "\n  To edit a target:            [cyan]prism target edit <name>[/cyan]"
    )

    return saved_paths


@main.command()
@click.option("--list", "list_flag", is_flag=True, help="List configured targets")
@click.option("--show", "show_name", default=None, help="Show a target's config")
@click.option("--default", "set_default", default=None, help="Set default target")
def setup(
    list_flag: bool,
    show_name: str | None, set_default: str | None,
) -> None:
    """Set up execution targets (first-time experience).

    Configures connection details and container runtime for remote
    execution backends (SLURM). Creates one target per node type.

    Settings are stored at the user level (~/.prism/targets/) and
    referenced by projects via prism.yaml.

    Examples:
        prism setup                        # interactive wizard
        prism setup --list                 # list configured targets
        prism setup --show perlmutter-gpu  # show a target's config
        prism setup --default local        # change default target
    """
    if set_default:
        from prism.dagster.targets import load_target, load_user_config, save_user_config
        # Validate target exists (or is "local")
        if set_default != "local":
            target_config = load_target(set_default)
            if target_config is None:
                console.print(f"[red]Error:[/red] No configured target '{set_default}'.")
                raise SystemExit(1)
        user_config = load_user_config()
        user_config["default_target"] = set_default
        save_user_config(user_config)
        console.print(f"[green]✓[/green] Default target set to '{set_default}'")
        return

    if show_name:
        from prism.dagster.targets import load_target
        config = load_target(show_name)
        if config is None:
            console.print(f"[red]Error:[/red] No configured target '{show_name}'.")
            raise SystemExit(1)
        console.print(f"[bold]Target: {show_name}[/bold]\n")
        console.print(yaml.dump(config, default_flow_style=False, sort_keys=False))
        return

    if list_flag:
        from prism.dagster.targets import list_targets, load_user_config
        saved = list_targets()
        user_config = load_user_config()
        default = user_config.get("default_target", "")

        console.print("[bold]Configured targets:[/bold]")
        # Always show local
        local_marker = " [green](default)[/green]" if default == "local" else ""
        console.print(f"  - local  (built-in){local_marker}")
        if saved:
            for t in saved:
                marker = " [green](default)[/green]" if t == default else ""
                console.print(f"  - {t}{marker}")
        else:
            console.print("  [dim](no additional targets configured)[/dim]")
        console.print(
            "\nRun [cyan]prism target add[/cyan] to create a new target."
        )
        return

    from prism.dagster.targets import get_config_path
    if get_config_path().exists():
        _run_setup_menu()
    else:
        _run_setup_wizard()


# =============================================================================
# Update command
# =============================================================================

# Marker that separates the Prism-managed portion of CLAUDE.md from user content.
_CLAUDE_MD_SEPARATOR = "## Analysis Context"


def _sync_project_plugins(project_dir: Path) -> bool:
    """Sync plugin files (skills, hooks, scripts, agents, CLAUDE.md) into a project.

    Returns True if the sync succeeded.
    """
    if not (project_dir / "astra.yaml").exists():
        console.print(f"  [red]✗[/red] {project_dir}: not an ASTRA project (no astra.yaml)")
        return False

    plugin_source = _get_plugin_source_dir()
    if plugin_source is None:
        console.print("  [red]✗[/red] Could not find Prism plugin source files.")
        return False

    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Sync directories: skills, hooks, scripts, agents
    for subdir in ("scripts", "hooks", "skills", "agents"):
        src = plugin_source / subdir
        dst = claude_dir / subdir
        if not src.exists():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        # Make executable as needed
        if subdir == "scripts":
            for f in dst.glob("*.sh"):
                f.chmod(f.stat().st_mode | 0o111)
        elif subdir == "hooks":
            for f in dst.glob("*.py"):
                f.chmod(f.stat().st_mode | 0o111)

    # Apply extraction model config to agents
    agents_dst = claude_dir / "agents"
    if agents_dst.exists():
        _update_extractor_agent_model(agents_dst)

    # Update the managed portion of CLAUDE.md (everything above "## Analysis Context")
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        existing = claude_md.read_text()
        # Find the separator
        sep_idx = existing.find(_CLAUDE_MD_SEPARATOR)
        if sep_idx != -1:
            user_section = existing[sep_idx:]
        else:
            # No separator found — preserve everything as user content
            user_section = (
                f"{_CLAUDE_MD_SEPARATOR}\n\n"
                "_Run `/prism-new` to scope the research question and populate "
                "this section with domain context and implementation notes not "
                "captured in astra.yaml._\n"
            )

        # Get fresh template
        name = project_dir.name
        template_path = plugin_source / "templates" / "CLAUDE.md"
        if template_path.exists():
            template = template_path.read_text().replace("{{name}}", name)
            template_sep_idx = template.find(_CLAUDE_MD_SEPARATOR)
            if template_sep_idx != -1:
                managed_section = template[:template_sep_idx]
            else:
                managed_section = template + "\n"
        else:
            managed_section = (
                f"# CLAUDE.md\n\n## Project: {name}\n\n"
                "ASTRA analysis project, built with Prism.\n\n---\n\n"
                "<!-- AUTOGENERATED: /prism-new populates below during specification -->\n"
            )

        claude_md.write_text(managed_section + user_section)

    console.print(f"  [green]✓[/green] {project_dir}")
    return True


def _prompt_sync_projects() -> None:
    """Prompt the user to sync plugin files into existing projects."""
    console.print(
        "\n[bold]Sync updated plugin files to your projects?[/bold]"
    )
    console.print(
        "  This updates skills, hooks, scripts, and CLAUDE.md in each project's .claude/ directory."
    )
    raw = click.prompt(
        "\n  Enter project paths (comma-separated), or skip",
        default="skip",
    )
    if raw.strip().lower() in ("skip", "s", ""):
        return

    paths = [Path(p.strip()).expanduser().resolve() for p in raw.split(",") if p.strip()]
    if not paths:
        return

    console.print()
    for p in paths:
        _sync_project_plugins(p)


@main.command()
@click.option("--check", is_flag=True, help="Only check for updates, don't install them")
def update(check: bool) -> None:
    """Update Lightcone packages to the latest version.

    Pulls the latest code from all Lightcone repositories and
    reinstalls Python packages.

    Examples:
        prism update          # pull & reinstall everything
        prism update --check  # just check what's available
    """
    from prism.updater import _get_lightcone_root, check_for_updates, pull_repos, reinstall_packages

    root = _get_lightcone_root()
    if root is None:
        console.print(
            "[red]Error:[/red] Could not find Lightcone install directory.\n"
            "  Expected repos as siblings of the Prism repo."
        )
        raise SystemExit(1)

    if check:
        console.print("[bold]Checking for updates...[/bold]\n")
        msg = check_for_updates(quiet_if_current=False)
        if msg:
            console.print(f"  {msg}")
        else:
            console.print("  [green]Everything is up to date.[/green]")
        return

    console.print(f"[bold]Updating from:[/bold] {root}\n")

    # Pull repos
    results = pull_repos(root)
    for name, success, message in results:
        icon = "[green]✓[/green]" if success else "[red]✗[/red]"
        console.print(f"  {icon} {name}: {message}")

    any_failed = any(not s for _, s, _ in results)
    any_updated = any(s and "updated" in m for _, s, m in results)

    # Reinstall packages only if something actually changed
    if any_updated:
        console.print("\n[bold]Reinstalling packages...[/bold]\n")
        pkg_results = reinstall_packages(root)
        for name, success, message in pkg_results:
            icon = "[green]✓[/green]" if success else "[red]✗[/red]"
            console.print(f"  {icon} {name}: {message}")
        if any(not s for _, s, _ in pkg_results):
            any_failed = True

    # Clear the update-check cache so the nag goes away
    from prism.updater import _CHECK_FILE
    if _CHECK_FILE.exists():
        _CHECK_FILE.unlink(missing_ok=True)

    if any_failed:
        console.print("\n[yellow]Some updates failed — see errors above.[/yellow]")
        raise SystemExit(1)
    else:
        console.print("\n[green bold]All packages updated successfully.[/green bold]")

    # Offer to sync plugin files into existing projects
    if any_updated:
        _prompt_sync_projects()


if __name__ == "__main__":
    main()
