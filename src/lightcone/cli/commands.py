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

    from lightcone.engine.materialize import MaterializeReport
    from lightcone.engine.project import ConvergenceReport

logger = logging.getLogger(__name__)


@functools.cache
def _console() -> Console:
    """Build the rich console, once and on first use.

    Returns:
        The console. Deferred so ``lc --help`` and shell completion never
        pay for it.
    """
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
        """Run a command, rendering engine errors as clean CLI errors.

        Args:
            ctx: The click context.

        Returns:
            Whatever the command returned.

        Raises:
            click.ClickException: In place of any ``ProjectError``.
        """
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
    """Run COMMAND in the project environment, under isolation.
    """
    from lightcone.engine import run as engine_run
    from lightcone.engine.project import current_project

    outcome = engine_run.probe(current_project(), command)
    if outcome.notes:
        click.echo("\n".join(["", *outcome.notes]), err=True)
    # `Popen.returncode` is negative for a signal, and `sys.exit(-9)`
    # truncates to 247. `lc run` is a proxy for the command it runs, so
    # an OOM-killed probe comes back as the shell's conventional 128+N.
    code = outcome.returncode
    sys.exit(128 - code if code < 0 else code)


# =============================================================================
# lc build
# =============================================================================


@main.command()
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the result as JSON on stdout.",
)
def build(as_json: bool) -> None:
    """Build the project's system-layer image, and commit it.

    Containerized projects only — a project containerizes by declaring
    [tool.lightcone.image] in pyproject.toml. The image is saved into the
    repository (.datalad/environments/) as versioned content, so clones
    obtain the exact bytes with `git annex get` instead of rebuilding.
    Idempotent: an image that is already built and committed is left
    alone.
    """
    from lightcone.engine import container as engine_container
    from lightcone.engine.project import current_project

    root = current_project()
    state, tag, _ = engine_container.image_state(root)
    if state == "direct":
        if as_json:
            click.echo(json.dumps({"mode": "direct"}))
        else:
            _console().print(
                "direct mode — no image to build; declare [bold]\\[tool.lightcone.image][/bold] "
                "in pyproject.toml to containerize this project."
            )
        return
    if state == "absent" and not as_json:
        _console().print(f"building [bold]{tag}[/bold] — this can take minutes")
    runtime, action = engine_container.build(root)
    if as_json:
        click.echo(
            json.dumps(
                {
                    "mode": "containerized",
                    "tag": runtime.image_tag,
                    "id": runtime.image_id,
                    "archive": runtime.archive,
                    "action": action,
                }
            )
        )
        return
    verb = "Built and committed" if action == "built" else "Already built —"
    _console().print(f"[green]✓[/green] {verb} {runtime.image_tag} ({runtime.archive})")


# =============================================================================
# lc materialize
# =============================================================================


@main.command()
@click.argument("targets", nargs=-1)
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help=(
        "Report what would run and why, without executing or committing "
        "anything; exit 1 if anything is out of date."
    ),
)
@click.option(
    "--refresh",
    is_flag=True,
    help=(
        "Also remake outputs that are behind — still what the analysis "
        "asks for, but made under an earlier environment."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON on stdout.",
)
def materialize(
    targets: tuple[str, ...], check_only: bool, refresh: bool, as_json: bool
) -> None:
    """Make the analysis's outputs, and commit each one as it lands.

    Each output is committed together with its manifest, in a commit that
    records the command that produced it. The git tree needs to be clean
    before the run can start.

    An output is remade when the analysis defines it differently than it
    was made — a changed recipe or decision — or when one of its declared
    inputs changed. Inputs are compared by content, so a rebuild that
    comes out byte-identical stops there instead of cascading.

    An output made under an earlier environment is reported as behind and
    left alone: it is still what the analysis asks for, and the manifest
    records the environment and the commit that produced it. Pass
    --refresh to remake those too.
    """
    from lightcone.engine import container as engine_container
    from lightcone.engine import materialize as engine
    from lightcone.engine.project import current_project

    root = current_project()
    if not check_only and not as_json:
        # The engine never prints, and the build it may be about to run
        # can take minutes — so the one place that owns the console says
        # so before handing over. Conditional mood, deliberately: the
        # engine's own refusals (a dirty tree, an invalid spec) come
        # first and cost no build, so this must promise nothing.
        state, tag, _ = engine_container.image_state(root)
        if state == "absent":
            _console().print(
                f"image absent — the run rebuilds [bold]{tag}[/bold] first "
                "(this can take minutes)"
            )
    if check_only:
        report = engine.check(root, targets, refresh=refresh)
    else:
        report = engine.materialize(root, targets, refresh=refresh)

    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
    else:
        if report.notes:
            click.echo("\n".join(["", *report.notes]), err=True)
        _render_materialize_output(report, root, dry_run=check_only)

    if not report.ok or (check_only and not report.up_to_date):
        sys.exit(1)


# =============================================================================
# lc status
# =============================================================================


@main.command()
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON on stdout.",
)
def status(as_json: bool) -> None:
    """Report what state each of the analysis's outputs is in.

    For every output the analysis declares: whether it is current, behind
    or stale, and — once it has been materialized — the commit it was made
    at. An output that is behind is not wrong; that commit is where the
    code and the environment which produced it can be read back.

    Reads only. It runs nothing, commits nothing, does not mind an unclean
    tree, and always exits 0 — a state is not a failure. Use
    `lc materialize --check` for a gate that exits nonzero.
    """
    from lightcone.engine import materialize as engine
    from lightcone.engine.project import current_project

    report = engine.status(current_project())
    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
        return

    lines = [f"  mode:    {report.mode}"]
    if report.image is not None:
        tag, state = report.image["tag"], report.image["state"]
        described = {
            "present": "built",
            "absent": "needs build — run `lc build`",
            "unfetched": "content not in this clone — the next build or run fetches it",
        }[state]
        lines.append(f"  image:   {tag} — {described}")
    lines.append(f"  sandbox: {report.sandbox}")
    lines.append("")
    marks = {"current": "[dim]·[/dim]", "behind": "[cyan]·[/cyan]", "stale": "[yellow]![/yellow]"}
    width = max((len(o.output) for o in report.outputs), default=0)
    lines += [
        # The commit gets a column of its own, for every state and not only
        # the interesting ones: "which code made this" is the question the
        # verb exists to answer, and it has an answer for a current output
        # too.
        f"  {marks[o.status]} {o.status:<8} {o.output:<{width}}  "
        f"{o.git_sha[:7] or '—':<7}" + (f"  [dim]{o.why}[/dim]" if o.why else "")
        for o in report.outputs
    ]
    lines += [f"  [yellow]![/yellow] {warning}" for warning in report.warnings]

    counts = report.counts
    if not report.outputs:
        lines.append("[dim]The analysis declares no output with a recipe.[/dim]")
    else:
        lines.append("")
        lines.append(
            " · ".join(f"{count} {state}" for state, count in counts.items() if count)
        )
    _console().print("\n".join(lines))


# =============================================================================
# Rendering
# =============================================================================


def _render_materialize_output(report: MaterializeReport, root: Path, *, dry_run: bool) -> None:
    """Print what ran, or what would.
    """
    lines = [
        f"  [yellow]·[/yellow] would run {name} — {why}"
        for name, why in report.planned.items()
    ]
    lines += [f"  [green]✓[/green] made {name}" for name in report.made]
    # Behind is not a warning and not a problem: it is a fact about where
    # an output came from, and the only line here that tells you something
    # you could not have worked out from the exit code.
    lines += [
        f"  [cyan]·[/cyan] behind {name} — {why}" for name, why in report.behind.items()
    ]
    lines += [f"  [dim]·[/dim] up to date {name}" for name in report.current]
    lines += [f"  [red]✗[/red] failed {name}" for name in report.failed]
    lines += [f"  [red]✗[/red] blocked {name}" for name in report.blocked]
    lines += [f"  [yellow]![/yellow] {warning}" for warning in report.warnings]

    if not report.ok:
        verdict = f"[red]✗[/red] {root} did not finish"
    elif report.up_to_date:
        verdict = f"[green]✓[/green] {root} is up to date — nothing to do"
    elif dry_run:
        verdict = f"[yellow]![/yellow] {len(report.planned)} output(s) would be made"
    else:
        verdict = f"[green]✓[/green] Made {len(report.made)} output(s) in {root}"
    # On the verdict line as well as in the listing: on a large analysis the
    # listing scrolls away, and this is the one state that reports something
    # rather than doing it.
    if report.behind:
        verdict += f" · {len(report.behind)} behind — `--refresh` remakes them"

    if lines:
        lines.append("")
    _console().print("\n".join([*lines, verdict]))
