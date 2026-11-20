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

    Engine prose and paths are escaped — they are data, not markup, and a
    literal ``[project]`` or a bracketed path must reach the terminal as
    written.
    """
    from rich.markup import escape

    mark, style = ("·", "yellow") if dry_run else ("✓", "green")
    where = escape(str(directory))

    lines: list[str] = []
    for items, label, item_mark, item_style in (
        (report.created, "would create" if dry_run else "created", mark, style),
        (report.repaired, "would repair" if dry_run else "repaired", mark, style),
        (report.blocked, "blocked", "✗", "red"),
    ):
        lines += [
            f"  [{item_style}]{item_mark}[/{item_style}] {label} {escape(item)}"
            for item in items
        ]
    lines += [f"  [yellow]![/yellow] {escape(warning)}" for warning in report.warnings]

    if report.converged:
        # A dry run over a converged project finds nothing to do because
        # there is nothing to do — one line serves both moods.
        verdict = f"[green]✓[/green] {where} is already converged — nothing to do"
    elif report.blocked:
        # A write run that left an item blocked did not converge the
        # project either; only a dry run gets to be neutral about it.
        verdict = f"[red]✗[/red] {where} is not converged"
    elif dry_run:
        verdict = f"[yellow]![/yellow] {where} is not converged"
    else:
        verdict = f"[green]✓[/green] Project converged at {where}"

    if lines:
        lines.append("")  # space the verdict off the list
    _console().print("\n".join([*lines, verdict]))


# =============================================================================
# lc run
# =============================================================================


@main.command(context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False})
@click.argument("cluster_id", metavar="CLUSTER")
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
def run(cluster_id: str, command: tuple[str, ...]) -> None:
    """Run COMMAND on CLUSTER in the project's sandboxed environment.

    Use `lc run CLUSTER -- COMMAND...` with a cluster name or ID; the allocation
    remains available after the command finishes.
    """
    from lightcone.engine import run as engine_run
    from lightcone.engine.project import current_project

    _require_cluster_id(cluster_id)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise click.UsageError("A command is required; no interactive shell is opened.")
    try:
        outcome = engine_run.probe(current_project(), command, cluster_id=cluster_id)
    except KeyboardInterrupt:
        _interrupted(cluster_id, "the remote command may still be running")
        raise
    if outcome.notes:
        click.echo("\n".join(["", *outcome.notes]), err=True)
    # `Popen.returncode` is negative for a signal, and `sys.exit(-9)`
    # truncates to 247. `lc run` is a proxy for the command it runs, so
    # an OOM-killed probe comes back as the shell's conventional 128+N.
    code = outcome.returncode
    sys.exit(128 - code if code < 0 else code)


def _interrupted(cluster_id: str, still: str, then: str = "") -> None:
    """Say what an interrupt leaves behind: the allocation outlives the client.

    A name can be reused once its allocation ends, so the remedy asks for
    the full ID rather than repeating a name that may point elsewhere.
    """
    target = cluster_id if cluster_id.startswith("clu_") else "<full-id>"
    click.echo(
        f"Interrupted; {still}. Stop the allocation with `lc compute down {target}`{then}.",
        err=True,
    )
    if target != cluster_id:
        click.echo(
            "Inspect `lc compute status --json` to identify the original allocation; "
            "cluster names can be reused.", err=True,
        )


def _require_cluster_id(value: str) -> None:
    """Validate positional cluster syntax before project preparation."""
    from lightcone.engine import compute
    from lightcone.engine.project import ProjectError

    try:
        compute.validate_id(value)
    except ProjectError as exc:
        raise click.BadParameter(str(exc), param_hint="CLUSTER") from exc


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
@click.argument("targets", nargs=-1, metavar="CLUSTER [TARGETS...]")
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
        "Also remake results that are behind — still what the analysis "
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
    """Make the analysis's results on CLUSTER, committing each as it lands.

    Execution requires a cluster name or ID as its first positional argument.
    With --check, all positional arguments are targets and no cluster is used.
    A target is a result's id (`fit#baseline`), or an output's id (`fit`)
    for its result in every universe.

    Each result is committed together with its ASTRA record, in a commit
    that records the command that produced it. The git tree needs to be
    clean before the run can start.

    A result is made when ASTRA's checker finds it missing or stale: never
    made, or something it is made from changed since its record's commit —
    its command, a decision, the content of what it reads. Content is
    compared, so a rebuild that comes out byte-identical stops there
    instead of cascading. A result whose bytes neither this clone nor any
    of its remotes holds is missing, and made again too. One a remote
    holds is fetched only where a recipe reads it; `lc fetch` brings it
    otherwise.

    A result made under an earlier environment is reported as behind and
    left alone: it is still what the analysis asks for, and its record
    names the environment and the commit that produced it. Pass
    --refresh to remake those too.
    """
    from lightcone.engine import container as engine_container
    from lightcone.engine import materialize as engine
    from lightcone.engine.project import current_project

    cluster_id = ""
    if check_only:
        from lightcone.engine.compute.model import ComputeError, Identity

        for target in targets:
            try:
                Identity.decode(target)
            except ComputeError:
                continue
            raise click.UsageError("--check takes result targets, not a cluster ID; omit the ID.")
    else:
        if not targets:
            raise click.UsageError(
                "Missing CLUSTER; create an allocation with lc compute launch."
            )
        cluster_id, *remaining = targets
        _require_cluster_id(cluster_id)
        targets = tuple(remaining)
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
        try:
            report = engine.materialize(root, targets, cluster_id=cluster_id, refresh=refresh)
        except KeyboardInterrupt:
            _interrupted(
                cluster_id, "remote recipes may still be writing results",
                " and confirm they have stopped before cleaning results/",
            )
            raise

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
    """Report where each of the analysis's results stands.

    For every result the analysis plans: unchanged; behind, where it is
    unchanged but was made under an earlier environment; stale, where
    something it is made from changed since it was made, or the file at its
    place is not the one its recipe made; or missing, never made, or made
    with its bytes in neither this clone nor any of its remotes. Beside
    each, once it has been made, the commit it was made at. A result that
    is behind is not wrong; that commit is where the code and the
    environment which produced it can be read back.

    An unchanged or behind result whose bytes this clone lacks is marked
    not here, with the remotes that hold them: `lc fetch` brings it.
    --json adds why each result is in its state, whether its bytes are
    here and which remotes hold them, and its status as ASTRA's checker
    reports it, under `astra`.

    Reads only. It runs nothing, fetches nothing, commits nothing, does not
    mind an unclean tree, and always exits 0 — a state is not a failure.
    Use `lc materialize --check` for a gate that exits nonzero.
    """
    from lightcone.engine import materialize as engine
    from lightcone.engine.project import current_project

    report = engine.status(current_project())
    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
        return

    from rich.markup import escape

    lines = []
    if report.image is not None:
        tag, state = report.image["tag"], report.image["state"]
        described = {
            "present": "built",
            "absent": "needs build — run `lc build`",
            "unfetched": "content not in this clone — the next build or run fetches it",
        }[state]
        lines += [f"  image: {tag} — {described}", ""]
    marks = {
        "unchanged": "[dim]·[/dim]",
        "behind": "[cyan]·[/cyan]",
        "stale": "[yellow]![/yellow]",
        "missing": "[yellow]![/yellow]",
    }
    width = max((len(r.result) for r in report.results), default=0)
    # The commit gets a column of its own, for every state and not only
    # the interesting ones: "which code made this" is the question the
    # verb exists to answer, and it has an answer for an unchanged result
    # too. Why a result is in its state is not shown: the state is the
    # answer at this level, and --json and `lc materialize --check` carry
    # the reasons. Whether its bytes are here is, for a result that is
    # current: one a run makes again needs no fetching.
    away = 0
    for r in report.results:
        commit = r.commit[:7] or "—"
        line = f"  {marks[r.state]} {r.state:<9} {r.result:<{width}}  "
        if r.state in ("unchanged", "behind") and not r.here:
            away += 1
            on = f", on {escape(', '.join(r.remotes))}" if r.remotes else ""
            line += f"{commit:<7}  [dim]not here{on}[/dim]"
        else:
            line += commit
        lines.append(line)
    lines += [f"  [yellow]![/yellow] {escape(warning)}" for warning in report.warnings]

    counts = report.counts
    if not report.results:
        lines.append("[dim]The analysis plans no result.[/dim]")
    else:
        lines.append("")
        lines.append(
            " · ".join(f"{count} {state}" for state, count in counts.items() if count)
            + (f" · {away} not here — `lc fetch` brings them" if away else "")
        )
    _console().print("\n".join(lines))


# =============================================================================
# lc fetch
# =============================================================================


@main.command()
@click.argument("targets", nargs=-1, metavar="[TARGETS...]")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON on stdout.",
)
def fetch(targets: tuple[str, ...], as_json: bool) -> None:
    """Bring the bytes of current results into this clone.

    A clone, or a pull, holds each result as a small stand-in for its
    bytes until they are fetched; `lc status` marks those results not
    here, with the remotes that hold them. This fetches the ones that are
    unchanged or behind. A result that is stale or missing is not
    fetched: what a remote holds of it is not what the analysis asks for,
    and `lc materialize` makes it again. A target is a result's id
    (`fit#baseline`), or an output's id (`fit`) for its result in every
    universe; with none, every result.

    Nothing is made or committed, and no cluster is used. Exits 1 if a
    result could not be fetched. One that no remote holds any more is
    missing from then on, and `lc materialize` makes it again.
    """
    from rich.markup import escape

    from lightcone.engine import materialize as engine
    from lightcone.engine.project import current_project

    report = engine.fetch(current_project(), targets)
    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
    else:
        lines = [f"  [green]✓[/green] fetched {name}" for name in report.fetched]
        lines += [
            f"  [yellow]·[/yellow] {state} {name} — `lc materialize` makes it again"
            for name, state in report.left.items()
        ]
        lines += [f"  [red]✗[/red] {name} — {escape(why)}" for name, why in report.failed.items()]
        lines += [f"  [yellow]![/yellow] {escape(warning)}" for warning in report.warnings]
        if report.failed:
            verdict = f"[red]✗[/red] {len(report.failed)} result(s) could not be fetched"
        elif report.fetched:
            verdict = f"[green]✓[/green] Fetched {len(report.fetched)} result(s)"
        else:
            verdict = "[green]✓[/green] Nothing to fetch" + (
                "" if report.left else " — every result is here"
            )
        _console().print("\n".join([*lines, *([""] if lines else []), verdict]))
    if not report.ok:
        sys.exit(1)


# =============================================================================
# Rendering
# =============================================================================


def _render_materialize_output(report: MaterializeReport, root: Path, *, dry_run: bool) -> None:
    """Print what ran, or what would.

    Engine prose and paths are escaped — they are data, not markup, and a
    literal ``[project]`` or a bracketed path must reach the terminal as
    written. Task names need no escape: ASTRA ids cannot hold a bracket.
    """
    from rich.markup import escape

    where = escape(str(root))
    lines = [
        f"  [yellow]·[/yellow] would run {name} — {escape(why)}"
        for name, why in report.planned.items()
    ]
    lines += [f"  [green]✓[/green] made {name}" for name in report.made]
    # Behind is not a warning and not a problem: it is a fact about where
    # a result came from, and the only line here that tells you something
    # you could not have worked out from the exit code.
    lines += [
        f"  [cyan]·[/cyan] behind {name} — {escape(why)}"
        for name, why in report.behind.items()
    ]
    lines += [f"  [dim]·[/dim] unchanged {name}" for name in report.unchanged]
    lines += [f"  [red]✗[/red] failed {name}" for name in report.failed]
    lines += [f"  [red]✗[/red] blocked {name}" for name in report.blocked]
    lines += [f"  [yellow]![/yellow] {escape(warning)}" for warning in report.warnings]

    if not report.ok:
        verdict = f"[red]✗[/red] {where} did not finish"
    elif report.up_to_date:
        verdict = f"[green]✓[/green] {where} is up to date — nothing to do"
    elif dry_run:
        verdict = f"[yellow]![/yellow] {len(report.planned)} result(s) would be made"
    else:
        verdict = f"[green]✓[/green] Made {len(report.made)} result(s) in {where}"
    # On the verdict line as well as in the listing: on a large analysis the
    # listing scrolls away, and this is the one state that reports something
    # rather than doing it.
    if report.behind:
        verdict += f" · {len(report.behind)} behind — `--refresh` remakes them"

    if lines:
        lines.append("")
    _console().print("\n".join([*lines, verdict]))


# Importing the command definitions does not import Dask or provider implementations.
from lightcone.cli.compute import compute  # noqa: E402

main.add_command(compute)
