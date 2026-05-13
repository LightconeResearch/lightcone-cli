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
    permissions: str,
    scratch_override: str | None,
) -> None:
    """Scaffold a new ASTRA project with Claude Code integration.

    Delegates the spec scaffold (``astra.yaml``, ``universes/baseline.yaml``,
    base ``.gitignore``, ``src/``) to ``astra init``, then layers on the
    lightcone-specific bits: ``Containerfile`` + ``requirements.txt``,
    ``.lightcone/`` project state, ``.claude/`` plugin bundle, ``CLAUDE.md``,
    and an optional Python venv.
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
        raise click.ClickException(
            f"astra init failed (exit code {e.code})."
        ) from e

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

    # git init last so the initial commit captures every scaffolded file.
    no_git = no_git or (directory / ".git").exists()
    if not no_git:
        subprocess.run(["git", "init", "-q"], cwd=directory, check=False)
        console.print("[green]✓[/green] Initialized git repository")

    # venv
    if not no_venv:
        if shutil.which("uv"):
            with console.status("[dim]Creating virtual environment…[/dim]"):
                subprocess.run(["uv", "venv", "--python", "3.12", ".venv"], cwd=directory, check=False, capture_output=True)
            with console.status("[dim]Installing lightcone-cli…[/dim]"):
                subprocess.run(
                    ["uv", "pip", "install", "--python", ".venv/bin/python", "lightcone-cli"],
                    cwd=directory,
                    check=False,
                    capture_output=True,
                )
        else:
            with console.status("[dim]Creating virtual environment…[/dim]"):
                subprocess.run(["python", "-m", "venv", ".venv"], cwd=directory, check=False, capture_output=True)
            with console.status("[dim]Installing lightcone-cli…[/dim]"):
                subprocess.run(
                    [".venv/bin/python", "-m", "pip", "install", "-q", "lightcone-cli"],
                    cwd=directory,
                    check=False,
                    capture_output=True,
                )
        console.print(f"[green]✓[/green] Virtual environment created in [cyan]{directory}/.venv[/cyan]")    

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
    console.print("  • Run [cyan]/lc-new[/cyan] to get started on a new analysis")


_CONTAINERFILE = """\
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv pip install -r requirements.txt

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
    workstation, srun-launched workers inside a SLURM allocation, or an
    existing scheduler if ``DASK_SCHEDULER_ADDRESS`` is set.
    """
    _abort_on_perlmutter_login()

    from lightcone.engine.container import load_runtime
    from lightcone.engine.dask_cluster import cluster_for_run
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
        console.print(
            f"[dim]Scratch root:[/dim] {resolve_scratch_root(project)}"
        )

    choice = load_runtime(project_path=project)
    _ensure_images(project, runtime=choice.runtime)
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

    with cluster_for_run(
        verbose=verbose, local_directory=str(rundirs.dask_local)
    ) as scheduler_addr:
        env = {
            **os.environ,
            "DASK_SCHEDULER_ADDRESS": scheduler_addr,
            # The dask plugin's worker-side ``_run_shell`` takes this
            # ``flock`` before forwarding a rule's lightcone output, so
            # parallel rules' blocks never interleave at the line level
            # — even across nodes. The lockfile sits under our scratch
            # root specifically to avoid DVS on NERSC.
            "LIGHTCONE_OUT_LOCK": str(rundirs.lock_path),
        }
        if verbose:
            console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
            sys.exit(subprocess.run(cmd, env=env).returncode)
        sys.exit(_run_silent(cmd, env=env, scratch_root=rundirs.root))


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
) -> list[str]:
    """Build the snakemake argv list for ``lc run``.

    ``--rerun-triggers`` uses ``nargs=+`` in snakemake's argparse, so without
    an explicit ``--`` separator it greedily consumes the first positional
    target path as an extra trigger value, causing an "invalid choice" error.
    """
    cmd: list[str] = [
        "snakemake",
        "-s", str(snakefile_path),
        "-d", str(project),
        "--cores", n,
        "--jobs", n,
        "--executor", "dask",
        "--rerun-triggers", *rerun_triggers.split(","),
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
    target = resolve_output_path(project, to, universe) / to.output_id / MANIFEST_FILENAME
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
    from lightcone.engine.container import ContainerBuildError, load_runtime

    project = _project_root()
    try:
        resolved_runtime = runtime or load_runtime(project_path=project).runtime
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

    _ensure_images(project, runtime=resolved_runtime, force=force)
    console.print("[green]Done.[/green]")


def _ensure_images(project: Path, *, runtime: str, force: bool = False) -> None:
    """Build/pull every container image referenced in astra.yaml.

    No-op when *runtime* is ``"none"``. Idempotent: skips images already
    present in the runtime's local image store. Used by ``lc build`` (with
    ``--force`` exposed) and as a pre-flight by ``lc run`` so the first
    invocation after editing a Containerfile doesn't fail mid-DAG with a
    missing image.
    """
    if runtime == "none":
        return

    from astra.helpers import load_yaml, resolve_analysis_tree

    from lightcone.engine.container import (
        ContainerBuildError,
        build_image,
        compute_image_tag,
        image_exists_locally,
        is_containerfile,
        pull_image,
    )
    from lightcone.engine.tree import collect_tree_outputs

    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
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
            if image_exists_locally(spec_str, runtime=runtime) and not force:
                continue
            console.print(
                f"[cyan]Pulling[/cyan] {spec_str} [dim](via {runtime})[/dim]"
            )
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
    help="Author override, e.g. \"Name <email@host>\". Default: git config.",
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
