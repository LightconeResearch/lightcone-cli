"""Command-line interface for lightcone-cli — the ASTRA execution layer.

This is a clean rebuild against the normative design spec, re-added layer
by layer.

**Layer 1 — project scaffolding.** The only verb is ``lc init``. What a
project *is* and how one is converged lives in
:mod:`lightcone.engine.project`; the file templates live in
:mod:`lightcone.engine.templates`. This module owns only the CLI: flags,
console rendering, and exit codes.

The remaining verbs — ``lc materialize``, ``lc run``, ``lc status``,
``lc verify``, ``lc build`` — arrive with their layers.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from lightcone.engine.project import ConvergenceReport, ProjectError, converge

console = Console()
logger = logging.getLogger(__name__)


class _EngineErrorGroup(click.Group):
    """Render engine errors as clean CLI errors instead of tracebacks.

    The engine raises its own exception types from many entry points.
    Translating once at the group boundary keeps every command, present
    and future, from leaking a raw traceback; click prints
    ``ClickException`` messages cleanly and exits 1.
    """

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except ProjectError as e:
            raise click.ClickException(str(e)) from e


@click.group(cls=_EngineErrorGroup)
@click.version_option(package_name="lightcone-cli")
@click.pass_context
def main(ctx: click.Context) -> None:
    """lightcone-cli — execution layer for ASTRA projects."""
    ctx.ensure_object(dict)


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
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path), default=".")
@click.option("--no-git", is_flag=True, help="Skip git init")
@click.option(
    "--no-sync",
    is_flag=True,
    help="Skip materializing the .venv (uv lock still runs).",
)
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help=(
        "Report what would be created or repaired without writing anything; "
        "exit 1 if the project is not converged."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the convergence report as JSON on stdout.",
)
def init(
    directory: Path,
    no_git: bool,
    no_sync: bool,
    check_only: bool,
    as_json: bool,
) -> None:
    """Converge DIRECTORY into an ASTRA project (idempotent).

    Safe to re-run at any time: creates whatever is missing, repairs the
    pieces lightcone manages, and never overwrites files you own. A
    directory that already holds an ``astra.yaml`` is adopted, not
    rejected.

    The spec scaffold (``astra.yaml``, ``universes/baseline.yaml``)
    follows the ``astra init`` boilerplate; on top of it sit the uv
    project (``pyproject.toml`` with lightcone-cli locked in,
    ``.python-version``, ``uv.lock``, ``.venv``), ``.gitignore`` entries,
    ``results/``, and a template MyST report (``myst.yml`` +
    ``index.md``).
    """
    directory = directory.resolve()
    write = not check_only

    if write and not as_json:
        console.print(f"[cyan]{_LIGHTCONE}[/cyan]")

    report = converge(directory, write=write, git=not no_git, sync=not no_sync)

    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
    elif check_only:
        _render_check(report, directory)
    else:
        _render_run(report, directory)

    if check_only and not report.converged:
        sys.exit(1)


def _render_check(report: ConvergenceReport, directory: Path) -> None:
    if report.converged:
        console.print(f"[green]✓[/green] {directory} is converged — nothing to do")
    else:
        for item in report.created:
            console.print(f"  [yellow]would create[/yellow] {item}")
        for item in report.repaired:
            console.print(f"  [yellow]would repair[/yellow] {item}")
    for warning in report.warnings:
        console.print(f"  [yellow]![/yellow] {warning}")


def _render_run(report: ConvergenceReport, directory: Path) -> None:
    for item in report.created:
        console.print(f"[green]✓[/green] created {item}")
    for item in report.repaired:
        console.print(f"[green]✓[/green] repaired {item}")
    for warning in report.warnings:
        console.print(f"[yellow]![/yellow] {warning}")
    if report.converged:
        console.print(f"\n[green]Project already converged at[/green] {directory}")
    else:
        console.print(f"\n[green]Project converged at[/green] {directory}")

    # Next steps only make sense for a freshly scaffolded spec.
    if "astra.yaml" in report.created:
        console.print("\nNext steps:")
        console.print(f"  • Go to the newly created directory [cyan]cd {directory}[/cyan]")
        console.print(
            "  • Add analysis dependencies with [cyan]uv add[/cyan] "
            "(e.g. [cyan]uv add numpy astropy[/cyan])"
        )
        console.print("  • Describe your analysis in [cyan]astra.yaml[/cyan]")
        console.print(
            "  • Preview the report with [cyan]myst start[/cyan] "
            "(requires the MyST CLI: [cyan]npm i -g mystmd[/cyan])"
        )
