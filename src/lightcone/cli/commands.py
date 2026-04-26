"""Command-line interface for lightcone-cli — the ASTRA-compliant agentic layer.

The redesigned CLI is a thin shim over Snakemake. Provenance integrity
(per-output content-addressed manifests) is implemented in
:mod:`lightcone.engine.manifest`; ``lc run`` generates a Snakefile from
``astra.yaml`` and shells out to ``snakemake``.

Commands:
- ``lc init``   — scaffold a project (CLAUDE.md, .claude/, venv, gitignore).
- ``lc run``    — generate Snakefile and run snakemake.
- ``lc status`` — manifest-driven status walk (no Snakemake needed).
- ``lc verify`` — recompute hashes and validate the provenance chain.
- ``lc build``  — build containers from Containerfiles.
- ``lc setup``  — minimal first-run configuration.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console

from lightcone.cli.plugin import get_plugin_source_dir

console = Console()
logger = logging.getLogger(__name__)


PERMISSION_TIERS: dict[str, dict[str, list[str]]] = {
    "yolo": {
        "allow": ["Bash(*)", "Edit", "Read", "Write", "WebSearch", "WebFetch", "mcp__*"],
    },
    "recommended": {
        "allow": ["Read", "Edit", "Write", "Bash(*)", "WebSearch", "WebFetch"],
        "deny": [
            "Edit(~/.ssh/**)",
            "Edit(~/.aws/**)",
            "Edit(~/.gnupg/**)",
            "Edit(//scratch/**)",
            "Edit(//pscratch/**)",
            "Bash(sudo *)",
            "Bash(rm -rf /*)",
            "Bash(git push *)",
            "Bash(git push)",
        ],
    },
    "minimal": {"allow": ["Read"]},
}


def _config_path() -> Path:
    return Path.home() / ".lightcone" / "config.yaml"


@click.group()
@click.version_option(package_name="lightcone-cli")
@click.pass_context
def main(ctx: click.Context) -> None:
    """lightcone-cli — ASTRA-compliant agentic layer CLI."""
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand in ("setup", "init", "eval"):
        return
    if not _config_path().exists():
        console.print(
            "\n[bold yellow]No global configuration found.[/bold yellow] "
            "Run [cyan]lc setup[/cyan] first.\n"
        )
        sys.exit(1)


# =============================================================================
# Project discovery
# =============================================================================


def _project_root(start: Path | None = None) -> Path:
    """Walk up from cwd until we find ``astra.yaml``. Errors if absent."""
    p = (start or Path.cwd()).resolve()
    for parent in [p, *p.parents]:
        if (parent / "astra.yaml").is_file():
            return parent
    raise click.ClickException(
        "No astra.yaml found in current directory or any parent. "
        "Run `lc init` to create one."
    )


# =============================================================================
# lc init
# =============================================================================


@main.command()
@click.argument("directory", type=click.Path(path_type=Path), default=".")
@click.option("--no-git", is_flag=True, help="Skip git init")
@click.option("--no-venv", is_flag=True, help="Skip Python venv creation")
@click.option(
    "--permissions",
    type=click.Choice(["yolo", "recommended", "minimal"]),
    default="recommended",
    help="Claude Code permission tier",
)
def init(directory: Path, no_git: bool, no_venv: bool, permissions: str) -> None:
    """Scaffold a new ASTRA project with Claude Code integration.

    Creates ``astra.yaml`` (boilerplate), ``CLAUDE.md``, ``.claude/`` (skills,
    agents, hooks, settings), ``.gitignore``, and an optional Python venv.
    """
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)

    if (directory / "astra.yaml").exists():
        raise click.ClickException(f"{directory}/astra.yaml already exists.")

    # Boilerplate astra.yaml
    (directory / "astra.yaml").write_text(_BOILERPLATE_ASTRA)

    # .gitignore
    (directory / ".gitignore").write_text(_GITIGNORE)

    # .lightcone/ project state dir
    (directory / ".lightcone").mkdir(exist_ok=True)
    (directory / ".lightcone" / "lightcone.yaml").write_text(
        yaml.safe_dump({"target": "local"})
    )

    # results/ directory placeholder
    (directory / "results").mkdir(exist_ok=True)
    (directory / "universes").mkdir(exist_ok=True)

    # Claude Code plugin bundle
    plugin_source = get_plugin_source_dir()
    if plugin_source is not None and plugin_source.exists():
        _install_claude_plugin(directory, plugin_source, permissions)

    # Project CLAUDE.md (a stub)
    (directory / "CLAUDE.md").write_text(_PROJECT_CLAUDE_MD)

    # git init
    if not no_git:
        subprocess.run(["git", "init", "-q"], cwd=directory, check=False)

    # venv
    if not no_venv:
        subprocess.run(["python", "-m", "venv", ".venv"], cwd=directory, check=False)

    console.print(f"\n[green]Project initialized at[/green] {directory}")
    console.print("\nNext steps:")
    console.print("  • Edit [cyan]astra.yaml[/cyan] to declare outputs and recipes")
    console.print("  • [cyan]lc run[/cyan] to materialize outputs")
    console.print("  • [cyan]lc status[/cyan] to check what's done")


_BOILERPLATE_ASTRA = '''# astra.yaml — declarative analysis spec
# See https://docs.lightcone.science for the full schema.
$schema: "https://astra-spec.org/v1/schema.json"
version: "1.0"
name: "My Analysis"
description: "Replace with a description of what this project does."

inputs: []

outputs:
  - id: example_output
    type: data
    description: "An example output. Replace with your own."
    recipe:
      command: |
        echo "Hello from a recipe" > {output[0]}/result.txt

decisions: {}
'''


_GITIGNORE = """# Python
__pycache__/
*.pyc
.venv/
*.egg-info/

# lightcone state
.lightcone/Snakefile
.lightcone/snakefile-config.json
.snakemake/

# Materialized results — track at your discretion
# results/
"""

_PROJECT_CLAUDE_MD = """# Project Notes for Claude

This is an ASTRA project orchestrated by `lightcone-cli`.

To materialize outputs declared in `astra.yaml`:

```
lc run                    # all outputs in the default universe
lc run output_id          # one specific output
lc status                 # show what's materialized vs stale vs missing
lc verify                 # validate the provenance chain
```

Outputs land in `results/<universe>/<output_id>/` along with a sidecar
`.lightcone-manifest.json` recording the recipe, container, decisions,
input hashes, and output hash.
"""


def _install_claude_plugin(
    project_dir: Path,
    plugin_source: Path,
    permissions: str,
) -> None:
    """Copy the bundled Claude Code plugin into the project's ``.claude/``."""
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    for sub in ("skills", "agents", "hooks", "scripts", "guides", "templates"):
        src = plugin_source / sub
        if src.exists():
            dest = claude_dir / sub
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
    settings = {
        "permissions": PERMISSION_TIERS[permissions],
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))


# =============================================================================
# lc run
# =============================================================================


@main.command()
@click.argument("outputs", nargs=-1)
@click.option("--universe", "-u", default=None, help="Universe to materialize")
@click.option("--cores", "-j", default=None, type=int, help="Parallel jobs")
@click.option(
    "--rerun-triggers",
    default="code,input,mtime",
    help="Comma-separated rerun-triggers (default: code,input,mtime)",
)
@click.option("--dry-run", "-n", is_flag=True, help="Show what would run without running")
@click.option("--force", "-f", is_flag=True, help="Force re-materialization")
@click.option("--verbose", "-v", is_flag=True, help="Show full executor output")
@click.option(
    "--executor",
    type=click.Choice(["auto", "local", "dask"]),
    default="auto",
    help="Execution backend. 'auto' uses Dask (LocalCluster on a workstation, "
    "srun-launched workers inside an SLURM allocation, or an existing scheduler "
    "if DASK_SCHEDULER_ADDRESS is set). 'local' bypasses Dask entirely.",
)
def run(
    outputs: tuple[str, ...],
    universe: str | None,
    cores: int | None,
    rerun_triggers: str,
    dry_run: bool,
    force: bool,
    verbose: bool,
    executor: str,
) -> None:
    """Materialize outputs declared in astra.yaml."""
    from lightcone.engine.container import load_runtime
    from lightcone.engine.snakefile import discover_universes, generate

    project = _project_root()
    universes = [universe] if universe else discover_universes(project)

    choice = load_runtime(project_path=project)
    snakefile_path, cfg_path = generate(
        project, universes=universes, runtime=choice.runtime
    )

    # Provenance guard: when ``runtime: auto`` silently fell back to
    # ``none`` and the spec declares any containers, the recipe will run
    # on the host while the manifest's ``container_image`` field still
    # records the declared image — i.e. a provenance lie. Warn loudly so
    # the user installs a runtime, sets ``runtime: none`` explicitly, or
    # removes the container declarations.
    if choice.runtime == "none" and not choice.explicit:
        cfg_data = json.loads(cfg_path.read_text())
        declared = sorted({
            entry["container_image"]
            for rule_entries in cfg_data.values()
            for entry in rule_entries.values()
            if entry.get("container_image")
        })
        if declared:
            console.print(
                "[yellow]⚠ No container runtime found on PATH "
                "(checked docker, podman, podman-hpc).[/yellow]\n"
                "  The following declared containers will be ignored:\n"
                + "\n".join(f"    [dim]•[/dim] {c}" for c in declared)
                + "\n  Recipes will run on the host without isolation, "
                "but each manifest will still record\n"
                "  the declared [cyan]container_image[/cyan] — recorded "
                "provenance will not match what executed.\n"
                "  Install [cyan]docker[/cyan], [cyan]podman[/cyan], or "
                "[cyan]podman-hpc[/cyan], or set\n"
                "  [cyan]container: {runtime: none}[/cyan] in "
                "[cyan]~/.lightcone/config.yaml[/cyan] to silence.\n"
            )

    targets: list[str] = []
    if outputs:
        for o in outputs:
            for u in universes:
                targets.append(_target_for(project, o, u))
    # If no specific targets, pass nothing → snakemake runs `rule all`.

    cmd = [
        "snakemake",
        "-s", str(snakefile_path),
        "-d", str(project),
        "--cores", str(cores or os.cpu_count() or 1),
        "--rerun-triggers", *rerun_triggers.split(","),
    ]
    # Container invocation is wrapped into the recipe at generation time;
    # we don't use Snakemake's ``container:`` directive or ``--sdm`` at all.
    if dry_run:
        cmd.append("-n")
    if force:
        cmd.append("--forceall")
    if not verbose:
        # Suppress the executor's progress/rule/host/reason chatter; rule
        # bodies still print our own ``▶`` marker via stderr, and errors
        # from the executor still come through. We do NOT pass ``all``
        # here because it silences workflow errors too.
        cmd.extend(["--quiet", "rules", "progress", "host", "reason"])
    cmd.extend(targets)

    if executor == "local":
        sys.exit(_invoke_snakemake(cmd, env=None, dry_run=dry_run, verbose=verbose))

    from lightcone.engine.dask_cluster import cluster_for_run

    with cluster_for_run(verbose=verbose) as scheduler_addr:
        cmd = [*cmd, "--executor", "lightconedask"]
        env = {**os.environ, "DASK_SCHEDULER_ADDRESS": scheduler_addr}
        sys.exit(_invoke_snakemake(cmd, env=env, dry_run=dry_run, verbose=verbose))


def _invoke_snakemake(
    cmd: list[str], *, env: dict[str, str] | None, dry_run: bool, verbose: bool
) -> int:
    if verbose:
        console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    if dry_run or verbose:
        proc = subprocess.run(cmd, env=env)
        return proc.returncode
    return _run_filtered(cmd, env=env)


# Lines emitted by the executor that we drop in the default (non-verbose)
# path. Substring match on the trimmed line — these are stable banner-style
# messages, not error content. Anything not matched passes through so real
# diagnostics are never hidden.
_EXECUTOR_NOISE_PREFIXES = (
    "Building DAG of jobs",
    "Using shell:",
    "Provided cores:",
    "Provided remote nodes:",
    "Rules claiming more threads",
    "Job stats:",
    "Select jobs to execute",
    "Execute ",
    "host:",
    "Complete log:",
    "Complete log(s):",
    "Nothing to be done",
    "Workflow defines that rule",
    "Shared connection",
    "Activating conda",
    "Assuming unrestricted shared filesystem",
    "Shutting down,",
    "Exiting because a job execution failed",
    "At least one job did not complete",
    "Trying to restart job",
    "Finished jobid",
    "Finished job ",
    "Workflow finished",
    "RuleException",
    "WorkflowError",
    "CalledProcessError in file",
)


def _run_filtered(cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    """Run the executor and pass through only meaningful lines.

    Rule bodies print their own ``▶`` progress marker; this filter drops
    the executor's banner lines and recipe-replay error blocks so the
    output reads as lightcone's, not the executor's. Genuine error
    content (recipe stdout/stderr, unfamiliar diagnostic lines) passes
    through untouched.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None

    # When the executor logs a failed shell job it prints a multi-line
    # block: ``Command 'set -euo pipefail; <recipe>' returned non-zero
    # exit status N.``. The recipe's own stdout/stderr already came out
    # earlier, so this replay is pure noise — mute from "Command 'set"
    # to the closing "returned non-zero exit status" line. Also mute the
    # single line after "Removing output files of failed job" (which is
    # just the bare result path).
    in_command_replay = False
    swallow_next = 0
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        stripped = line.strip()

        if in_command_replay:
            if "returned non-zero exit status" in stripped:
                in_command_replay = False
            continue
        if swallow_next > 0:
            swallow_next -= 1
            continue
        if stripped.startswith("Command 'set -euo pipefail"):
            in_command_replay = "returned non-zero exit status" not in stripped
            continue
        if stripped.startswith("Removing output files of failed job"):
            swallow_next = 1
            continue

        if not stripped:
            continue
        if any(stripped.startswith(p) for p in _EXECUTOR_NOISE_PREFIXES):
            continue
        # The job-stats table that follows "Job stats:" — drop the rows
        # too. They look like "rulename    1" or "total    3" or a
        # separator line of dashes.
        if stripped.startswith("---") or stripped == "total":
            continue
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    return proc.wait()


def _target_for(project: Path, output_id: str, universe: str) -> str:
    """Translate an output id into a Snakemake target path (the manifest)."""
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.tree import collect_tree_outputs

    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    for to in collect_tree_outputs(spec):
        if to.output_id == output_id and to.output_def.get("recipe") is not None:
            base = (
                to.analysis_path.lstrip("./")
                if to.analysis_path
                else "results"
            )
            if to.analysis_path:
                return f"{base}/results/{universe}/{output_id}/.lightcone-manifest.json"
            return f"results/{universe}/{output_id}/.lightcone-manifest.json"
    raise click.ClickException(
        f"Output '{output_id}' not found in astra.yaml or has no recipe."
    )


# =============================================================================
# lc status
# =============================================================================


@main.command()
@click.option("--universe", "-u", default=None)
def status(universe: str | None) -> None:
    """Report materialization status for every declared output."""
    from lightcone.engine.snakefile import discover_universes
    from lightcone.engine.status import get_output_status

    project = _project_root()
    universes = [universe] if universe else discover_universes(project)

    for u in universes:
        console.print(f"\n[bold]Universe[/bold] [cyan]{u}[/cyan]")
        for s in get_output_status(project, universe_id=u):
            label = _status_label(s.status)
            scope = f"[dim]{s.analysis_id}.[/dim]" if s.analysis_id else ""
            console.print(f"  {label}  {scope}{s.output_id}")


_STATUS_STYLES = {
    "ok": "[green]✓ ok[/green]    ",
    "stale": "[yellow]✸ stale[/yellow] ",
    "missing": "[red]✗ miss[/red]  ",
    "alias": "[dim]→ alias[/dim] ",
}


def _status_label(s: str) -> str:
    return _STATUS_STYLES.get(s, s)


# =============================================================================
# lc verify
# =============================================================================


@main.command()
@click.option("--universe", "-u", default=None)
def verify(universe: str | None) -> None:
    """Validate the provenance chain by recomputing hashes."""
    from lightcone.engine.snakefile import discover_universes
    from lightcone.engine.verify import verify_outputs

    project = _project_root()
    universes = [universe] if universe else discover_universes(project)

    failed = 0
    for u in universes:
        console.print(f"\n[bold]Universe[/bold] [cyan]{u}[/cyan]")
        for r in verify_outputs(project, universe_id=u):
            if r.passed:
                console.print(f"  [green]✓ ok[/green]    {r.output_id}")
            else:
                failed += 1
                console.print(
                    f"  [red]✗ {r.failure}[/red]  {r.output_id}  [dim]{r.detail}[/dim]"
                )

    if failed:
        console.print(f"\n[bold red]{failed} integrity failure(s).[/bold red]")
        sys.exit(1)
    console.print("\n[green]All outputs verified.[/green]")


# =============================================================================
# lc build
# =============================================================================


@main.command()
@click.option("--force", is_flag=True, help="Rebuild all images even if cached")
@click.option(
    "--runtime",
    default=None,
    help="docker | podman | podman-hpc (overrides ~/.lightcone/config.yaml)",
)
def build(force: bool, runtime: str | None) -> None:
    """Build container images declared in astra.yaml.

    Containerfile syntax is Dockerfile syntax — we use ``docker``,
    ``podman``, or ``podman-hpc`` directly. Each Containerfile builds to
    an OCI image tagged ``lc-<project>-<hash>`` in the runtime's local
    image store. Pre-built registry images (``python:3.12-slim``,
    ``ghcr.io/foo/bar:tag``) are skipped — the runtime pulls them at
    ``lc run`` time.
    """
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.container import (
        ContainerBuildError,
        build_image,
        compute_image_tag,
        image_exists_locally,
        is_containerfile,
        load_runtime,
        pull_image,
    )
    from lightcone.engine.tree import collect_tree_outputs

    project = _project_root()
    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)

    try:
        if runtime:
            resolved_runtime = runtime
        else:
            choice = load_runtime(project_path=project)
            resolved_runtime = choice.runtime
    except ContainerBuildError as e:
        raise click.ClickException(str(e))

    if resolved_runtime == "none":
        console.print(
            "[yellow]No container runtime available "
            "(checked docker, podman, podman-hpc). "
            "Install one to build images, or set [cyan]container.runtime[/cyan] "
            "in [cyan]~/.lightcone/config.yaml[/cyan].[/yellow]"
        )
        return

    project_name = (spec.get("name") or project.name).lower().replace(" ", "-")

    seen: set[str] = set()
    for to in collect_tree_outputs(spec):
        recipe = to.output_def.get("recipe") or {}
        spec_str = (
            recipe.get("container")
            or to.analysis_spec.get("container")
            or spec.get("container")
        )
        if not spec_str or spec_str in seen:
            continue
        seen.add(spec_str)
        if not is_containerfile(spec_str, project):
            # Registry image — pull so ``lc run`` can use ``--pull=never``
            # without depending on the runtime's registry resolution.
            if image_exists_locally(spec_str, runtime=resolved_runtime) and not force:
                console.print(f"[dim]Cached[/dim] {spec_str}")
                continue
            console.print(
                f"[cyan]Pulling[/cyan] {spec_str} [dim](via {resolved_runtime})[/dim]"
            )
            try:
                pull_image(spec_str, runtime=resolved_runtime)
            except ContainerBuildError as e:
                raise click.ClickException(str(e))
            continue

        containerfile = project / spec_str
        tag = compute_image_tag(project_name, containerfile, project)
        if image_exists_locally(tag, runtime=resolved_runtime) and not force:
            console.print(f"[dim]Cached[/dim] {spec_str} → {tag}")
            continue
        console.print(
            f"[cyan]Building[/cyan] {spec_str} → {tag} [dim](via {resolved_runtime})[/dim]"
        )
        try:
            build_image(tag, containerfile, project, runtime=resolved_runtime)
        except ContainerBuildError as e:
            raise click.ClickException(str(e))
    console.print("[green]Done.[/green]")


# =============================================================================
# lc setup
# =============================================================================


@main.command()
def setup() -> None:
    """Minimal global configuration. Creates ``~/.lightcone/config.yaml``."""
    config = _config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.exists():
        console.print(f"Config already exists at {config}")
        return
    config.write_text(
        yaml.safe_dump(
            {
                "default_target": "local",
                # Container runtime used by `lc build` and embedded in every
                # recipe by `lc run`. ``auto`` picks the first of
                # docker/podman/podman-hpc found on PATH; set explicitly to
                # pin. ``none`` disables containerization entirely.
                "container": {"runtime": "auto"},
            }
        )
    )
    console.print(f"[green]Created[/green] {config}")
