"""Command-line interface for lightcone-cli — the ASTRA execution layer.
"""

from __future__ import annotations

import functools
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from rich.console import Console

    from lightcone.engine.project import ConvergenceReport

logger = logging.getLogger(__name__)


@functools.cache
def _console() -> Console:
    """The rich console, built on first use to avoid startup cost at each
    invocation of the cli even when the console is not needed."""
    from rich.console import Console

    return Console()


class _EngineErrorGroup(click.Group):
    """Render engine errors as clean CLI errors instead of tracebacks.

    The engine raises its own exception types from many entry points.
    Translating once at the group boundary keeps every command, present
    and future, from leaking a raw traceback; click prints
    ``ClickException`` messages cleanly and exits 1.
    """

    def invoke(self, ctx: click.Context) -> object:
        from lightcone.engine.project import ProjectError

        try:
            return super().invoke(ctx)
        except ProjectError as e:
            raise click.ClickException(str(e)) from e


@click.group(cls=_EngineErrorGroup)
@click.version_option(package_name="lightcone-cli")
def main() -> None:
    """lightcone-cli — execution layer for ASTRA projects."""


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
def init(directory: Path, check_only: bool, as_json: bool) -> None:
    """Converge DIRECTORY into a standard Lightcone project (idempotent).

    Safe to re-run at any time: creates whatever is missing, repairs the
    pieces lightcone manages, and never overwrites files you own.

    The spec scaffold (``astra.yaml``, ``universes/baseline.yaml``)
    follows the ``astra init`` boilerplate; on top of it sit the uv
    project (``pyproject.toml`` with lightcone-cli locked in,
    ``.python-version``, ``uv.lock``, ``.venv``), ``.gitignore`` entries,
    ``results/``, and a template MyST report (``myst.yml`` +
    ``index.md``).
    """
    from lightcone.engine.project import converge

    directory = directory.resolve()
    write = not check_only

    if write and not as_json:
        _console().print(f"[cyan]{_LIGHTCONE}[/cyan]")

    report = converge(directory, write=write)

    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
    else:
        _render_init_output(report, directory, dry_run=check_only)

    if check_only and not report.converged:
        sys.exit(1)


def _render_init_output(report: ConvergenceReport, directory: Path, *, dry_run: bool) -> None:
    """Print a convergence report: the items, then the verdict.

    ``--check`` and a real run print the *same* report — they disagree
    only about tense — so ``dry_run`` selects the mood and nothing else,
    mirroring the ``write`` flag convergence itself takes. Blocked items
    are listed with created and repaired ones because they count the same
    way: they are what lets a report say "not converged" (see
    :class:`~lightcone.engine.project.ConvergenceReport`). Their reason
    arrives separately, as a warning.
    """
    mark, style = ("·", "yellow") if dry_run else ("✓", "green")

    lines: list[str] = []
    for items, label, item_mark, item_style in (
        (report.created, "would create" if dry_run else "created", mark, style),
        (report.repaired, "would repair" if dry_run else "repaired", mark, style),
        (report.blocked, "blocked", "✗", "red"),
    ):
        lines += [f"  [{item_style}]{item_mark}[/{item_style}] {label} {item}" for item in items]
    lines += [f"  [yellow]![/yellow] {warning}" for warning in report.warnings]

    if report.converged:
        # A dry run over a converged project finds nothing to do because
        # there is nothing to do — one line serves both moods.
        verdict = f"[green]✓[/green] {directory} is already converged — nothing to do"
    elif report.blocked:
        # A write run that left an item blocked did not converge the
        # project either; only a dry run gets to be neutral about it.
        verdict = f"[red]✗[/red] {directory} is not converged"
    elif dry_run:
        verdict = f"[yellow]![/yellow] {directory} is not converged"
    else:
        verdict = f"[green]✓[/green] Project converged at {directory}"

    if lines:
        lines.append("")  # space the verdict off the list
    _console().print("\n".join([*lines, verdict]))


# =============================================================================
# lc run
# =============================================================================


@main.command(context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False})
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
def run(command: tuple[str, ...]) -> None:
    """Run COMMAND in the project environment, inside the sandbox.

    Byte-for-byte the environment recipes get — same lock, same
    ``.venv``, same boundary — so a command that works here means a
    recipe will. The project and the inputs declared in ``astra.yaml``
    are readable, ``results/`` and scratch are writable, and anything
    outside that — a host tool, a system library, an undeclared file —
    is refused.
    """
    from lightcone.engine import run as engine_run
    from lightcone.engine.project import current_project

    outcome = engine_run.probe(current_project(), command)
    if outcome.notes:
        # click.echo, not rich: these lines are built to be pasted, and
        # reflowing a `uv add` remedy would break the one thing the
        # denial message is for.
        click.echo("\n".join(["", *outcome.notes]), err=True)
    # `Popen.returncode` is negative for a signal, and `sys.exit(-9)`
    # truncates to 247. `lc run` is a proxy for the command it runs, so
    # an OOM-killed probe comes back as the shell's conventional 128+N.
    code = outcome.returncode
    sys.exit(128 - code if code < 0 else code)
