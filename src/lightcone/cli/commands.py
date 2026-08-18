"""Command-line interface for lightcone-cli — the ASTRA execution layer.
"""

from __future__ import annotations

import functools
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from rich.console import Console

    from lightcone.engine.project import ConvergenceReport

logger = logging.getLogger(__name__)


@functools.cache
def _console(*, stderr: bool = False) -> Console:
    """The rich console, built on first use to avoid startup cost at each
    invocation of the cli even when the console is not needed.

    The stderr variant is what commentary uses when a command's stdout
    belongs to the thing being run rather than to us.
    """
    from rich.console import Console

    return Console(stderr=stderr)


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
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--no-sandbox",
    "sandboxed",
    flag_value=False,
    default=True,
    help="Run without enforcement. Recorded as unsandboxed — never silent.",
)
@click.option(
    "--sandbox-debug",
    "debug",
    is_flag=True,
    help="Print the policy and the exact command, then run it.",
)
@click.option(
    "--require-sandbox",
    "require",
    is_flag=True,
    help="Refuse to run at all unless this host can actually enforce.",
)
def run(command: tuple[str, ...], sandboxed: bool, debug: bool, require: bool) -> None:
    """Run COMMAND in the project environment, inside the sandbox.

    Byte-for-byte the environment recipes get — same lock, same
    ``.venv``, same boundary — so a probe that works means a recipe
    will. Reads the project tree and the inputs declared in
    ``astra.yaml``; writes nowhere but its own temporary scope.

    With no COMMAND, opens a shell in that environment.
    """
    from lightcone.engine import run as engine_run
    from lightcone.engine.project import find_project

    project = find_project()

    if not command:
        # On stderr, like every other line lc says about a run: the
        # command's stdout is the command's. And it must not claim
        # enforcement it is about to switch off — this line is the only
        # signal before an interactive shell takes the terminal, since
        # the boundary's own notes do not print until the shell exits.
        _echo(
            [
                "opening a shell inside the recipe environment "
                + ("(sandboxed)" if sandboxed else "(NOT sandboxed — --no-sandbox)")
            ]
        )

    outcome = engine_run.probe(
        project,
        command,
        sandboxed=sandboxed,
        require=require,
        on_plan=_render_plan if debug else None,
    )
    _render_notes(outcome.notes)
    sys.exit(_exit_status(outcome.returncode))


def _exit_status(returncode: int) -> int:
    """The child's exit code, in the shell's spelling.

    `Popen.returncode` is *negative* for a signal, and `sys.exit(-9)`
    truncates to 247. `lc run` is a proxy for the command it runs, so an
    OOM-killed probe has to come back as the conventional 137 that a
    script would test for.
    """
    return 128 - returncode if returncode < 0 else returncode


def _echo(lines: Sequence[str], *, indent: str = "") -> None:
    """Commentary on stderr, unstyled and unwrapped.

    stderr because a probe's stdout belongs to the command it ran. And
    unwrapped because these lines are built to be pasted — rich reflowing
    a `uv add` line would break the one thing the denial message is for.
    Blank separators stay blank: indenting them turns every empty line of
    a copied remedy into trailing whitespace.
    """
    console = _console(stderr=True)
    for line in lines:
        console.print(
            f"{indent}{line}" if line else "",
            highlight=False,
            markup=False,
            crop=False,
            overflow="ignore",
        )


def _render_plan(lines: Sequence[str]) -> None:
    """The `--sandbox-debug` dump, printed before the command runs."""
    _echo(lines)
    _console(stderr=True).print()


def _render_notes(notes: Sequence[str]) -> None:
    """The boundary's notes: downgrade notice, denial, failure trailer."""
    if not notes:
        return
    _console(stderr=True).print()
    _echo(notes, indent="  ")
