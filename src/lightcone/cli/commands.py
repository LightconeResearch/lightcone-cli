"""Command-line interface for lightcone-cli — the ASTRA-compliant agentic layer.

The redesigned CLI is a thin shim over Snakemake. Provenance integrity
(per-output content-addressed manifests) is implemented in
:mod:`lightcone.engine.manifest`; ``lc run`` generates a Snakefile from
``astra.yaml`` and shells out to ``snakemake``.

Commands:
- ``lc init``   — scaffold a project (CLAUDE.md, .claude/, venv, gitignore,
  MyST report template).
- ``lc run``    — generate Snakefile and run snakemake.
- ``lc status`` — manifest-driven status walk (no Snakemake needed).
- ``lc verify`` — recompute hashes and validate the provenance chain.
- ``lc build``  — build containers from Containerfiles.

The global config at ``~/.lightcone/config.yaml`` is auto-created with
defaults on first invocation if missing.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import click
import yaml
from rich.console import Console

from lightcone.cli.plugin import get_plugin_source_dir

console = Console()
logger = logging.getLogger(__name__)


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
        "allow": ["Read", "Edit", "Write", "Bash(*)", "WebSearch", "WebFetch"],
        # Patterns under "ask" prompt the user before the agent can act,
        # but don't block outright the way "deny" does. Use "ask" for
        # paths the agent legitimately *might* need to write to but
        # where a stray edit would be expensive — scratch filesystems
        # being the obvious case on HPC, where projects often live in
        # $SCRATCH and a careless edit could trash someone else's data.
        "ask": [
            "Edit(//scratch/**)",
            "Edit(//pscratch/**)",
            "Write(//scratch/**)",
            "Write(//pscratch/**)",
        ],
        "deny": [
            "Edit(~/.ssh/**)",
            "Edit(~/.aws/**)",
            "Edit(~/.gnupg/**)",
            "Bash(sudo *)",
            "Bash(rm -rf *)",
            "Bash(rm -fr *)",
            "Bash(git push *)",
            "Bash(git push)",
        ],
    },
    "minimal": {"allow": ["Read"]},
}


def _config_path() -> Path:
    return Path.home() / ".lightcone" / "config.yaml"


def _ensure_global_config() -> None:
    """Create ``~/.lightcone/config.yaml`` with defaults if missing."""
    config = _config_path()
    if config.exists():
        return
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        yaml.safe_dump(
            {
                # Container runtime used by `lc build` and embedded in every
                # recipe by `lc run`. ``auto`` picks the first of
                # podman/docker/podman-hpc found on PATH (skipping docker if
                # its daemon is unreachable); set explicitly to pin. ``none``
                # disables containerization entirely.
                "container": {"runtime": "auto"},
            }
        )
    )


@click.group()
@click.version_option(package_name="lightcone-cli")
@click.pass_context
def main(ctx: click.Context) -> None:
    """lightcone-cli — ASTRA-compliant agentic layer CLI."""
    ctx.ensure_object(dict)
    _ensure_global_config()


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
_LIGHTCONE = """
_______________________
| . _ |_ _|_ _ _  _  _
|_|(_|| | | (_(_)| |(/_
_____|_________________
"""


@main.command()
@click.argument("directory", type=click.Path(path_type=Path), default=".")
@click.option("--no-git", is_flag=True, help="Skip git init")
@click.option("--no-venv", is_flag=True, help="Skip Python venv creation")
@click.option(
    "--github",
    "github_repo",
    default=None,
    metavar="[NAME|OWNER/NAME|URL]",
    help=(
        "Connect the project to this GitHub repository (created if it "
        "doesn't exist) without prompting"
    ),
)
@click.option(
    "--private/--public",
    "github_private",
    default=None,
    help="Visibility when --github creates a new repository",
)
@click.option(
    "--no-github",
    is_flag=True,
    help="Skip the GitHub connection step entirely",
)
@click.option(
    "--permissions",
    type=click.Choice(["yolo", "recommended", "minimal"]),
    default="recommended",
    help="Claude Code permission tier",
)
@click.option(
    "--scratch",
    "scratch_override",
    default=None,
    type=str,
    help=(
        "Scratch root for snakemake state, dask spill, and run locks. "
        "Overrides the site default. Shell expressions like $SCRATCH are "
        "expanded at run time (kept verbatim in the project config)."
    ),
)
def init(
    directory: Path,
    no_git: bool,
    no_venv: bool,
    github_repo: str | None,
    github_private: bool | None,
    no_github: bool,
    permissions: str,
    scratch_override: str | None,
) -> None:
    """Scaffold a new ASTRA project with Claude Code integration.

    Delegates the spec scaffold (``astra.yaml``, ``universes/baseline.yaml``,
    base ``.gitignore``, ``src/``) to ``astra init``, then layers on the
    lightcone-specific bits: ``Containerfile`` + ``requirements.txt``,
    ``.lightcone/`` project state, ``.claude/`` plugin bundle, ``CLAUDE.md``,
    a template MyST report (``myst.yml`` + ``index.md``), an optional
    GitHub repository connection, and an optional Python venv.
    """
    console.print(f"[cyan]{_LIGHTCONE}[/cyan]")

    from astra.cli import init as astra_init

    from lightcone.engine.site_registry import detect_current_site

    directory = directory.resolve()

    if (directory / "astra.yaml").exists():
        raise click.ClickException(f"{directory}/astra.yaml already exists.")

    # Spec scaffold: astra.yaml, universes/baseline.yaml, base .gitignore,
    # src/. We hold off on git init until our own files are in place so
    # the initial commit captures the full project state.
    try:
        astra_init.callback(directory=directory, no_git=True)  # type: ignore[misc]
    except SystemExit as e:
        raise click.ClickException(f"astra init failed (exit code {e.code}).") from e

    # Point the spec at our project-local Containerfile. The astra
    # boilerplate ships ``container: python:3.12-slim`` so the scaffold
    # is runnable as-is, but we want lightcone projects to build their
    # own image so dependencies can evolve under content-addressed
    # rebuilds.
    astra_yaml_path = directory / "astra.yaml"
    astra_yaml_path.write_text(
        astra_yaml_path.read_text().replace(
            "container: python:3.12-slim", "container: Containerfile", 1
        )
    )
    (directory / "Containerfile").write_text(_CONTAINERFILE)
    (directory / "requirements.txt").write_text(_REQUIREMENTS)

    # Append lightcone-specific entries to the .gitignore astra wrote.
    gitignore_path = directory / ".gitignore"
    gitignore_path.write_text(gitignore_path.read_text() + _GITIGNORE_APPEND)

    # .lightcone/ project state dir + lightcone.yaml
    (directory / ".lightcone").mkdir(exist_ok=True)
    project_cfg: dict[str, object] = {"target": "local"}
    if scratch_override:
        project_cfg["scratch_root"] = scratch_override
    (directory / ".lightcone" / "lightcone.yaml").write_text(
        yaml.safe_dump(project_cfg)
    )

    # results/ directory placeholder
    (directory / "results").mkdir(exist_ok=True)

    # Claude Code plugin bundle
    plugin_source = get_plugin_source_dir()
    if plugin_source is not None and plugin_source.exists():
        _install_claude_plugin(directory, plugin_source, permissions)

    # Project CLAUDE.md (a stub)
    (directory / "CLAUDE.md").write_text(_PROJECT_CLAUDE_MD)

    # Template MyST report. MyST support is a recommended add-on on top of
    # the spec, not part of it — which is why the report scaffold lives here
    # and not in `astra init`.
    _create_report_template(directory)

    # git init last so the initial commit captures every scaffolded file.
    no_git = no_git or (directory / ".git").exists()
    if not no_git:
        subprocess.run(["git", "init", "-q"], cwd=directory, check=False)
        console.print("[green]✓[/green] Initialized git repository")

    # GitHub connection: encouraged (a repo backs the analysis up and is
    # what a hub deployment's image builder clones), never forced.
    if not no_github and not no_git:
        _connect_github(
            directory, repo_input=github_repo, private=github_private
        )

    # venv: the project's *science* environment — requirements.txt, the
    # same dependency list the Containerfile installs — so the code has a
    # fast local dev loop before any container/cluster is involved.
    # Deliberately NOT lightcone-cli: installing a second lc (and its
    # snakemake) into the venv would shadow the deployment's install the
    # moment the venv is activated, and a version-skewed lc produces
    # errors that point everywhere but at the shadowing (observed on the
    # hub: a stale venv lc predates the site's build service and cluster
    # backend). `lc` stays a tool of the ambient environment.
    if not no_venv:
        if shutil.which("uv"):
            with console.status("[dim]Creating virtual environment…[/dim]"):
                subprocess.run(
                    ["uv", "venv", "--python", "3.12", ".venv"],
                    cwd=directory,
                    check=False,
                    capture_output=True,
                )
            with console.status("[dim]Installing project dependencies…[/dim]"):
                subprocess.run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        ".venv/bin/python",
                        "-r",
                        "requirements.txt",
                    ],
                    cwd=directory,
                    check=False,
                    capture_output=True,
                )
        else:
            with console.status("[dim]Creating virtual environment…[/dim]"):
                subprocess.run(
                    ["python", "-m", "venv", ".venv"],
                    cwd=directory,
                    check=False,
                    capture_output=True,
                )
            with console.status("[dim]Installing project dependencies…[/dim]"):
                subprocess.run(
                    [
                        ".venv/bin/python",
                        "-m",
                        "pip",
                        "install",
                        "-q",
                        "-r",
                        "requirements.txt",
                    ],
                    cwd=directory,
                    check=False,
                    capture_output=True,
                )
        console.print(
            f"[green]✓[/green] Virtual environment created in "
            f"[cyan]{directory}/.venv[/cyan] "
            f"[dim](project dependencies; `lc` itself stays on the ambient "
            "PATH)[/dim]"
        )

    console.print(f"\n[green]Project initialized at[/green] {directory}")

    # Surface the resolved scratch root if a known site was detected — gives
    # users early visibility into where lc run will keep its operational
    # state (snakemake metadata, dask spill, cross-node locks). On NERSC
    # this is critical: $HOME and CFS are mounted via DVS (no flock, slow
    # small-file I/O), so lightcone keeps everything on $SCRATCH (Lustre).
    site = detect_current_site()
    if site:
        scratch_expr = scratch_override or site.get("scratch_root")
        if scratch_expr:
            console.print(f"\n[dim]Detected site:[/dim] {site.display_name}")
            console.print(
                f"[dim]Scratch root for lc run:[/dim] [cyan]{scratch_expr}[/cyan] "
                f"[dim](resolved at run time)[/dim]"
            )

    console.print("\nNext steps:")
    console.print(f"  • Go to the newly created directory [cyan]cd {directory}[/cyan]")
    console.print("  • Start [cyan]claude[/cyan]")
    console.print(
        "  • Run [cyan]/lc-new[/cyan] to scope a new analysis, "
        "[cyan]/lc-from-code[/cyan] to port existing code, "
        "or [cyan]/lc-from-paper[/cyan] to reproduce a paper"
    )
    console.print(
        "  • Preview the report with [cyan]myst start[/cyan] "
        "(requires the MyST CLI: [cyan]npm i -g mystmd[/cyan])"
    )


def _connect_github(
    directory: Path, *, repo_input: str | None, private: bool | None
) -> None:
    """The ``lc init`` GitHub step: authenticate, create/connect, push.

    Deliberately non-fatal: scaffolding must never be lost to a network
    hiccup or a declined prompt — any failure degrades to a printed
    "here's how to do it later" and init continues. Interactive prompts
    appear only on a TTY; scripted/agent runs use ``--github``/
    ``--no-github`` (no flags → a one-line hint, no blocking).
    """
    from lightcone.engine import github as gh_engine
    from lightcone.engine.site_registry import detect_current_site

    probe = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        console.print(
            f"[green]✓[/green] GitHub remote already configured "
            f"([cyan]{probe.stdout.strip()}[/cyan])"
        )
        return

    interactive = (
        repo_input is None and sys.stdin.isatty() and sys.stdout.isatty()
    )
    if repo_input is None and not interactive:
        console.print(
            "[dim]No GitHub repository connected (non-interactive run). "
            "Connect later with `lc init --github <name>` semantics: "
            "`gh repo create --source . --push`.[/dim]"
        )
        return

    on_hub = detect_current_site().key == "lightcone-hub"
    try:
        console.print(
            "\n[bold]GitHub[/bold] [dim]— a repository backs your analysis "
            "up and is what the hub's image builder clones for cloud "
            "runs.[/dim]"
        )
        if interactive and not click.confirm(
            "  Connect this project to a GitHub repository?", default=True
        ):
            console.print(
                "  [dim]Skipped. Later: `gh repo create --source . "
                "--push`.[/dim]"
            )
            return

        identity = gh_engine.discover_identity()
        if identity is None and gh_engine.device_flow_client_id():

            def _show_code(code: str, uri: str) -> None:
                console.print(
                    f"  To authorize, enter code [bold cyan]{code}[/bold cyan] "
                    f"at [cyan]{uri}[/cyan]\n"
                    "  [dim]Waiting for authorization… (Ctrl-C to skip)[/dim]"
                )

            identity = gh_engine.device_flow(_show_code)
            where = gh_engine.persist_token(identity)
            console.print(
                f"[green]✓[/green] Authenticated as "
                f"[cyan]{identity.login}[/cyan]"
                + (f" [dim]({where})[/dim]" if where else "")
            )
        elif identity is None and interactive and shutil.which("gh"):
            if click.confirm(
                "  No GitHub credential found. Run `gh auth login` now?",
                default=True,
            ):
                subprocess.run(["gh", "auth", "login"], check=False)
                identity = gh_engine.discover_identity()
        if identity is None:
            console.print(
                "  [yellow]No GitHub credential available.[/yellow] "
                "[dim]Authenticate (`gh auth login`) and connect later "
                "with `gh repo create --source . --push`.[/dim]"
            )
            return
        if identity.source != "device":
            console.print(
                f"[green]✓[/green] Authenticated as "
                f"[cyan]{identity.login}[/cyan] [dim](via {identity.source})[/dim]"
            )

        raw = repo_input or click.prompt(
            "  Repository (name, owner/name, or URL of an existing repo)",
            default=directory.name,
        )
        target = gh_engine.resolve_repo(identity, raw)
        if target.exists:
            console.print(
                f"[green]✓[/green] Connecting to existing repository "
                f"[cyan]{target.full_name}[/cyan]"
            )
        else:
            if private is None:
                # The hub's image builder clones anonymously, so public
                # is the default that keeps `lc run` working there;
                # everywhere else default to private.
                private = (
                    click.confirm(
                        "  Keep the repository private?", default=not on_hub
                    )
                    if interactive
                    else not on_hub
                )
            gh_engine.create_repo(identity, target, private=private)
            console.print(
                f"[green]✓[/green] Created "
                f"{'private' if private else 'public'} repository "
                f"[cyan]{target.full_name}[/cyan]"
            )
            if private and on_hub:
                console.print(
                    "  [yellow]⚠ The hub's image builder can only clone "
                    "public repositories today[/yellow] — cloud runs will "
                    "fail to build until the repo is public\n"
                    "  ([cyan]gh repo edit --visibility public[/cyan]) or "
                    "the deployment gets a clone token."
                )
        gh_engine.connect_and_push(directory, identity, target)
        console.print(
            f"[green]✓[/green] Pushed initial commit → "
            f"[cyan]{target.url}[/cyan]"
        )
    except KeyboardInterrupt:
        console.print(
            "\n  [dim]GitHub step skipped. Later: `gh repo create "
            "--source . --push`.[/dim]"
        )
    except gh_engine.GitHubError as e:
        console.print(
            f"  [yellow]GitHub step failed:[/yellow] {e}\n"
            "  [dim]The project is fully scaffolded locally; connect "
            "later with `gh repo create --source . --push`.[/dim]"
        )


_CONTAINERFILE = """\
FROM python:3.12-slim

# uv, pulled from its official image (fast, reproducible, no curl bootstrap).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Cluster deployments run this image as uid 1000; slim bases have no
# passwd entry for it, and tools that call getpass.getuser() (snakemake,
# at startup) crash on a passwd-less uid. Give it a name.
RUN useradd --uid 1000 --create-home lightcone

WORKDIR /app

# On a Kubernetes / Dask Gateway deployment the pod image IS the execution
# environment, so it must contain lightcone-cli. Installing it here also pulls
# dask, distributed, and the dask-gateway client at pinned, hub-matching
# versions, so this image doubles as a valid Gateway worker. Add project
# dependencies to requirements.txt.
#
# --system installs into the image's Python (no venv in a container), which
# also puts dask-worker / dask-gateway-scheduler on PATH where the Gateway
# backend launches them by name.
COPY requirements.txt .
RUN uv pip install --system --no-cache "lightcone-cli[gateway]" -r requirements.txt

COPY . .
"""


_REQUIREMENTS = """\
numpy
pandas
"""


_GITIGNORE_APPEND = """
# lightcone-cli
.lightcone/Snakefile
.lightcone/snakefile-config.json
.snakemake/
.snakemake.legacy/
results/

# MyST build output
_build/
"""


def _create_report_template(directory: Path) -> None:
    """Write ``myst.yml`` + ``index.md`` — a starter report for the analysis.

    The report references the boilerplate ``astra.yaml`` elements by path via
    the MySTRA plugin, so the ids used below must track the ``astra init``
    boilerplate (``example_method``, ``main_result``).
    """
    name = directory.name or "My Analysis"
    (directory / "myst.yml").write_text(_MYST_YML)
    (directory / "index.md").write_text(f"# {name}\n" + _INDEX_MD_BODY)


_MYST_YML = """\
# MyST configuration for the analysis report (https://mystmd.org/).
# The MySTRA plugin resolves {astra}`...` references against astra.yaml.
# `latest` always tracks the newest MySTRA release; for a reproducible
# build, pin a tag instead, e.g. .../releases/download/v0.0.1/mystra.mjs
version: 1
project:
  plugins:
    - https://github.com/LightconeResearch/MySTRA/releases/latest/download/mystra.mjs
  toc:
    - file: index.md
site:
  template: book-theme
"""


_INDEX_MD_BODY = """
> **TODO:** this report was scaffolded by `lc init`. It references the
> analysis elements declared in `astra.yaml` *by path* — figures, decisions,
> and numbers stay single-sourced in the analysis, so never hard-type a
> measured value here. Preview with `myst start` (requires the MyST CLI).

## Introduction

TODO: the research question, its context, and why it matters.

## Methods

TODO: describe the approach. Reference the decisions the analysis exposes
rather than restating them — for example, we adopt the
{astra}`decisions.example_method` for this analysis:

:::{astra} decisions.example_method
:::

## Results

TODO: present the outputs. Once `lc run` has materialized results, pull
numbers in live, e.g.:

% The analysis yields {astra:value}`outputs.main_result`.

:::{astra} outputs
:::
"""

_PROJECT_CLAUDE_MD = """# Project Notes for Claude

This is an ASTRA project orchestrated by `lightcone-cli`. It was just
scaffolded by `lc init` and has not been scoped yet — `astra.yaml` holds
the placeholder example, not real science.

The three entry skills cover the common starting points:

- `/lc-new` — scope from a research question (empty `astra.yaml`).
- `/lc-from-code` — wrap an existing codebase in ASTRA.
- `/lc-from-paper` — reproduce a published paper end-to-end.

Once scoped, the `lc` CLI keeps the substrate in sync:

```
lc run                    # all outputs in the default universe
lc run output_id          # one specific output
lc status                 # show what's materialized vs stale vs missing
lc verify                 # validate the provenance chain
```

Outputs land in `results/<universe>/<output_id>/` along with a sidecar
`.lightcone-manifest.json` recording the recipe, container, decisions,
input hashes, and output hash.

## Report

`index.md` + `myst.yml` are a template MyST report wired to the MySTRA
plugin. The report references analysis elements by path — inline mentions
with the `{astra}` role, block embeds with the `{astra}` directive, live
numbers with `{astra:value}` — so never hard-type a measured value in the
prose. Preview with `myst start` (requires the MyST CLI, `npm i -g mystmd`).
"""


def _install_claude_plugin(
    project_dir: Path,
    plugin_source: Path,
    permissions: str,
) -> None:
    """Copy the bundled Claude Code plugin into the project's ``.claude/``.

    The hook configuration ships with the plugin as ``hooks.json`` so
    that hook entries live next to the scripts they reference. The CLI
    only owns the ``--permissions`` tier selection.
    """
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    for sub in ("skills", "agents", "scripts", "guides", "templates"):
        src = plugin_source / sub
        if src.exists():
            dest = claude_dir / sub
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
    hooks = json.loads((plugin_source / "hooks.json").read_text())
    settings = {
        "permissions": PERMISSION_TIERS[permissions],
        "hooks": hooks,
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))


# =============================================================================
# lc run
# =============================================================================


def _abort_on_perlmutter_login() -> None:
    """Stop-gap: refuse ``lc run`` on a Perlmutter login node.

    NERSC sets ``NERSC_HOST=perlmutter`` on every node; SLURM sets
    ``SLURM_JOB_ID`` only inside an allocation. Their conjunction (NERSC
    host + no allocation) unambiguously marks a login node, where shared
    CPU and the absence of compute resources make a real run a bad idea.

    Bypassed when ``DASK_SCHEDULER_ADDRESS`` is set, matching the branch
    in ``cluster_for_run``: if the user is targeting an external
    scheduler the login-node CPU does not matter.

    Remove once proper site-backend gating exists.
    """
    if os.environ.get("LIGHTCONE_ALLOW_LOGIN_NODE"):
        return
    if os.environ.get("NERSC_HOST") != "perlmutter":
        return
    if "SLURM_JOB_ID" in os.environ:
        return
    if os.environ.get("DASK_SCHEDULER_ADDRESS"):
        return
    raise click.ClickException(
        "Refusing to run on a Perlmutter login node — compute work must "
        "run inside a SLURM allocation.\n"
        "  Start one with, e.g.:\n"
        "    salloc -N 1 -C gpu -q interactive -t 1:00:00 -A <account>\n"
        "  then re-run `lc run` from inside."
    )


@main.command()
@click.argument("outputs", nargs=-1)
@click.option("--universe", "-u", default=None, help="Universe to materialize")
@click.option("--jobs", "-j", default=None, type=int, help="Parallel jobs")
@click.option(
    "--rerun-triggers",
    default="code,input,mtime,params",
    help="Comma-separated rerun-triggers (default: code,input,mtime,params)",
)
@click.option("--force", "-f", is_flag=True, help="Force re-materialization")
@click.option("--verbose", "-v", is_flag=True, help="Show full executor output")
def run(
    outputs: tuple[str, ...],
    universe: str | None,
    jobs: int | None,
    rerun_triggers: str,
    force: bool,
    verbose: bool,
) -> None:
    """Materialize outputs declared in astra.yaml.

    Always dispatches through a Dask cluster: a ``LocalCluster`` on a
    workstation, srun-launched workers inside a SLURM allocation, a
    run-scoped Dask Gateway cluster on a JupyterHub deployment (created
    with the project's worker image — kept up to date through the hub's
    build service — and culled when the run finishes), or an existing
    scheduler if ``DASK_SCHEDULER_ADDRESS`` is set
    (``LIGHTCONE_GATEWAY_CLUSTER=<name>`` similarly attaches to an
    existing Gateway cluster and leaves it running).
    """
    _abort_on_perlmutter_login()

    from lightcone.engine.container import KUBERNETES_RUNTIME, load_runtime
    from lightcone.engine.dask_cluster import (
        GATEWAY_CLUSTER_ENV,
        cluster_for_run,
        gateway_branch_active,
    )
    from lightcone.engine.scratch import (
        RunLockBusyError,
        acquire_run_lock,
        ensure_snakemake_symlink,
        prepare_run_dirs,
        resolve_scratch_root,
    )
    from lightcone.engine.snakefile import discover_universes, generate

    project = _project_root()
    universes = [universe] if universe else discover_universes(project)

    # Resolve scratch and prepare per-run directories before anything
    # else. Snakemake's ``.snakemake/`` is redirected via symlink so its
    # workflow lock and metadata land on a filesystem that honours
    # ``flock`` (Lustre on NERSC) rather than DVS-mounted home/CFS where
    # locks are silent no-ops. Dask spill and our cross-node stdout lock
    # live alongside it.
    rundirs = prepare_run_dirs(project)
    ensure_snakemake_symlink(project, rundirs.snakemake_state)
    if verbose:
        console.print(f"[dim]Scratch root:[/dim] {resolve_scratch_root(project)}")

    choice = load_runtime(project_path=project)
    _ensure_images(project, runtime=choice.runtime)
    # On a kubernetes-runtime site the worker pod is the container:
    # resolve which image the Gateway cluster must run. On a hub this
    # ensures the image is up to date first (committing/pushing env
    # changes and driving a BinderHub build when needed — a fast no-op
    # round-trip when the registry already has it); the run-scoped
    # cluster is then created with exactly that image.
    expected_worker_image = (
        _worker_image_for_run(project, verbose=verbose)
        if choice.runtime == KUBERNETES_RUNTIME
        else None
    )
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
        declared = sorted(
            {
                entry["container_image"]
                for rule_entries in cfg_data.values()
                for entry in rule_entries.values()
                if entry.get("container_image")
            }
        )
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

    n = str(jobs or os.cpu_count() or 1)
    # Snakemake requires ``--cores`` to bound per-rule CPU; the dask
    # plugin requires ``--jobs`` to bound parallel dispatch. We surface
    # one knob and pass it as both.
    cmd = _build_snakemake_cmd(
        snakefile_path=snakefile_path,
        project=project,
        n=n,
        rerun_triggers=rerun_triggers,
        targets=targets,
        force=force,
        has_outputs=bool(outputs),
        gateway=gateway_branch_active(),
    )

    # Hold a project-level flock for the duration of the run. Acquiring
    # it also clears any stale snakemake lock left by a previously
    # crashed invocation — safe because we just proved we're alone on
    # the project. Concurrent ``lc run`` on the same project bails
    # cleanly rather than corrupting Snakemake state.
    try:
        run_lock_cm = acquire_run_lock(rundirs)
        run_lock_cm.__enter__()
    except RunLockBusyError as e:
        raise click.ClickException(str(e))

    try:
        with cluster_for_run(
            verbose=verbose,
            local_directory=str(rundirs.dask_local),
            expected_worker_image=expected_worker_image,
            max_workers=int(n),
        ) as cluster_env:
            _warn_runtime_cluster_mismatch(
                project, runtime=choice.runtime, cluster_env=cluster_env
            )
            env = {
                **os.environ,
                # How the child snakemake's executor reaches the cluster:
                # DASK_SCHEDULER_ADDRESS for address-dialled clusters, or
                # LIGHTCONE_GATEWAY_CLUSTER for Dask Gateway (rejoined by
                # name through the Gateway API).
                **cluster_env,
                # The dask plugin's worker-side ``_run_shell`` takes this
                # ``flock`` before forwarding a rule's lightcone output, so
                # parallel rules' blocks never interleave at the line level
                # — even across nodes. The lockfile sits under our scratch
                # root specifically to avoid DVS on NERSC.
                "LIGHTCONE_OUT_LOCK": str(rundirs.lock_path),
            }
            # Exactly one of the two cluster variables may reach the
            # child: a stale LIGHTCONE_GATEWAY_CLUSTER lingering in the
            # user's shell (we tell them to export it) must not redirect
            # the child to a Gateway cluster the parent never verified,
            # and vice versa.
            if "DASK_SCHEDULER_ADDRESS" in cluster_env:
                env.pop(GATEWAY_CLUSTER_ENV, None)
            elif GATEWAY_CLUSTER_ENV in cluster_env:
                env.pop("DASK_SCHEDULER_ADDRESS", None)
            if verbose:
                console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
                sys.exit(subprocess.run(cmd, env=env).returncode)
            sys.exit(_run_silent(cmd, env=env, scratch_root=rundirs.root))
    except RuntimeError as e:
        # Cluster bootstrap errors are user guidance ("no Gateway cluster
        # running — create one like this…", "workers lack the resource
        # contract"), not stack traces. SystemExit from the run itself
        # passes through untouched.
        raise click.ClickException(str(e))


def _run_silent(
    cmd: list[str],
    *,
    env: dict[str, str],
    scratch_root: Path,
) -> int:
    """Run snakemake with its own output suppressed.

    All user-facing output for the run flows through dask workers
    (``_run_shell`` in the executor plugin → terminal stdout under
    ``flock``). The parent snakemake process here only emits its own
    bootstrap chatter (DAG building, rule selection, "Workflow finished")
    plus, on failure, a workflow-level diagnostic. We discard stdout
    wholesale and tail stderr into a bounded ring buffer so a workflow
    crash leaves a real log behind without that log being visible
    during a successful run.
    """
    from collections import deque

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stderr is not None
    tail: deque[str] = deque(maxlen=400)
    for line in proc.stderr:
        tail.append(line)
    rc = proc.wait()
    if rc != 0:
        log = scratch_root / f"snakemake-stderr-{os.getpid()}.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("".join(tail))
            console.print(
                f"\n[red]✗ Workflow failed.[/red] "
                f"Last snakemake stderr saved to [cyan]{log}[/cyan]."
            )
        except OSError:
            # Last-ditch: dump to stderr if scratch is unwritable.
            sys.stderr.write("".join(tail))
    return rc


def _build_snakemake_cmd(
    *,
    snakefile_path: Path,
    project: Path,
    n: str,
    rerun_triggers: str,
    targets: list[str],
    force: bool,
    has_outputs: bool,
    gateway: bool = False,
) -> list[str]:
    """Build the snakemake argv list for ``lc run``.

    ``--rerun-triggers`` uses ``nargs=+`` in snakemake's argparse, so without
    an explicit ``--`` separator it greedily consumes the first positional
    target path as an extra trigger value, causing an "invalid choice" error.

    *gateway* scopes ``--shared-fs-usage``: Gateway worker pods share
    only the project volume with the driver, not its HOME or install
    prefix. Snakemake's default (everything shared) makes the child
    snakemake use driver-local paths — the driver's source-cache under
    ``~/.cache`` (unwritable/absent in the worker pod) and the driver's
    ``sys.executable`` (a conda path a slim worker image doesn't have).
    Declaring what is actually shared keeps the child on worker-local
    equivalents. Local and SLURM runs keep the default: there the
    workers genuinely share the driver's environment.
    """
    cmd: list[str] = [
        # lc's own interpreter, not a PATH lookup: an activated project
        # venv (or any stray env) earlier on PATH would otherwise supply
        # a different snakemake than the lightcone-cli driving it —
        # version skew that surfaces as executor errors three layers
        # from the cause. The worker side is unaffected (the executor
        # resolves the child interpreter per-branch).
        sys.executable,
        "-m",
        "snakemake",
        "-s",
        str(snakefile_path),
        "-d",
        str(project),
        "--cores",
        n,
        "--jobs",
        n,
        "--executor",
        "dask",
        "--rerun-triggers",
        *rerun_triggers.split(","),
    ]
    if gateway:
        cmd += [
            "--shared-fs-usage",
            "input-output",
            "persistence",
            "sources",
            "storage-local-copies",
            # Driver and workers see the project through NFS (the hub's
            # RWX volume); the client-side attribute cache can hide a
            # worker's freshly written outputs from the driver for tens
            # of seconds. Snakemake's default 5s declares the rule
            # failed ("output missing") even though it succeeded.
            "--latency-wait",
            "60",
        ]
    if force:
        # ``--force`` scopes to explicit targets; ``rule all`` itself
        # has no recipe, so force-all is the only useful sense when no
        # targets were named.
        cmd.append("--force" if has_outputs else "--forceall")
    if targets:
        cmd.append("--")
    cmd.extend(targets)
    return cmd


def _target_for(project: Path, output_id: str, universe: str) -> str:
    """Translate an output id into a Snakemake target path (the manifest).

    Accepts either a bare ``output_id`` (root-level or unique sub-analysis
    output) or a qualified ``analysis_id.output_id`` to disambiguate when
    the same id appears in multiple sub-analyses.
    """
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.manifest import MANIFEST_FILENAME
    from lightcone.engine.tree import collect_tree_outputs, resolve_output_path

    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    matches = []
    for to in collect_tree_outputs(spec):
        if to.output_def.get("recipe") is None:
            continue
        qualified = (
            f"{to.analysis_id}.{to.output_id}" if to.analysis_id else to.output_id
        )
        if qualified == output_id or to.output_id == output_id:
            matches.append((qualified, to))

    if not matches:
        raise click.ClickException(
            f"Output '{output_id}' not found in astra.yaml or has no recipe."
        )
    if len(matches) > 1:
        opts = ", ".join(q for q, _ in matches)
        raise click.ClickException(
            f"Output '{output_id}' is ambiguous; qualify it as one of: {opts}"
        )

    _, to = matches[0]
    target = (
        resolve_output_path(project, to, universe) / to.output_id / MANIFEST_FILENAME
    )
    return str(target.relative_to(project))


# =============================================================================
# lc status
# =============================================================================


@main.command()
@click.option("--universe", "-u", default=None)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of a styled table.",
)
def status(universe: str | None, as_json: bool) -> None:
    """Report materialization status for every declared output."""
    from lightcone.engine.snakefile import discover_universes
    from lightcone.engine.status import get_output_status

    project = _project_root()
    universes = [universe] if universe else discover_universes(project)

    if as_json:
        payload = {
            "universes": [
                {
                    "universe_id": u,
                    "outputs": [
                        {
                            "output_id": s.output_id,
                            "analysis_id": s.analysis_id,
                            "status": s.status,
                            "recipe_command": s.recipe_command,
                        }
                        for s in get_output_status(project, universe_id=u)
                    ],
                }
                for u in universes
            ],
        }
        click.echo(json.dumps(payload, indent=2))
        return

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
    help=(
        "docker | podman | podman-hpc | kubernetes "
        "(overrides ~/.lightcone/config.yaml)"
    ),
)
@click.option(
    "--no-commit",
    is_flag=True,
    help=(
        "On a hub: fail instead of auto-committing environment-file "
        "changes before the image build"
    ),
)
def build(force: bool, runtime: str | None, no_commit: bool) -> None:
    """Build container images declared in astra.yaml.

    Containerfile syntax is Dockerfile syntax — we use ``docker``,
    ``podman``, or ``podman-hpc`` directly. Each Containerfile builds to
    an OCI image tagged ``lc-<project>-<hash>`` in the runtime's local
    image store. Pre-built registry images (``python:3.12-slim``,
    ``ghcr.io/foo/bar:tag``) are skipped — the runtime pulls them at
    ``lc run`` time.

    On a kubernetes-runtime site (a lightcone-hub deployment) there is
    no docker in-pod: ``lc build`` instead commits any environment-file
    changes, pushes them, and drives an image build through the hub's
    BinderHub service into the deployment registry — the image a
    Gateway cluster will run. Without a reachable build service it
    falls back to probing the registry and printing how to publish the
    image off-hub.
    """
    from lightcone.engine.container import (
        KUBERNETES_RUNTIME,
        ContainerBuildError,
        load_runtime,
    )

    project = _project_root()
    try:
        resolved_runtime = runtime or load_runtime(project_path=project).runtime
    except ContainerBuildError as e:
        raise click.ClickException(str(e))

    if resolved_runtime == KUBERNETES_RUNTIME:
        from lightcone.engine.binder import binder_available

        if binder_available():
            _build_via_hub(project, commit=not no_commit)
        else:
            _report_registry_images(project)
        return

    if resolved_runtime == "none":
        console.print(
            "[yellow]No container runtime available "
            "(checked docker, podman, podman-hpc). "
            "Install one to build images, or set [cyan]container.runtime[/cyan] "
            "in [cyan]~/.lightcone/config.yaml[/cyan].[/yellow]"
        )
        return

    _ensure_images(project, runtime=resolved_runtime, force=force)
    console.print("[green]Done.[/green]")


def _container_specs(project: Path) -> tuple[str, list[str]]:
    """``(project_name, distinct container specs)`` from astra.yaml.

    Specs are returned in tree order, first occurrence wins — the same
    walk ``_ensure_images`` has always done, factored out so the
    kubernetes-runtime paths (`lc build` registry report, expected
    worker image) resolve the identical set.
    """
    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.tree import collect_tree_outputs

    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    project_name = (spec.get("name") or project.name).lower().replace(" ", "-")

    specs: list[str] = []
    for to in collect_tree_outputs(spec):
        recipe = to.output_def.get("recipe") or {}
        spec_str = (
            recipe.get("container")
            or to.analysis_spec.get("container")
            or spec.get("container")
        )
        if spec_str and spec_str not in specs:
            specs.append(spec_str)
    return project_name, specs


def _warn_runtime_cluster_mismatch(
    project: Path, *, runtime: str, cluster_env: dict[str, str]
) -> None:
    """Warn when the container runtime and the cluster branch disagree.

    The two are resolved from independent signals (site declaration /
    user config vs. ambient cluster env), so misconfiguration can pair
    them wrongly in both directions:

    * ``kubernetes`` runtime off-Gateway — recipes run wherever the
      cluster puts them with no pod image lc can verify, while manifests
      still record the declared ``container_image`` (provenance hazard,
      e.g. ``runtime: kubernetes`` copied into a laptop config).
    * An OCI runtime on a Gateway cluster — recipes arrive wrapped in
      ``docker run``/``podman run`` inside worker pods that have no such
      binary, so every containerized rule fails (e.g.
      ``DASK_GATEWAY__ADDRESS`` set on a workstation that has docker).

    Warnings, not errors: the pairing can be intentional (a k8s-native
    external scheduler; a gateway whose workers do ship podman).
    """
    from lightcone.engine.container import KUBERNETES_RUNTIME, RUNTIMES
    from lightcone.engine.dask_cluster import GATEWAY_CLUSTER_ENV

    on_gateway = GATEWAY_CLUSTER_ENV in cluster_env
    if runtime == KUBERNETES_RUNTIME and not on_gateway:
        console.print(
            "[yellow]⚠ Container runtime is [cyan]kubernetes[/cyan] but "
            "this run is not attached through Dask Gateway.[/yellow]\n"
            "  Recipes will execute unwrapped and lc cannot verify they "
            "run in the declared container\n"
            "  image — manifests may record provenance that does not "
            "match what executed. If this\n"
            "  machine is not a lightcone-hub deployment, remove "
            "[cyan]container: {runtime: kubernetes}[/cyan]\n"
            "  from [cyan]~/.lightcone/config.yaml[/cyan]."
        )
    elif runtime in RUNTIMES and on_gateway:
        _, specs = _container_specs(project)
        if specs:
            console.print(
                f"[yellow]⚠ Recipes are wrapped for [cyan]{runtime}"
                f"[/cyan], but they will execute inside Dask Gateway "
                "worker pods,[/yellow]\n"
                f"  which typically do not provide {runtime} — "
                "containerized rules will fail. On a\n"
                "  Kubernetes-backed gateway the pod is the container: "
                "set [cyan]container: {runtime: kubernetes}[/cyan]\n"
                "  in [cyan]~/.lightcone/config.yaml[/cyan] and give the "
                "cluster the project's image instead."
            )


def _expected_worker_image(project: Path) -> str | None:
    """The image the project's Gateway cluster is expected to run.

    Exactly one distinct declared container → its pullable realization
    (deployment-registry ref for a Containerfile, the spec itself for a
    registry image). Zero → ``None``. Several distinct images → ``None``
    with a warning: a Gateway cluster runs a single worker image, so
    heterogeneous per-rule containers cannot be realized on this path.
    A Containerfile that cannot be resolved (no ``LIGHTCONE_REGISTRY``)
    also yields ``None`` with a warning — silently dropping it would
    corrupt the single-vs-multiple decision and disable verification
    without saying so.
    """
    from lightcone.engine.container import is_containerfile, resolve_worker_image

    project_name, specs = _container_specs(project)
    images: list[str] = []
    unresolved: list[str] = []
    for spec_str in specs:
        image = resolve_worker_image(
            spec_str, project_path=project, project_name=project_name
        )
        if image is None and is_containerfile(spec_str, project):
            unresolved.append(spec_str)
        if image and image not in images:
            images.append(image)
    if unresolved:
        console.print(
            "[yellow]⚠ Cannot resolve "
            + ", ".join(f"[cyan]{s}[/cyan]" for s in unresolved)
            + " to a registry image: LIGHTCONE_REGISTRY is not set.[/yellow]\n"
            "  Worker-image verification is disabled for this run. Ask "
            "the hub admin to inject\n"
            "  [cyan]LIGHTCONE_REGISTRY[/cyan] (see lightcone-hub helm "
            "values)."
        )
        return None
    if len(images) > 1:
        console.print(
            "[yellow]⚠ astra.yaml declares multiple distinct container "
            "images, but a Dask Gateway cluster runs a single worker "
            "image:[/yellow]\n"
            + "\n".join(f"    [dim]•[/dim] {i}" for i in images)
            + "\n  All recipes will execute in whatever image the attached "
            "cluster runs.\n"
            "  Consolidate on one Containerfile to restore per-recipe "
            "provenance on this deployment."
        )
        return None
    return images[0] if images else None


def _binder_progress(verbose: bool) -> Callable[[str, str], None]:
    """Progress callback for hub image builds.

    Git steps (commit/push) always print — they mutate the user's repo
    and must never be silent. Build phases print on transition; the
    per-line repo2docker build log only with ``--verbose``.
    """
    state = {"phase": ""}

    def cb(phase: str, message: str) -> None:
        if phase in ("commit", "push"):
            console.print(f"  [dim]{message}[/dim]")
            return
        if phase and phase != state["phase"]:
            state["phase"] = phase
            console.print(f"  [dim]binder: {phase}[/dim]")
        if verbose and message:
            console.print(f"  [dim]{message}[/dim]")

    return cb


def _worker_image_for_run(project: Path, *, verbose: bool) -> str | None:
    """The worker image a kubernetes-runtime run should use.

    On a hub (BinderHub service reachable) a declared Containerfile is
    *ensured*: environment changes are committed and pushed, and the
    image is built through the hub's build service when the registry
    doesn't have it yet — so ``lc run`` always starts its cluster on an
    up-to-date image. Registry-image specs pass through unchanged.
    Elsewhere, falls back to the passive registry resolution of
    :func:`_expected_worker_image` (verification-only).
    """
    from lightcone.engine.binder import (
        BinderBuildError,
        binder_available,
        ensure_worker_image,
    )
    from lightcone.engine.container import is_containerfile

    if not binder_available():
        return _expected_worker_image(project)

    _, specs = _container_specs(project)
    if not specs:
        # No container declared: the cluster runs the deployment's
        # default worker image, which ships the lightcone stack.
        return None
    if len(specs) > 1:
        console.print(
            "[yellow]⚠ astra.yaml declares multiple distinct containers, "
            "but a Dask Gateway cluster runs a single worker image:"
            "[/yellow]\n"
            + "\n".join(f"    [dim]•[/dim] {s}" for s in specs)
            + "\n  Falling back to the deployment's default worker image. "
            "Consolidate on one\n"
            "  Containerfile to run recipes in the project environment."
        )
        return None
    spec = specs[0]
    if not is_containerfile(spec, project):
        return spec
    console.print(
        f"[cyan]Ensuring worker image[/cyan] for [cyan]{spec}[/cyan] "
        "[dim](hub build service)[/dim]"
    )
    try:
        image = ensure_worker_image(
            project, spec, on_progress=_binder_progress(verbose)
        )
    except BinderBuildError as e:
        raise click.ClickException(str(e))
    console.print(f"[green]✓[/green] Worker image: [cyan]{image}[/cyan]")
    return image


def _build_via_hub(project: Path, *, commit: bool) -> None:
    """``lc build`` on a hub: publish every declared Containerfile.

    Drives the BinderHub service (build + push into the deployment
    registry) for each declared Containerfile — the explicit,
    verbose-by-default form of the ensure step ``lc run`` performs
    implicitly. Registry-image specs need no build and are reported
    as-is.
    """
    from lightcone.engine.binder import BinderBuildError, ensure_worker_image
    from lightcone.engine.container import is_containerfile

    _, specs = _container_specs(project)
    if not specs:
        console.print("No containers declared in astra.yaml — nothing to build.")
        return
    for spec in specs:
        if not is_containerfile(spec, project):
            console.print(
                f"[green]•[/green] {spec} — registry image; the Gateway "
                "cluster pulls it directly (must ship the lightcone "
                "worker stack)."
            )
            continue
        console.print(f"[cyan]Building[/cyan] {spec} [dim](hub build service)[/dim]")
        try:
            image = ensure_worker_image(
                project,
                spec,
                commit=commit,
                on_progress=_binder_progress(verbose=True),
            )
        except BinderBuildError as e:
            raise click.ClickException(str(e))
        console.print(
            f"[green]✓[/green] {spec} → [cyan]{image}[/cyan] "
            "[dim](in the deployment registry; `lc run` will start its "
            "cluster with it)[/dim]"
        )


def _report_registry_images(project: Path) -> None:
    """``lc build`` on a kubernetes-runtime site.

    The worker pod is the container, so images must exist in the
    deployment registry (``LIGHTCONE_REGISTRY``) for a Gateway cluster
    to pull — there is no docker/podman in-pod to build with. Probe the
    registry best-effort and print the exact publish commands when an
    image is missing. Purely informational: never fails the command,
    because the authoritative check happens when the user's cluster
    starts (Kubernetes pulls the image or says why not).
    """
    from lightcone.engine.container import (
        compute_image_tag,
        deployment_registry,
        is_containerfile,
        registry_image_exists,
        registry_image_ref,
    )

    project_name, specs = _container_specs(project)
    if not specs:
        console.print("No containers declared in astra.yaml — nothing to check.")
        return

    console.print(
        "[dim]This deployment runs recipes inside Dask Gateway worker "
        "pods; images are pulled from the deployment registry, not built "
        "here.[/dim]"
    )
    registry = deployment_registry()
    for spec_str in specs:
        if not is_containerfile(spec_str, project):
            console.print(
                f"[green]•[/green] {spec_str} — registry image; use it as "
                "the cluster's worker image.\n"
                "  [dim]Note: a Gateway worker image must contain dask, "
                "distributed, dask-gateway, and\n"
                "  lightcone-cli at hub-matching versions — plain images "
                "like python:*-slim will not\n"
                "  start as workers. Base project images on the "
                "deployment's lightcone-worker-default.[/dim]"
            )
            continue
        if registry is None:
            console.print(
                f"[yellow]•[/yellow] {spec_str} — cannot resolve: "
                "LIGHTCONE_REGISTRY is not set on this deployment. "
                "Ask the hub admin to inject it (see lightcone-hub "
                "helm values)."
            )
            continue
        tag = compute_image_tag(project_name, project / spec_str, project)
        ref = registry_image_ref(tag, registry=registry)
        exists = registry_image_exists(ref) if ref else None
        if exists is True:
            console.print(f"[green]✓[/green] {spec_str} → {ref} [dim](in registry)[/dim]")
            continue
        state = (
            "[red]missing from the registry[/red]"
            if exists is False
            else "[yellow]could not be verified[/yellow]"
        )
        # Instruct `lc build` (not a raw `docker build .`): the tag hash
        # attests the *staged* build context (Containerfile + deps +
        # COPY sources, with results//.git/venvs excluded). A raw docker
        # build from the project root would bake in whatever else lives
        # there and push a different artifact under the attested name.
        console.print(
            f"[yellow]•[/yellow] {spec_str} → {ref} — {state}.\n"
            "  Publish it from a clone of this project on any machine "
            "with docker and registry access:\n"
            f"    [cyan]lc build[/cyan]   [dim]# builds {tag} from the "
            "hash-attested build context[/dim]\n"
            f"    [cyan]docker tag {tag} {ref}[/cyan]\n"
            f"    [cyan]docker push {ref}[/cyan]\n"
            "  then create your Gateway cluster with "
            f'[cyan]image="{ref}"[/cyan].'
        )


def _ensure_images(project: Path, *, runtime: str, force: bool = False) -> None:
    """Build/pull every container image referenced in astra.yaml.

    No-op for non-wrapping runtimes: ``none`` uses no images, and
    ``kubernetes`` has no local image store — images live in the
    deployment registry (see ``_report_registry_images``). Idempotent:
    skips images already present in the runtime's local image store.
    Used by ``lc build`` (with ``--force`` exposed) and as a pre-flight
    by ``lc run`` so the first invocation after editing a Containerfile
    doesn't fail mid-DAG with a missing image.
    """
    from lightcone.engine.container import KUBERNETES_RUNTIME

    if runtime in ("none", KUBERNETES_RUNTIME):
        return

    from lightcone.engine.container import (
        ContainerBuildError,
        build_image,
        compute_image_tag,
        image_exists_locally,
        is_containerfile,
        pull_image,
    )

    project_name, specs = _container_specs(project)
    for spec_str in specs:
        if not is_containerfile(spec_str, project):
            # Registry image — pull so ``lc run`` can use ``--pull=never``
            # without depending on the runtime's registry resolution.
            if image_exists_locally(spec_str, runtime=runtime) and not force:
                continue
            console.print(f"[cyan]Pulling[/cyan] {spec_str} [dim](via {runtime})[/dim]")
            try:
                pull_image(spec_str, runtime=runtime)
            except ContainerBuildError as e:
                raise click.ClickException(str(e))
            continue

        containerfile = project / spec_str
        tag = compute_image_tag(project_name, containerfile, project)
        if image_exists_locally(tag, runtime=runtime) and not force:
            continue
        console.print(
            f"[cyan]Building[/cyan] {spec_str} → {tag} [dim](via {runtime})[/dim]"
        )
        try:
            build_image(tag, containerfile, project, runtime=runtime)
        except ContainerBuildError as e:
            raise click.ClickException(str(e))


# =============================================================================
# lc export
# =============================================================================


@main.group()
def export() -> None:
    """Export project artifacts in interoperable formats."""


@export.command("wrroc")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path("./wrroc"),
    help="Bundle directory (or .zip path with --zip).",
    show_default=True,
)
@click.option(
    "--universe",
    "-u",
    multiple=True,
    help="Restrict to specific universes (default: all).",
)
@click.option(
    "--author",
    default=None,
    help='Author override, e.g. "Name <email@host>". Default: git config.',
)
@click.option(
    "--license",
    "license_url",
    default=None,
    help="License URL or SPDX identifier. Default: CC-BY-4.0.",
)
@click.option(
    "--zip/--no-zip",
    "zip_bundle",
    default=False,
    help="Package the bundle as a .zip after building.",
)
@click.option(
    "--metadata-only",
    is_flag=True,
    help="Skip data files; bundle manifests + astra.yaml + universes only.",
)
def export_wrroc_cmd(
    output: Path,
    universe: tuple[str, ...],
    author: str | None,
    license_url: str | None,
    zip_bundle: bool,
    metadata_only: bool,
) -> None:
    """Export a Workflow Run RO-Crate (WRROC) bundle.

    The bundle is suitable for upload to WorkflowHub, Zenodo (with the
    RO-Crate plugin), or any RO-Crate-aware archive. The lightcone
    manifest format on disk is unchanged — this is a publication view
    generated on demand.

    Examples:

      lc export wrroc                                 # ./wrroc/ directory
      lc export wrroc -o my-run.zip --zip             # zip bundle
      lc export wrroc --metadata-only                 # provenance, no data
      lc export wrroc -u baseline -u alt              # specific universes
    """
    from lightcone.engine.wrroc import export_wrroc

    project = _project_root()

    try:
        result = export_wrroc(
            project_path=project,
            output_path=output,
            universes=list(universe) or None,
            author=author,
            license=license_url,
            zip_bundle=zip_bundle,
            include_data=not metadata_only,
        )
    except FileExistsError as e:
        raise click.ClickException(str(e))

    flavor = "zip bundle" if result.is_zip else "directory"
    console.print(
        f"[green]✓[/green] Wrote WRROC {flavor}: [cyan]{result.bundle_path}[/cyan]"
    )
    if result.runs_included == 0:
        console.print(
            "[yellow]Warning:[/yellow] no materialized outputs were found — "
            "the bundle contains only the workflow definition.\n"
            "  This usually means recipes haven't been run yet (try [cyan]lc run[/cyan]) "
            "or the [cyan].lightcone-manifest.json[/cyan] sidecars are missing.\n"
            "  Workflow-only bundles will not pass strict Provenance Run Crate "
            "validation; that profile requires at least one materialized run."
        )
    else:
        u_list = ", ".join(result.universes_included)
        console.print(
            f"  Captured [bold]{result.runs_included}[/bold] runs across "
            f"universes: [cyan]{u_list}[/cyan]"
        )


# Register eval subgroup (requires optional 'eval' extra)
try:
    from lightcone.eval.cli import eval_group

    main.add_command(eval_group, "eval")
except ImportError:
    pass
