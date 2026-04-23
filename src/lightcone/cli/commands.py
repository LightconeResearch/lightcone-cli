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
#:
#: Three tiers are available:
#:
#: * ``yolo`` — All tools allowed, including MCP servers.  No guardrails.
#:   Suitable for trusted, isolated development environments.
#: * ``recommended`` — Full read/write/bash access with deny rules for
#:   sensitive dotfiles, HPC scratch filesystems, and destructive commands
#:   (``sudo``, ``rm -rf /``, ``git push``).  Default for new projects.
#: * ``minimal`` — Read-only.  Every write or shell action requires explicit
#:   human confirmation.  Use when working on shared or production systems.
PERMISSION_TIERS: dict[str, dict[str, list[str]]] = {
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
            "Read",
            "Edit",
            "Write",
            "Bash(*)",
            "WebSearch",
            "WebFetch",
        ],
        "deny": [
            # Sensitive dotfiles — don't silently modify credentials/keys
            "Edit(~/.ssh/**)",
            "Edit(~/.aws/**)",
            "Edit(~/.gnupg/**)",
            # Common HPC scratch filesystems
            "Edit(//scratch/**)",
            "Edit(//pscratch/**)",
            # Dangerous bash — require explicit confirmation
            "Bash(sudo *)",
            "Bash(rm -rf /*)",
            "Bash(git push *)",
            "Bash(git push)",
        ],
    },
    "minimal": {
        "allow": ["Read"],
    },
}


@click.group()
@click.version_option(package_name="lightcone-cli")
@click.pass_context
def main(ctx: click.Context) -> None:
    """lightcone-cli — ASTRA-compliant Agentic Layer CLI."""
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand in ("setup", "target", "update", "eval"):
        return
    from lightcone.engine.targets import get_config_path
    if not get_config_path().exists():
        console.print(
            "\n[bold yellow]No execution environment configured.[/bold yellow]"
        )
        console.print(
            "  lightcone-cli needs a default target configured before you can use it.\n"
        )
        ctx.invoke(setup)
        return



# =============================================================================
# Path helpers
# =============================================================================


def _find_lightcone_yaml(project_path: Path) -> Path | None:
    """Find lightcone.yaml, checking .lightcone/ first then root for backwards compat."""
    candidate = project_path / ".lightcone" / "lightcone.yaml"
    if candidate.exists():
        return candidate
    candidate = project_path / "lightcone.yaml"
    if candidate.exists():
        return candidate
    return None


def _find_dagster_yaml(project_path: Path) -> Path | None:
    """Find dagster.yaml, checking .lightcone/ first then root for backwards compat."""
    candidate = project_path / ".lightcone" / "dagster.yaml"
    if candidate.exists():
        return candidate
    candidate = project_path / "dagster.yaml"
    if candidate.exists():
        return candidate
    return None


def _load_lightcone_config(project_path: Path) -> dict:
    """Load lightcone.yaml config, returning empty dict if not found."""
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
@click.option("--target", "-t", default=None, help="Execution target name")
@click.option(
    "--permissions",
    type=click.Choice(["yolo", "recommended", "minimal"]),
    default=None,
    help="Claude Code permission tier (default: prompt or saved default)",
)
@click.option(
    "--existing-project", "existing_project",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help=(
        "Path to existing code to migrate "
        "(copies into DIRECTORY, adds lightcone-cli infrastructure)"
    ),
)
@click.option(
    "--sub-analysis", "sub_analysis",
    is_flag=True,
    default=False,
    help="Create a sub-analysis directory and wire it into the parent project",
)
def init(
    directory: Path, no_git: bool, no_venv: bool,
    target: str | None, permissions: str | None,
    existing_project: Path | None,
    sub_analysis: bool,
) -> None:
    """Create a new ASTRA analysis project with full agentic scaffolding.

    Creates the project with ASTRA specification files, Claude Code plugin
    configuration, skills, hooks, and a Python virtual environment.

    Use --existing-project to migrate existing code into ASTRA. If the
    source path differs from DIRECTORY, code is copied in. Then run
    /lc-migrate in Claude Code to generate the spec.

    Use --sub-analysis to scaffold a sub-analysis directory and wire it
    into the parent project's astra.yaml and universe files.

    DIRECTORY is the project folder to create (default: current directory).

    Examples:
        lc init my-analysis
        lc init my-analysis --target perlmutter-gpu
        lc init . --existing-project .
        lc init my-analysis --existing-project ../old-code
        lc init analyses/new_stage --sub-analysis
        lc init --sub-analysis new_stage
    """
    if sub_analysis:
        _init_sub_analysis(directory)
        return

    if existing_project is not None:
        _init_existing_project(
            directory, source=existing_project,
            no_git=no_git, no_venv=no_venv,
            target=target, permissions=permissions,
        )
        return

    # Check if this is already an ASTRA project
    if (directory / "astra.yaml").exists():
        console.print(
            f"[red]Error:[/red] [cyan]{directory}[/cyan] is already an ASTRA project "
            f"(astra.yaml exists)."
        )
        console.print(
            "Use [cyan]astra validate[/cyan] to check it, or delete astra.yaml to re-init."
        )
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
        ".lightcone",
    ]
    for subdir in subdirs:
        (directory / subdir).mkdir(parents=True, exist_ok=True)

    # Create dagster.yaml inside .lightcone/
    _create_dagster_yaml(directory)

    # Create .gitignore
    _create_or_append_gitignore(directory)

    # Create boilerplate astra.yaml
    _create_boilerplate_astra_yaml(directory)

    # Create CLAUDE.md with project conventions
    _create_claude_md(directory)

    # Resolve target and permission tier, then create Claude Code settings
    effective_target = target
    if not effective_target:
        from lightcone.engine.targets import load_user_config
        effective_target = load_user_config().get("default_target", "local")

    tier = _resolve_permission_tier(permissions)
    _create_claude_settings(directory, tier, target=effective_target)

    # Write lightcone.yaml project config
    _create_lightcone_config(directory, effective_target)

    # If user explicitly passed --target, ensure it's been configured
    if target and target != "local":
        from lightcone.engine.targets import load_target
        if load_target(target) is None:
            console.print(
                f"\n[yellow]Target [cyan]{target}[/cyan] "
                "is not configured yet.[/yellow]"
            )
            console.print(
                "  Run [cyan]lc setup[/cyan] to configure execution targets."
            )

    # Create virtual environment
    _create_venv(directory, no_venv)

    # Initialize git repository
    _init_git_repo(directory, no_git)

    # Print success message
    console.print(f"[green]✓[/green] Created ASTRA analysis project: [cyan]{directory}[/cyan]")
    if target:
        console.print(f"  Target: [cyan]{target}[/cyan]")

    # Container runtime detection and guidance
    from lightcone.engine.container import detect_container_runtime
    rt = detect_container_runtime()
    if rt:
        console.print(f"  Container runtime: [cyan]{rt}[/cyan]")
    else:
        console.print(
            "\n[yellow]Note:[/yellow] No container runtime (Docker or Podman) detected.\n"
            "  Recipes will run in the project venv "
            "(dependencies from requirements.txt).\n"
            "  For full container isolation, install one of:\n"
            "    Podman: [cyan]https://podman.io/docs/installation[/cyan]\n"
            "  (recommended — rootless, no daemon)\n"
            "    Docker: [cyan]https://docs.docker.com/engine/install/[/cyan]"
        )

    console.print(
        "\n[bold yellow]Note:[/bold yellow] Telemetry is enabled by default. "
        "Claude Code sessions in this project will be traced to Langfuse.\n"
        "  To disable, set [cyan]TRACE_TO_LANGFUSE=false[/cyan] "
        "in [cyan].claude/settings.local.json[/cyan]."
    )

    console.print(f"\n[bold]cd {directory}[/bold] && [bold]claude[/bold]")
    console.print("Then run [cyan]/lc-new[/cyan] to scope your research question.")


_GITIGNORE_LINES = [
    "results/",
    "results/.dagster/",
    "__pycache__/",
    "*.py[cod]",
    ".venv/",
    ".ipynb_checkpoints/",
    ".DS_Store",
    ".langfuse/",
]


def _create_dagster_yaml(directory: Path) -> None:
    """Create .lightcone/dagster.yaml for Dagster instance configuration."""
    dagster_yaml_content = {
        "storage": {
            "sqlite": {
                "base_dir": "results/.dagster",
            },
        },
    }
    lightcone_dir = directory / ".lightcone"
    lightcone_dir.mkdir(parents=True, exist_ok=True)
    (lightcone_dir / "dagster.yaml").write_text(
        yaml.dump(dagster_yaml_content, default_flow_style=False, sort_keys=False)
    )


def _create_or_append_gitignore(directory: Path) -> None:
    """Create .gitignore or append missing lightcone-cli entries to an existing one."""
    gitignore_path = directory / ".gitignore"
    if gitignore_path.exists():
        existing = gitignore_path.read_text()
        existing_lines = {line.strip() for line in existing.splitlines()}
        missing = [line for line in _GITIGNORE_LINES if line not in existing_lines]
        if missing:
            addition = "\n# lightcone-cli / ASTRA\n" + "\n".join(missing) + "\n"
            with open(gitignore_path, "a") as f:
                f.write(addition)
    else:
        content = "# ASTRA Analysis\n" + "\n".join(_GITIGNORE_LINES) + "\n"
        gitignore_path.write_text(content)


def _init_existing_project(
    directory: Path,
    *,
    source: Path,
    no_git: bool,
    no_venv: bool,
    target: str | None,
    permissions: str | None,
) -> None:
    """Add lightcone-cli infrastructure to an existing project.

    If source != directory, copies source contents into directory first.
    Adds .lightcone/, .claude/, universes/, CLAUDE.md, and .gitignore entries
    without creating boilerplate astra.yaml or overwriting existing files.
    The user then runs /lc-migrate in Claude Code to generate the spec.
    """
    source = source.resolve()
    directory = directory if directory == Path(".") else directory

    # Copy source into directory if they differ
    if source.resolve() != directory.resolve():
        directory.mkdir(parents=True, exist_ok=True)
        # Copy all files from source, skipping hidden dirs and __pycache__
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

    # Check if this is already an ASTRA project
    if (directory / "astra.yaml").exists():
        console.print(
            f"[red]Error:[/red] [cyan]{directory}[/cyan] already has an astra.yaml."
        )
        console.print(
            "Use [cyan]astra validate[/cyan] to check it, "
            "or delete astra.yaml and re-run."
        )
        raise SystemExit(1)

    console.print(
        f"[bold]Adding lightcone-cli infrastructure to: [cyan]{directory}[/cyan][/bold]\n"
    )

    # Create directories that don't exist yet
    for subdir in ["universes", "results", ".lightcone"]:
        d = directory / subdir
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)

    # .lightcone/ internals
    _create_dagster_yaml(directory)

    # .gitignore — append if exists, create if not
    _create_or_append_gitignore(directory)

    # CLAUDE.md — only if it doesn't exist
    if not (directory / "CLAUDE.md").exists():
        _create_claude_md(directory)
    else:
        console.print("  [dim]CLAUDE.md already exists, skipping[/dim]")

    # Containerfile — only if it doesn't exist
    if not (directory / "Containerfile").exists():
        containerfile = """\
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
"""
        (directory / "Containerfile").write_text(containerfile)
    else:
        console.print("  [dim]Containerfile already exists, skipping[/dim]")

    # requirements.txt — don't touch if it exists
    if not (directory / "requirements.txt").exists():
        (directory / "requirements.txt").write_text("")
    else:
        console.print("  [dim]requirements.txt already exists, skipping[/dim]")

    # Claude Code settings
    tier = _resolve_permission_tier(permissions)
    _create_claude_settings(directory, tier)

    # lightcone.yaml
    effective_target = target
    if not effective_target:
        from lightcone.engine.targets import load_user_config
        effective_target = load_user_config().get("default_target", "local")
    _create_lightcone_config(directory, effective_target)

    if target and target != "local":
        from lightcone.engine.targets import load_target
        if load_target(target) is None:
            console.print(
                f"\n[yellow]Target [cyan]{target}[/cyan] "
                "is not configured yet.[/yellow]"
            )
            console.print(
                "  Run [cyan]lc setup[/cyan] to configure execution targets."
            )

    # Virtual environment
    _create_venv(directory, no_venv)

    # Git
    _init_git_repo(directory, no_git)

    # Success
    console.print(
        f"\n[green]✓[/green] Added lightcone-cli infrastructure to: [cyan]{directory}[/cyan]"
    )

    console.print(
        "\n[bold]Next steps:[/bold]"
    )
    if directory != Path("."):
        console.print(f"  [bold]cd {directory}[/bold]")
    console.print("  [bold]claude[/bold]")
    console.print("  [cyan]/lc-migrate[/cyan]")


def _create_boilerplate_astra_yaml(directory: Path) -> None:
    """Create boilerplate astra.yaml with TODOs."""

    name = directory.name if directory != Path(".") else "My Analysis"

    astra_yaml = f"""# ASTRA Analysis Specification
# Documentation: https://github.com/LightconeResearch/ASTRA

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


def _init_sub_analysis(directory: Path) -> None:
    """Scaffold a sub-analysis directory and wire it into the parent project."""
    from astra.helpers import load_yaml, save_yaml

    # Resolve the sub-analysis path.
    # If directory has no path separator (e.g. "new_stage"), default to analyses/<name>
    sub_path = directory
    if sub_path == Path("."):
        console.print("[red]Error:[/red] Please provide a name or path for the sub-analysis.")
        raise SystemExit(1)

    # If the user gave a bare name (no directory separators), put it under analyses/
    if len(sub_path.parts) == 1:
        sub_path = Path("analyses") / sub_path

    name = sub_path.name

    # Find the project root by looking for astra.yaml
    project_root = Path.cwd()
    if not (project_root / "astra.yaml").exists():
        console.print(
            "[red]Error:[/red] No astra.yaml found in current directory. "
            "Run this from the project root."
        )
        raise SystemExit(1)

    abs_sub_path = project_root / sub_path

    if abs_sub_path.exists() and (abs_sub_path / "astra.yaml").exists():
        console.print(
            f"[red]Error:[/red] Sub-analysis already exists at "
            f"[cyan]{sub_path}[/cyan] (astra.yaml found)."
        )
        raise SystemExit(1)

    # 1. Create the sub-analysis directory structure
    abs_sub_path.mkdir(parents=True, exist_ok=True)
    (abs_sub_path / "scripts").mkdir(exist_ok=True)
    (abs_sub_path / "scripts" / ".gitkeep").touch()
    (abs_sub_path / "universes").mkdir(exist_ok=True)
    (abs_sub_path / "results").mkdir(exist_ok=True)

    # Write the sub-analysis astra.yaml
    label = name.replace("_", " ").replace("-", " ").title()
    sub_spec = {
        "name": label,
        "description": "",
        "inputs": [],
        "outputs": [],
        "decisions": {},
    }
    save_yaml(sub_spec, abs_sub_path / "astra.yaml")

    # Write the sub-analysis baseline universe
    sub_universe = {
        "id": "baseline",
        "description": "Default configuration",
        "decisions": {},
    }
    save_yaml(sub_universe, abs_sub_path / "universes" / "baseline.yaml")

    # Write CLAUDE.md
    _create_claude_md(abs_sub_path)

    # 2. Wire into the parent astra.yaml
    root_spec = load_yaml(project_root / "astra.yaml")
    if "analyses" not in root_spec or root_spec["analyses"] is None:
        root_spec["analyses"] = {}
    root_spec["analyses"][name] = {"path": f"./{sub_path}"}
    save_yaml(root_spec, project_root / "astra.yaml")

    # 3. Wire into all root universe files
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
        f"[green]\u2713[/green] Created sub-analysis "
        f"[cyan]{name}[/cyan] at [cyan]{sub_path}[/cyan]"
    )
    console.print(f"  - {sub_path}/astra.yaml")
    console.print(f"  - {sub_path}/CLAUDE.md")
    console.print(f"  - {sub_path}/scripts/")
    console.print(f"  - {sub_path}/results/")
    console.print(f"  - {sub_path}/universes/baseline.yaml")
    console.print("  - Wired into root astra.yaml and universe files")


def _create_claude_md(directory: Path) -> None:
    """Create CLAUDE.md from the template in the plugin source."""
    name = directory.name if directory != Path(".") else "My Analysis"

    # Find the template
    plugin_source = get_plugin_source_dir()
    template_path = plugin_source / "templates" / "CLAUDE.md" if plugin_source else None

    if template_path and template_path.exists():
        content = template_path.read_text()
        content = content.replace("{{name}}", name)
    else:
        # Fallback: minimal CLAUDE.md if template not found
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


def _create_lightcone_config(directory: Path, target_name: str) -> None:
    """Create .lightcone/lightcone.yaml with target reference."""
    config = {
        "target": target_name,
    }
    lightcone_dir = directory / ".lightcone"
    lightcone_dir.mkdir(parents=True, exist_ok=True)
    (lightcone_dir / "lightcone.yaml").write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )
    console.print(f"[green]✓[/green] Created .lightcone/lightcone.yaml (target: {target_name})")



def _prompt_permission_tier() -> str:
    """Interactively prompt the user to choose a permission tier.

    Returns one of: 'yolo', 'recommended', 'minimal'.
    Saves the choice as the default for future projects.
    """
    console.print("\n[bold]Claude Code permission level[/bold]")
    console.print("  Controls what Claude can do without asking.\n")
    console.print("    1. yolo — Everything including MCP. No guardrails.")
    console.print("    2. recommended — Full access with guardrails (no sudo/push/scratch).")
    console.print("    3. minimal — Only file reading. Everything else prompts.")

    choice_map = {"1": "yolo", "2": "recommended", "3": "minimal"}
    raw = click.prompt(
        "\n  Select permission level",
        type=click.Choice(["1", "2", "3"]),
        default="2",
    )
    tier = choice_map.get(raw, "recommended")

    from lightcone.engine.targets import load_user_config, save_user_config
    global_config = load_user_config()
    global_config["default_permission_tier"] = tier
    save_user_config(global_config)
    console.print(f"  [green]✓[/green] Permissions: {tier}")

    return tier


def _prompt_extraction_model() -> str:
    """Interactively prompt the user to choose a model for literature extraction subagents.

    Returns a model name (e.g. 'haiku', 'sonnet') or empty string for inherit.
    Saves the choice to ~/.lightcone/config.yaml.
    """
    from lightcone.engine.targets import load_user_config, save_user_config

    console.print("\n[bold]Literature extraction model[/bold]")
    console.print("  Model used for paper-reading subagents during /lc-new.\n")
    console.print("    1. inherit — Use the same model as the main session (default)")
    console.print("    2. haiku  — Fast and cheap, good for straightforward extraction")
    console.print("    3. sonnet — Balanced cost and capability")

    choice_map = {"1": "", "2": "haiku", "3": "sonnet"}
    raw = click.prompt(
        "\n  Select extraction model",
        type=click.Choice(["1", "2", "3"]),
        default="1",
    )
    model = choice_map.get(raw, "")

    global_config = load_user_config()
    global_config["extraction_model"] = model
    save_user_config(global_config)

    display = model if model else "inherit"
    console.print(f"  [green]✓[/green] Extraction model: {display}")
    return model


def _resolve_permission_tier(flag_value: str | None) -> str:
    """Resolve which permission tier to use.

    Priority:
    1. --permissions flag (explicit override)
    2. Saved default in ~/.lightcone/config.yaml
    3. Interactive prompt (first time only)
    """
    # 1. Explicit flag
    if flag_value is not None:
        console.print(f"  Permissions: [cyan]{flag_value}[/cyan] (--permissions flag)")
        return flag_value

    # 2. Saved default
    from lightcone.engine.targets import load_user_config
    global_config = load_user_config()
    saved = global_config.get("default_permission_tier")
    if saved:
        console.print(f"  Permissions: [cyan]{saved}[/cyan] (saved default)")
        return saved

    # 3. Interactive prompt
    return _prompt_permission_tier()


def _update_extractor_agent_model(agents_dir: Path) -> None:
    """Update the lc-extractor agent definition with the configured extraction model.

    Reads extraction_model from ~/.lightcone/config.yaml and sets the model field
    in the agent's YAML frontmatter. If empty/missing, removes the model field
    so the agent inherits the parent model.
    """
    from lightcone.engine.targets import load_user_config

    extractor_path = agents_dir / "lc-extractor.md"
    if not extractor_path.exists():
        return

    user_config = load_user_config()
    extraction_model = user_config.get("extraction_model", "sonnet")

    content = extractor_path.read_text()

    # Insert or remove model field in frontmatter
    if extraction_model:
        # Add model field after description line
        if "model:" not in content:
            content = content.replace(
                "\ntools: Read, Bash",
                f"\nmodel: {extraction_model}\ntools: Read, Bash",
            )
        else:
            # Update existing model field
            import re
            content = re.sub(r"model: \w+", f"model: {extraction_model}", content)

    extractor_path.write_text(content)


def _create_claude_settings(
    directory: Path, tier: str = "recommended", target: str = "local",
) -> None:
    """Create Claude Code settings with lightcone-cli skills and agents.

    If a non-local target maps to a known HPC site, site-specific deny rules
    (e.g. scratch filesystem paths) are merged into the permissions.
    """
    claude_dir = directory / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Find the plugin source directory
    plugin_source = get_plugin_source_dir()
    if plugin_source is None:
        console.print(
            "[yellow]Warning:[/yellow] Could not find lightcone-cli plugin source files. "
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

    # Copy agents and apply extraction model config
    agents_src = plugin_source / "agents"
    agents_dst = claude_dir / "agents"
    if agents_src.exists():
        if agents_dst.exists():
            shutil.rmtree(agents_dst)
        shutil.copytree(agents_src, agents_dst)
        _update_extractor_agent_model(agents_dst)

    # Copy guides
    guides_src = plugin_source / "guides"
    guides_dst = claude_dir / "guides"
    if guides_src.exists():
        if guides_dst.exists():
            shutil.rmtree(guides_dst)
        shutil.copytree(guides_src, guides_dst)

    # Build permissions: start from tier, then merge site-specific deny rules
    permissions: dict[str, list[str]] = {
        k: list(v) for k, v in PERMISSION_TIERS[tier].items()
    }
    if target != "local" and "deny" in permissions:
        from lightcone.engine.site_registry import detect_site, get_site_scratch_deny_rules
        site_key = detect_site(target)
        if site_key:
            site_deny = get_site_scratch_deny_rules(site_key)
            existing = set(permissions["deny"])
            for rule in site_deny:
                if rule not in existing:
                    permissions["deny"].append(rule)

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
                            "command": ".claude/scripts/check-lc-run.sh",
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
            "LANGFUSE_HOST": "https://telemetry.lightconeresearch.workers.dev",
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


def _create_venv(directory: Path, no_venv: bool) -> bool:
    """Create a virtual environment with lightcone-cli installed from PyPI."""
    if no_venv:
        return False

    venv_path = directory / ".venv"

    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning:[/yellow] Failed to create virtual environment: {e}")
        return False

    console.print("[green]✓[/green] Created virtual environment (.venv)")

    pip_path = venv_path / ("Scripts" if sys.platform == "win32" else "bin") / "pip"
    try:
        subprocess.run(
            [str(pip_path), "install", "lightcone-cli"],
            capture_output=True,
            check=True,
        )
        console.print("[green]✓[/green] Installed lightcone-cli in virtual environment")
    except subprocess.CalledProcessError:
        console.print(
            "[yellow]Warning:[/yellow] Could not install lightcone-cli automatically. "
            "You can install manually with: .venv/bin/pip install lightcone-cli"
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
@click.option("--qos", default=None, help="qos option override (see `lc target`)")
@click.option("--constraint", default=None, help="constraint option override")
@click.option("--time-limit", default=None, help="walltime (e.g. 30m, 2h, 01:30:00)")
@click.option("--account", default=None, help="allocation account override")
@click.option("--partition", default=None, help="partition override")
@click.option("--strategy", default=None,
              type=click.Choice(["fit", "switch"]),
              help="adjustment when options exceed limits: 'fit' trims resources "
                   "to stay in the selected qos (default); 'switch' keeps resources "
                   "and picks another qos")
def run(
    outputs: tuple[str, ...],
    universe: str | None,
    target: str | None,
    no_build: bool,
    qos: str | None,
    constraint: str | None,
    time_limit: str | None,
    account: str | None,
    partition: str | None,
    strategy: str | None,
) -> None:
    """Materialize ASTRA outputs via Dagster.

    Runs recipes to produce outputs. Without arguments, materializes all
    outputs for all universes. Container build specs are automatically
    built before execution unless --no-build is given.

    Any unknown flags are passed through as extra SLURM scheduling directives.

    Examples:
        lc run                           # all outputs, all universes
        lc run accuracy                  # specific output
        lc run --universe baseline       # specific universe
        lc run accuracy -u baseline      # specific output + universe
        lc run --target perlmutter       # run on SLURM
        lc run --qos regular             # override default qos
        lc run --qos debug --time-limit 30m   # quick test
        lc run --no-build                # skip container builds
    """
    from lightcone.engine.assets import build_definitions
    from lightcone.engine.targets import load_target

    output_names = list(outputs)

    project_path = Path.cwd()
    if not (project_path / "astra.yaml").exists():
        console.print("[red]Error:[/red] No astra.yaml found in current directory.")
        raise SystemExit(1)

    target_name = target
    if not target_name:
        lightcone_data = _load_lightcone_config(project_path)
        target_name = lightcone_data.get("target")
        if not target_name:
            from lightcone.engine.targets import load_user_config
            target_name = load_user_config().get("default_target")

    target_config = None
    if target_name and target_name != "local":
        target_config = load_target(target_name)

    # Build CLI overrides from named flags
    cli_overrides: dict[str, Any] = {}
    if qos:
        cli_overrides["qos"] = qos
    if constraint:
        cli_overrides["constraint"] = constraint
    if time_limit:
        cli_overrides["time_limit"] = time_limit
    if account:
        cli_overrides["account"] = account
    if partition:
        cli_overrides["partition"] = partition
    if strategy:
        cli_overrides["strategy"] = strategy

    universe_id = universe or "baseline"
    defs = build_definitions(
        project_path, target_config=target_config, universe_id=universe_id,
        no_build=no_build, cli_overrides=cli_overrides or None,
        target_name=target_name,
    )

    console.print("[bold]Materializing outputs...[/bold]")

    import dagster as dg

    # Select assets to materialize (exclude external/input-only assets)
    all_assets = list(defs.resolve_all_asset_specs())
    if output_names:
        # Support dot-notation: hod_fitting.galaxy_mesh -> [universe, hod_fitting, galaxy_mesh]
        selection = [dg.AssetKey([universe_id] + o.split(".")) for o in output_names]
    else:
        selection = [
            spec.key for spec in all_assets
            if not (spec.metadata or {}).get('external', False)
        ]

    # Ensure dagster.yaml exists so materialization events are persisted.
    # Without this, events are lost and lc status can't detect them.
    dagster_yaml_path = _find_dagster_yaml(project_path)
    if dagster_yaml_path is None:
        lightcone_dir = project_path / ".lightcone"
        lightcone_dir.mkdir(parents=True, exist_ok=True)
        dagster_yaml_path = lightcone_dir / "dagster.yaml"
        dagster_yaml_content = {
            "storage": {"sqlite": {"base_dir": "results/.dagster"}},
        }
        dagster_yaml_path.write_text(
            yaml.dump(dagster_yaml_content, default_flow_style=False, sort_keys=False)
        )
    instance = dg.DagsterInstance.from_config(str(dagster_yaml_path.parent))

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
    type=click.Choice(["docker", "podman", "podman-hpc"]),
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
    config (.lightcone/lightcone.yaml → ~/.lightcone/targets/). Use --runtime to override.

    Examples:
        lc build                      # auto-detect runtime from target
        lc build --runtime podman-hpc # force podman-hpc
        lc build --runtime docker     # force docker
        lc build --force              # rebuild all images
    """
    from astra.helpers import get_outputs, load_yaml, resolve_analysis_tree

    from lightcone.engine.container import (
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
        from lightcone.engine.targets import load_target, load_user_config
        lightcone_data = _load_lightcone_config(project_path)
        target_name = lightcone_data.get("target")
        if not target_name:
            target_name = load_user_config().get("default_target")
        if target_name and target_name != "local":
            target_config = load_target(target_name)
            if target_config:
                runtime = target_config.get("container_runtime", "docker")
        if runtime is None:
            from lightcone.engine.container import detect_container_runtime
            runtime = detect_container_runtime()
            if runtime is None:
                console.print(
                    "[red]Error:[/red] No container runtime found (Docker or Podman).\n"
                    "  Install Docker or Podman to build container images."
                )
                raise SystemExit(1)

    spec = load_yaml(project_path / "astra.yaml")
    spec = resolve_analysis_tree(spec, project_path)
    project_name = spec.get("name") or project_path.name

    # Collect all unique container specs that need building or migrating.
    from lightcone.engine.container import is_containerfile

    build_specs: list[tuple[str, str]] = []  # (label, spec)
    raw_default = spec.get("container")
    if raw_default is not None:
        if is_containerfile(raw_default, project_path):
            build_specs.append(("analysis-level", raw_default))
        elif runtime != "docker":
            # Pre-built images need pull/migrate for HPC runtimes
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
                    bspec, project_path, project_name, force=force, runtime=runtime,
                )
            console.print(f"  [green]ready[/green]  {label} -> {tag}")
        except ContainerBuildError as e:
            console.print(f"  [red]fail[/red]   {label}: {e}")


def _status_label(s: str) -> str:
    """Format a status string for rich display."""
    if s == "materialized":
        return "[green]ok[/green]"
    elif s == "pending":
        return "[dim]pending[/dim]"
    elif s == "alias":
        return "[cyan]alias[/cyan]"
    return "[yellow]no recipe[/yellow]"


def _display_tree_status(
    name: str,
    groups: dict,
    all_status: dict[str, dict[str, str]],
) -> None:
    """Display status grouped by sub-analysis as a tree."""
    from rich.tree import Tree

    for uid, universe_status in all_status.items():
        tree = Tree(f"[bold]{name}[/bold]  universe: {uid}")

        for analysis_id, outputs in groups.items():
            if analysis_id is None:
                # Root-level outputs
                for out_id, out_def in outputs:
                    s = universe_status.get(out_id, "no_recipe")
                    tree.add(f"{out_id:40s} {_status_label(s)}")
            else:
                branch = tree.add(f"[bold cyan]{analysis_id}/[/bold cyan]")
                for out_id, out_def in outputs:
                    qualified = f"{analysis_id}/{out_id}"
                    s = universe_status.get(qualified, "no_recipe")
                    branch.add(f"{out_id:40s} {_status_label(s)}")

        console.print(tree)


def _display_flat_status(
    name: str,
    outputs: list[tuple[str, dict]],
    all_status: dict[str, dict[str, str]],
) -> None:
    """Display status as a flat table (original behavior)."""
    from rich.table import Table

    table = Table(title=f"{name} -- Output Status")
    table.add_column("Output", style="cyan")
    for uid in all_status:
        table.add_column(uid)

    for out_id, out_def in outputs:
        if not out_id:
            continue
        row = [out_id]
        for uid, universe_status in all_status.items():
            s = universe_status.get(out_id, "no_recipe")
            row.append(_status_label(s))
        table.add_row(*row)

    console.print(table)


@main.command()
@click.option("--universe", "-u", default=None, help="Show status for specific universe")
def status(universe: str | None) -> None:
    """Show materialization status of all outputs.

    Displays a table of outputs vs universes with materialization state.

    Examples:
        lc status
        lc status --universe baseline
    """
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

    # Collect all qualified output IDs grouped by sub-analysis
    from lightcone.engine.tree import collect_tree_outputs

    tree_outputs = collect_tree_outputs(spec)

    # Group outputs by analysis_id (None for root)
    from collections import OrderedDict

    groups: OrderedDict[str | None, list[tuple[str, dict]]] = OrderedDict()
    for tree_out in tree_outputs:
        gid = tree_out.analysis_id
        if gid not in groups:
            groups[gid] = []
        groups[gid].append((tree_out.output_id, tree_out.output_def))

    # Display as tree when sub-analyses exist
    has_sub = any(k is not None for k in groups)

    if has_sub:
        _display_tree_status(name, groups, all_status)
    else:
        _display_flat_status(name, groups.get(None, []), all_status)

    # Count totals across all groups
    recipe_count = 0
    total_outputs = 0
    materialized_count = 0
    total_cells = 0
    for tree_out in tree_outputs:
        out_id = tree_out.output_id
        if not out_id:
            continue
        total_outputs += 1
        has_recipe = bool(tree_out.output_def.get("recipe"))
        if has_recipe:
            recipe_count += 1
        if tree_out.analysis_id:
            qualified = f"{tree_out.analysis_id}/{out_id}"
        else:
            qualified = out_id
        for uid, universe_status in all_status.items():
            if has_recipe:
                total_cells += 1
            if universe_status.get(qualified) == "materialized":
                materialized_count += 1

    console.print(f"\n  Recipes: {recipe_count}/{total_outputs} outputs integrated")
    console.print(f"  Materialized: {materialized_count}/{total_cells} runs")

    # Show container status
    from lightcone.engine.container import detect_container_runtime, get_container_status

    raw_container = spec.get("container")
    rt = detect_container_runtime() or "docker"
    cstatus = get_container_status(raw_container, project_path, name, runtime=rt)
    if cstatus.type == "prebuilt":
        console.print(f"  Container: prebuilt [cyan]{cstatus.image}[/cyan]")
    elif cstatus.type == "build":
        if cstatus.exists:
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
        lc dev
        lc dev --port 8080
        lc dev --universe experiment1
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
        "from lightcone.engine.assets import build_definitions\n"
        f"defs = build_definitions(Path({str(project_path)!r}), "
        f"universe_id={universe!r}, no_build=True)\n"
    )

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
            check=True,
            env=env,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Dagster webserver stopped[/dim]")
    except FileNotFoundError:
        console.print("[red]Error:[/red] dagster-webserver not found.")
        console.print("  Install with: [cyan]pip install lightcone-cli[/cyan]")
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
@click.option("--show", "show_name", default=None, help="Show a target's run options")
@click.pass_context
def target(
    ctx: click.Context,
    set_target: str | None,
    list_flag: bool,
    show_name: str | None,
) -> None:
    """Show or manage the execution target for this project.

    The target is the named execution environment (e.g. ``local`` or a
    configured HPC site) that ``lc run`` uses.  Running ``lc target``
    with no flag prints the current target and, if configured, the
    available run options you can override via ``lc run --<option>``.
    """
    if ctx.invoked_subcommand is not None:
        return

    from lightcone.engine.targets import list_targets, load_target

    if set_target:
        project_path = Path.cwd()
        lightcone_yaml = _find_lightcone_yaml(project_path)
        if lightcone_yaml is None:
            console.print(
                "[red]Error:[/red] No lightcone.yaml found. Run 'lc init' first."
            )
            raise SystemExit(1)
        if set_target != "local" and load_target(set_target) is None:
            console.print(f"[red]Error:[/red] No configured target '{set_target}'.")
            console.print(
                f"  Available: {', '.join(list_targets()) or 'none'}"
            )
            raise SystemExit(1)
        with open(lightcone_yaml) as f:
            data = yaml.safe_load(f) or {}
        data["target"] = set_target
        with open(lightcone_yaml, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        console.print(f"[green]✓[/green] Project target set to '{set_target}'")
        return

    if show_name:
        config = load_target(show_name)
        if config is None:
            console.print(f"[red]Error:[/red] No configured target '{show_name}'.")
            raise SystemExit(1)
        _display_target(show_name, config)
        return

    if list_flag:
        from lightcone.engine.targets import load_user_config
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
        console.print("\nRun [cyan]lc target add[/cyan] to create a new target.")
        return

    # Default view: current project target + available run options.
    project_path = Path.cwd()
    lightcone_yaml = _find_lightcone_yaml(project_path)
    if lightcone_yaml is None:
        console.print("No lightcone.yaml found. Run [cyan]lc init[/cyan] first.")
        return
    with open(lightcone_yaml) as f:
        data = yaml.safe_load(f) or {}
    current = data.get("target", "not set")
    if current == "not set":
        console.print("  Current target: [cyan]not set[/cyan]")
        console.print("\n  Use [cyan]lc target --set <name>[/cyan] to choose one.")
        return
    if current == "local":
        console.print("  Current target: [cyan]local[/cyan] (local execution)")
        console.print("\n  Use [cyan]lc target --set <name>[/cyan] to switch.")
        return

    config = load_target(current)
    if config is None:
        console.print(f"  Current target: [cyan]{current}[/cyan]")
        console.print(
            "  [yellow]Warning:[/yellow] target is not configured. "
            "Run [cyan]lc target add[/cyan] or [cyan]--set[/cyan] "
            "to a known target."
        )
        return
    _display_target(current, config)


def _display_target(name: str, config: dict) -> None:
    """Print an agent-safe view of a target: options only.

    Omits implementation details (backend type, hostnames, cache
    state) that would encourage bypassing ``lc``.  Shows just the
    orthogonal option axes with their defaults and per-choice guidance,
    plus the resource guardrails and the adjustment strategy.
    """
    from lightcone.engine.targets import (
        OPTION_AXES,
        get_option_choices,
        get_option_default,
        get_option_guidance,
        get_options,
    )

    console.print(f"[bold]Target: {name}[/bold]")
    site = config.get("site")
    if site:
        console.print(f"  Site: {site}")

    options = get_options(config)
    if options:
        console.print(
            "\n  [bold]Run options[/bold] "
            "[dim](override with `lc run --<option> <value>`)[/dim]"
        )
        for axis in OPTION_AXES:
            if axis not in options:
                continue
            default = get_option_default(config, axis)
            choices = get_option_choices(config, axis)
            guidance = get_option_guidance(config, axis)
            header = f"    {axis}"
            if default is not None:
                header += f" [dim](default: {default})[/dim]"
            console.print(header)
            if guidance:
                console.print(f"      [dim]{guidance}[/dim]")
            if choices:
                width = max(len(c) for c in choices)
                for value, desc in choices.items():
                    suffix = f" — {desc}" if desc else ""
                    console.print(f"      {value:<{width}}{suffix}")

    limits = config.get("resource_limits", {})
    if limits:
        console.print("\n  [bold]Resource limits[/bold]")
        for key, val in limits.items():
            console.print(f"    {key}: {val}")

    strategy = config.get("strategy")
    if strategy:
        console.print(f"\n  [bold]Adjustment strategy:[/bold] {strategy}")


@target.command("refresh")
@click.argument("name")
def target_refresh(name: str) -> None:
    """Refresh the cached option limits for a target.

    Re-reads the environment's current capacity so ``lc run`` can
    validate and auto-adjust your option choices.  Run this periodically
    if the available options change.
    """
    from lightcone.engine.targets import (
        get_option_choices,
        load_target,
        refresh_cluster_cache,
    )

    config = load_target(name)
    if config is None:
        console.print(f"[red]Error:[/red] No configured target '{name}'.")
        raise SystemExit(1)

    if config.get("backend") != "slurm":
        console.print(
            f"Target '{name}' has no external option limits to refresh."
        )
        return

    info = refresh_cluster_cache(name)
    console.print(
        f"[green]✓[/green] Refreshed option limits for '{name}'."
    )

    # Warn about declared QoS values not available in the environment.
    from lightcone.engine.targets import (
        get_cache_key_overrides,
        resolve_cache_key,
    )
    overrides = get_cache_key_overrides(config)
    # Each (qos, constraint) pair is validated; warn if neither the
    # prefixed nor bare form resolves to an available cache record.
    qos_choices = list(get_option_choices(config, "qos"))
    constraint_values: list[str | None] = (
        list(get_option_choices(config, "constraint")) or [None]
    )
    for qos_value in qos_choices:
        for constraint in constraint_values:
            key = resolve_cache_key(qos_value, constraint, info.qos, overrides)
            if key not in info.qos:
                label = (
                    f"{qos_value} / {constraint}" if constraint else qos_value
                )
                console.print(
                    f"  [yellow]⚠[/yellow] option '{label}' is not available "
                    "in the current environment."
                )


def _discover_options() -> tuple[dict[str, Any], dict[str, str]]:
    """Try live discovery; return ``(options, cache_key_overrides)``."""
    try:
        from lightcone.engine.slurm_info import (
            build_option_suggestions,
            discover_cluster,
        )

        console.print("\n  Querying the environment for available options...")
        cluster = discover_cluster()
        if not cluster.qos:
            console.print("  [dim](no options discovered)[/dim]")
            return {}, {}
        options, overrides = build_option_suggestions(cluster)
        q_count = len((options.get("qos") or {}).get("choices", {}))
        console.print(f"  Found {q_count} qos choices.")
        return options, overrides
    except Exception as exc:
        logger.debug("option discovery failed: %s", exc)
        console.print("  [dim](option discovery not available)[/dim]")
        return {}, {}


def _prompt_option_default(
    axis: str,
    choices: dict[str, str],
    hint: str | None = None,
) -> str:
    """Prompt the user to pick the default value for *axis*."""
    items = list(choices.items())
    if len(items) == 1:
        return items[0][0]
    console.print(f"\n  [bold]Default {axis}[/bold]:")
    default_idx = 1
    for i, (value, desc) in enumerate(items, 1):
        suffix = f" — {desc}" if desc else ""
        console.print(f"    {i}. {value}{suffix}")
        if hint and value == hint:
            default_idx = i
    idx = click.prompt(
        f"  Default {axis}",
        type=click.IntRange(1, len(items)),
        default=default_idx,
    )
    return str(items[idx - 1][0])


@target.command("add")
@click.argument("name", required=False)
def target_add(name: str | None) -> None:
    """Create a new execution target."""
    from lightcone.engine.site_registry import get_site_defaults, list_known_sites
    from lightcone.engine.targets import save_target

    console.print("\n[bold]Create New Target[/bold]\n")

    known = list_known_sites()
    hpc_sites = [(k, d) for k, d in known if k != "local"]

    console.print("  [bold]Site type:[/bold]")
    console.print("    1. Local (Docker)")
    for i, (_key, display) in enumerate(hpc_sites, 2):
        console.print(f"    {i}. {display}")
    console.print(f"    {len(hpc_sites) + 2}. Other remote site")

    site_choices = [str(i) for i in range(1, len(hpc_sites) + 3)]
    choice = click.prompt(
        "\n  Select site type",
        type=click.Choice(site_choices),
        default="1",
    )

    if choice == "1":
        target_name = name or "local"
        local_config: dict[str, Any] = {
            "site": "local",
            "backend": "local",
            "connection": {},
        }
        path = save_target(target_name, local_config)
        console.print(f"\n  [green]✓[/green] Created target '{target_name}' at {path}")
        return

    if choice == str(len(hpc_sites) + 2):  # "Other remote site"
        site_key = None
        site = {}
    else:
        site_key = hpc_sites[int(choice) - 2][0]
        site = get_site_defaults(site_key) or {}

    hostname_default = site.get("connection", {}).get("hostname", "")
    hostname = (
        hostname_default
        or click.prompt("  Hostname", default=hostname_default)
    )
    username = click.prompt("  Username", default=os.environ.get("USER", ""))
    account = click.prompt("  Account/allocation")
    container_runtime = site.get("container_runtime")
    if not container_runtime:
        container_runtime = click.prompt(
            "  Container runtime (blank to skip)", default="",
        ) or None

    target_name = name or (f"{site_key}-{account}" if site_key else hostname)

    # --- Options ---
    suggested = site.get("suggested_options") or {}
    overrides = dict(site.get("cache_key_overrides") or {})
    if not suggested:
        suggested, discovered_overrides = _discover_options()
        overrides.update(discovered_overrides)

    options: dict[str, Any] = {}
    for axis in ("qos", "constraint", "time_limit"):
        axis_spec = suggested.get(axis)
        if not axis_spec:
            continue
        axis_choices = axis_spec.get("choices") or {}
        hint = axis_spec.get("default")
        if isinstance(axis_choices, dict) and axis_choices:
            default = _prompt_option_default(axis, axis_choices, hint)
            entry: dict[str, Any] = {
                "default": default, "choices": dict(axis_choices),
            }
        elif hint:
            entry = {"default": hint}
        else:
            continue
        if axis_spec.get("guidance"):
            entry["guidance"] = axis_spec["guidance"]
        options[axis] = entry
    if account:
        options["account"] = {"default": account}

    hpc_config: dict[str, Any] = {
        "site": site_key or hostname,
        "backend": "slurm",
        "connection": {"hostname": hostname, "username": username},
    }
    if container_runtime:
        hpc_config["container_runtime"] = container_runtime
    if options:
        hpc_config["options"] = options
    if overrides:
        hpc_config["cache_key_overrides"] = overrides

    # --- Resource limits ---
    console.print("\n  [bold]Resource limits[/bold]")
    console.print("  (caps on what a recipe may request per run)\n")
    hpc_config["resource_limits"] = {
        "max_nodes": click.prompt("  Max nodes per job", type=int, default=4),
        "max_walltime_minutes": click.prompt(
            "  Max walltime (minutes)", type=int, default=360,
        ),
        "max_concurrent_jobs": click.prompt(
            "  Max concurrent jobs", type=int, default=8,
        ),
    }
    hpc_config["strategy"] = "fit"

    path = save_target(target_name, hpc_config)
    console.print(
        f"\n  [green]✓[/green] Created target '{target_name}' at {path}"
    )

    # Cache the environment's option limits for validation.
    try:
        from lightcone.engine.targets import refresh_cluster_cache
        refresh_cluster_cache(target_name)
        console.print("  [green]✓[/green] Cached option limits.")
    except Exception as exc:
        logger.debug("refresh_cluster_cache failed: %s", exc)
        console.print(
            f"  [dim](limits cache not available — run "
            f"`lc target refresh {target_name}` later)[/dim]"
        )


@target.command("edit")
@click.argument("name")
def target_edit(name: str) -> None:
    """Edit an existing execution target by opening its YAML.

    Targets live at ``~/.lightcone/targets/<name>.yaml``.  Options are
    orthogonal and human-readable; edit directly with your preferred
    editor.
    """
    from lightcone.engine.targets import get_targets_dir, load_target

    config = load_target(name)
    if config is None:
        console.print(f"[red]Error:[/red] No configured target '{name}'.")
        raise SystemExit(1)
    path = get_targets_dir() / f"{name}.yaml"
    editor = os.environ.get("EDITOR", "vi")
    console.print(f"Opening {path} with {editor}...")
    subprocess.call([editor, str(path)])


# =============================================================================
# Setup command
# =============================================================================


def _run_setup_menu() -> None:
    """Show the setup management menu when config already exists."""
    from lightcone.engine.targets import list_targets, load_user_config, save_user_config

    user_config = load_user_config()
    default = user_config.get("default_target", "local")
    tier = user_config.get("default_permission_tier", "recommended")
    extraction_model = user_config.get("extraction_model", "sonnet")
    extraction_display = extraction_model if extraction_model else "inherit"
    targets = list_targets()
    target_names = ["local"] + [t for t in targets if t != "local"]

    console.print("\n[bold]lightcone-cli Setup[/bold]")
    console.print(f"  Default target:      {default}")
    console.print(f"  Permission level:    {tier}")
    console.print(f"  Extraction model:    {extraction_display}")
    console.print(f"  Targets:             {', '.join(target_names)}")

    console.print("\n  1. Change permission level")
    console.print("  2. Change extraction model")
    console.print("  3. Add a target")
    console.print("  4. Edit a target")
    console.print("  5. Change default target")
    console.print("  6. Re-run setup wizard")
    console.print("  7. Exit")

    choice = click.prompt(
        "\n  Select action",
        type=click.Choice(["1", "2", "3", "4", "5", "6", "7"]),
        default="7",
    )

    if choice == "7":
        return
    elif choice == "1":
        _prompt_permission_tier()
    elif choice == "2":
        _prompt_extraction_model()
    elif choice == "3":
        ctx = click.get_current_context()
        ctx.invoke(target_add)
    elif choice == "4":
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
    elif choice == "5":
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
    elif choice == "6":
        _run_setup_wizard()


def _run_setup_wizard() -> list[Path]:
    """Run the interactive setup wizard.

    Creates one target per node type for HPC sites, plus a local target.
    Returns the list of paths where target configs were saved.
    """
    from lightcone.engine.site_registry import get_site_defaults, list_known_sites
    from lightcone.engine.targets import load_user_config, save_target, save_user_config

    console.print("\n[bold]lightcone-cli Setup — Target Configuration[/bold]")
    console.print(
        "  These settings are stored in [cyan]~/.lightcone/targets/[/cyan] and "
        "referenced by projects via .lightcone/lightcone.yaml.\n"
    )

    saved_paths: list[Path] = []
    default_target = "local"

    # --- Configure HPC? ---
    configure_hpc = click.confirm(
        "  Configure a remote execution site (HPC)?",
        default=False,
    )

    if configure_hpc:
        known = list_known_sites()
        hpc_sites = [(k, d) for k, d in known if k != "local"]
        console.print("\n  [bold]Sites:[/bold]")
        for i, (_key, display) in enumerate(hpc_sites, 1):
            console.print(f"    {i}. {display}")
        console.print(f"    {len(hpc_sites) + 1}. Other remote site")

        site_choices = [str(i) for i in range(1, len(hpc_sites) + 2)]
        site_idx = click.prompt(
            "\n  Select site",
            type=click.Choice(site_choices),
            default="1",
        )
        selected_idx = int(site_idx) - 1

        if selected_idx < len(hpc_sites):
            site_key = hpc_sites[selected_idx][0]
            site = get_site_defaults(site_key) or {}
            display = site.get("display_name", site_key)
            hostname = site.get("connection", {}).get("hostname", "")
            console.print(f"  Detected: [cyan]{display}[/cyan] ({hostname})\n")
            username = click.prompt(
                "  Username", default=os.environ.get("USER", ""),
            )
            account = click.prompt("  Account/allocation")
            container_runtime = site.get("container_runtime")
            default_name = f"{site_key}-{account}"
            target_name = click.prompt("  Target name", default=default_name)

            suggested = site.get("suggested_options") or {}
            overrides = dict(site.get("cache_key_overrides") or {})

            options: dict[str, Any] = {}
            for axis in ("qos", "constraint", "time_limit"):
                axis_spec = suggested.get(axis)
                if not axis_spec:
                    continue
                choices = axis_spec.get("choices") or {}
                hint = axis_spec.get("default")
                if isinstance(choices, dict) and choices:
                    default = _prompt_option_default(axis, choices, hint)
                    entry: dict[str, Any] = {
                        "default": default, "choices": dict(choices),
                    }
                elif hint:
                    entry = {"default": hint}
                else:
                    continue
                if axis_spec.get("guidance"):
                    entry["guidance"] = axis_spec["guidance"]
                options[axis] = entry
            if account:
                options["account"] = {"default": account}

            target_config: dict[str, Any] = {
                "site": site_key,
                "backend": "slurm",
                "connection": {"hostname": hostname, "username": username},
            }
            if container_runtime:
                target_config["container_runtime"] = container_runtime
            if options:
                target_config["options"] = options
            if overrides:
                target_config["cache_key_overrides"] = overrides
            target_config["strategy"] = "fit"
            target_config["resource_limits"] = {
                "max_nodes": 4,
                "max_walltime_minutes": 360,
                "max_concurrent_jobs": 8,
            }

        else:
            console.print("\n  [bold]Custom remote site[/bold]\n")
            cluster_name = click.prompt("  Name (e.g. frontier, summit)")
            hostname = click.prompt("  Hostname", default=cluster_name)
            username = click.prompt(
                "  Username", default=os.environ.get("USER", ""),
            )
            account = click.prompt("  Account/allocation")
            container_runtime = click.prompt(
                "  Container runtime (blank to skip)", default="",
            ) or None
            default_name = f"{cluster_name}-{account}"
            target_name = click.prompt("  Target name", default=default_name)

            suggested, overrides = _discover_options()
            options = {}
            for axis in ("qos", "constraint", "time_limit"):
                axis_spec = suggested.get(axis)
                if not axis_spec or not axis_spec.get("choices"):
                    continue
                default = _prompt_option_default(
                    axis, axis_spec["choices"], axis_spec.get("default"),
                )
                options[axis] = {
                    "default": default, "choices": dict(axis_spec["choices"]),
                }
            if account:
                options["account"] = {"default": account}

            target_config = {
                "site": cluster_name,
                "backend": "slurm",
                "connection": {"hostname": hostname, "username": username},
            }
            if container_runtime:
                target_config["container_runtime"] = container_runtime
            if options:
                target_config["options"] = options
            if overrides:
                target_config["cache_key_overrides"] = overrides
            target_config["strategy"] = "fit"

        path = save_target(target_name, target_config)
        saved_paths.append(path)
        console.print(f"  [green]✓[/green] Created target: {target_name}")

        # Cache available option limits for validation.
        if target_config.get("backend") == "slurm":
            try:
                from lightcone.engine.targets import refresh_cluster_cache
                refresh_cluster_cache(target_name)
                console.print("  [green]✓[/green] Cached option limits.")
            except Exception:
                pass

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

    # --- Extraction model (default to sonnet) ---
    if "extraction_model" not in user_config:
        user_config["extraction_model"] = "sonnet"
        save_user_config(user_config)

    console.print(
        "\n  To list configured targets:  [cyan]lc target --list[/cyan]"
        "\n  To add more targets:         [cyan]lc target add[/cyan]"
        "\n  To edit a target:            [cyan]lc target edit <name>[/cyan]"
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

    Settings are stored at the user level (~/.lightcone/targets/) and
    referenced by projects via .lightcone/lightcone.yaml.

    Examples:
        lc setup                        # interactive wizard
        lc setup --list                 # list configured targets
        lc setup --show perlmutter-gpu  # show a target's config
        lc setup --default local        # change default target
    """
    if set_default:
        from lightcone.engine.targets import load_target, load_user_config, save_user_config
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
        from lightcone.engine.targets import load_target
        config = load_target(show_name)
        if config is None:
            console.print(f"[red]Error:[/red] No configured target '{show_name}'.")
            raise SystemExit(1)
        console.print(f"[bold]Target: {show_name}[/bold]\n")
        console.print(yaml.dump(config, default_flow_style=False, sort_keys=False))
        return

    if list_flag:
        from lightcone.engine.targets import list_targets, load_user_config
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
            "\nRun [cyan]lc target add[/cyan] to create a new target."
        )
        return

    from lightcone.engine.targets import get_config_path
    if get_config_path().exists():
        _run_setup_menu()
    else:
        _run_setup_wizard()


# =============================================================================
# Update command
# =============================================================================

# Marker that separates the lightcone-cli-managed portion of CLAUDE.md from user content.
_CLAUDE_MD_SEPARATOR = "## Analysis Context"


def _sync_project_plugins(project_dir: Path) -> bool:
    """Sync plugin files (skills, hooks, scripts, agents, CLAUDE.md) into a project.

    Returns True if the sync succeeded.
    """
    if not (project_dir / "astra.yaml").exists():
        console.print(f"  [red]✗[/red] {project_dir}: not an ASTRA project (no astra.yaml)")
        return False

    plugin_source = get_plugin_source_dir()
    if plugin_source is None:
        console.print("  [red]✗[/red] Could not find lightcone-cli plugin source files.")
        return False

    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Sync directories: skills, hooks, scripts, agents, guides
    for subdir in ("scripts", "hooks", "skills", "agents", "guides"):
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
                "_Run `/lc-new` to scope the research question and populate "
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
                "ASTRA analysis project, built with lightcone-cli.\n\n---\n\n"
                "<!-- AUTOGENERATED: /lc-new populates below during specification -->\n"
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
@click.option("--sync", is_flag=True, help="Only sync plugin files to projects (skip upgrade)")
def update(sync: bool) -> None:
    """Upgrade lightcone-cli and sync plugin files to projects.

    Upgrades lightcone-cli from PyPI, then offers to sync
    updated skills, hooks, and scripts into your projects.

    Examples:
        lc update          # upgrade package & sync projects
        lc update --sync   # just sync plugin files (no upgrade)
    """
    if not sync:
        console.print("[bold]Upgrading lightcone-cli...[/bold]\n")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "lightcone-cli"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            console.print("  [green]✓[/green] lightcone-cli upgraded")
        else:
            console.print(f"  [red]✗[/red] upgrade failed: {proc.stderr.strip()[:200]}")
            raise SystemExit(1)

    _prompt_sync_projects()


# Register eval subgroup (requires optional 'eval' extra)
try:
    from lightcone.eval.cli import eval_group

    main.add_command(eval_group, "eval")
except ImportError:
    pass


if __name__ == "__main__":
    main()
