"""Command-line interface for lightcone-cli — the ASTRA execution layer.
"""

from __future__ import annotations

import functools
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from rich.console import Console

    from lightcone.engine.materialize import MaterializeReport
    from lightcone.engine.project import ConvergenceReport
    from lightcone.engine.publish import PublishReport

logger = logging.getLogger(__name__)

#: Lightcone Research's palette as a terminal shows it, each tone by the
#: name markup calls it. A terminal never says whether its ground is dark
#: or light, so a tone has to read on both. Antique Gold and Mushroom Cap
#: do as they are; the others are too dark for a dark ground (Blue Ink is
#: 2.5:1 there) and are mixed toward white until they read on either,
#: near 4.5:1 on dark and 3.7:1 on light. On a terminal with fewer
#: colours, rich takes the nearest it has.
_PALETTE = {
    "gold": "#a67c3c",  # Antique Gold
    "ink": "#7a8394",  # Blue Ink, 25% toward white
    "slate": "#5c8793",  # Slate Blue, 15%
    "verdigris": "#588e88",  # Vert de Gris, 15%
    "wax": "#b2735f",  # Wax Red, 15%
    "mushroom": "#8b7d70",  # Mushroom Cap
}

# The marks every verb speaks with, each in the tone of what it says: done,
# still to do, wants attention, failed, made under an earlier environment,
# and nothing to do.
_DONE = "[verdigris]✓[/verdigris]"
_TODO = "[gold]·[/gold]"
_WARN = "[gold]![/gold]"
_FAILED = "[wax]✗[/wax]"
_BEHIND = "[slate]·[/slate]"
_QUIET = "[mushroom]·[/mushroom]"


@functools.cache
def _console() -> Console:
    """Build the rich console, once and on first use.

    Returns:
        The console, which knows the palette's tones by name. Deferred so
        ``lc --help`` and shell completion never pay for it.
    """
    from rich.console import Console
    from rich.theme import Theme

    # No highlighter: rich's own colours numbers, paths and quoted words in
    # a terminal's cyan, magenta and green, which are no tone of the palette.
    return Console(theme=Theme(_PALETTE), highlight=False)


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
#: What `lc init` opens with, for a person at a terminal. The mark is
#: Lightcone's, stippled in braille, where a character is a grid of two
#: dots by four: the two cones with their rims, the axis through them, and
#: the line through the apex, which the rule beside it carries on. A blank
#: cell of the mark is U+2800, so every row is as wide. Beside it: the
#: tool's name, a rule that fades at both ends, and what lc is.
_BANNER = """
  [gold]⠀⠀⢀⡠⠤⡧⠤⣀⠀⠀[/gold]
  [gold]⠀⠀⠑⢦⠤⡧⢤⠖⠁⠀[/gold]   [bold][gold]L I G H T C O N E[/gold][/bold]   [ink]C L I[/ink]
  [gold]⠀⠀⢀⣈⣢⣧⣊⡠⠤⠤   ┈┈╌╌─────────────────────╌╌┈┈[/gold]
  [gold]⠉⠉⠁⢀⠜⡟⢄⠀⠀⠀[/gold]   [slate]THE EXECUTION LAYER FOR ASTRA[/slate]
  [gold]⠀⠀⡠⠗⠒⡗⠒⠧⡀⠀[/gold]
  [gold]⠀⠀⠈⠑⠒⡗⠒⠉⠀⠀[/gold]
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

    In a clone it sets up git-annex, which enables the store the project
    declares for its data, so `lc fetch` works from there. It creates
    nothing online: `lc publish` does, when you choose to.
    """
    from lightcone.engine.project import converge

    directory = directory.resolve()
    write = not check_only

    # A transcript or a log has no use for a logo: an agent runs this verb
    # more often than a person does.
    if write and not as_json and _console().is_terminal:
        _console().print(_BANNER)

    report = converge(directory, write=write)

    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
    else:
        _render_init_output(report, directory, dry_run=check_only)
        if write and not report.blocked and not report.online:
            click.echo(
                "\n  Not online yet. To back it up and share it, choose who can read it:\n"
                f"    lc publish github.com/OWNER/{directory.name} --private\n"
                f"    lc publish github.com/OWNER/{directory.name} --public"
            )

    if check_only and not report.converged:
        sys.exit(1)


def _render_init_output(report: ConvergenceReport, directory: Path, *, dry_run: bool) -> None:
    """Print a convergence report: the items, then the verdict.

    Engine prose and paths are escaped — they are data, not markup, and a
    literal ``[project]`` or a bracketed path must reach the terminal as
    written.
    """
    from rich.markup import escape

    mark = _TODO if dry_run else _DONE
    where = escape(str(directory))

    lines: list[str] = []
    for items, label, item_mark in (
        (report.created, "would create" if dry_run else "created", mark),
        (report.repaired, "would repair" if dry_run else "repaired", mark),
        (report.blocked, "blocked", _FAILED),
    ):
        lines += [f"  {item_mark} {label} {escape(item)}" for item in items]
    lines += [f"  {_WARN} {escape(warning)}" for warning in report.warnings]

    if report.converged:
        # A dry run over a converged project finds nothing to do because
        # there is nothing to do — one line serves both moods.
        verdict = f"{_DONE} {where} is already converged — nothing to do"
    elif report.blocked:
        # A write run that left an item blocked did not converge the
        # project either; only a dry run gets to be neutral about it.
        verdict = f"{_FAILED} {where} is not converged"
    elif dry_run:
        verdict = f"{_WARN} {where} is not converged"
    else:
        verdict = f"{_DONE} Project converged at {where}"

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


def _interrupted(cluster_id: str, still: str) -> None:
    """Say what an interrupt leaves behind: the allocation outlives the client.

    A name can be reused once its allocation ends, so the remedy asks for
    the full ID rather than repeating a name that may point elsewhere.
    """
    target = cluster_id if cluster_id.startswith("clu_") else "<full-id>"
    click.echo(
        f"Interrupted; {still}. Stop the allocation with `lc compute down {target}`.",
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
    fetch the exact bytes instead of rebuilding.
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
    _console().print(f"{_DONE} {verb} {runtime.image_tag} ({runtime.archive})")


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
                cluster_id,
                "recipes may still be running, in the run's checkout, which stays until the "
                "allocation has ended; the project holds what was committed",
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

    For every result the analysis plans: ok; behind, where it is ok but
    was made under an earlier environment; stale, where something it is
    made from changed since it was made, or the file at its place is not
    the one its recipe made; or missing, never made, or made with its
    bytes in neither this clone nor any of its remotes. Beside each, once
    it has been made, the commit it was made at. A result that is behind
    is not wrong; that commit is where the code and the environment which
    produced it can be read back.

    An ok or behind result whose bytes this clone lacks is marked
    unfetched, with the remotes that hold them: `lc fetch` brings it.
    --json adds why each result is in its state, whether it is unfetched
    and which remotes hold its bytes, and its status as ASTRA's checker
    reports it, under `astra`.

    While the project is not online at all, a last line says so, with the
    `lc publish` that puts it there. Once it is, what of it is published
    is `lc publish --check`'s to say.

    Reads only. It runs nothing, fetches nothing, commits nothing, does not
    mind an unclean tree, and always exits 0 — a state is not a failure.
    Use `lc materialize --check` for a gate that exits nonzero.
    """
    from lightcone.engine import materialize as engine
    from lightcone.engine.project import current_project

    root = current_project()
    report = engine.status(root)
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
        "ok": _QUIET,
        "behind": _BEHIND,
        "stale": _WARN,
        "missing": _WARN,
    }
    width = max((len(r.result) for r in report.results), default=0)
    # The commit gets a column of its own, for every state and not only
    # the interesting ones: "which code made this" is the question the
    # verb exists to answer, and it has an answer for an ok result
    # too. Why a result is in its state is not shown: the state is the
    # answer at this level, and --json and `lc materialize --check` carry
    # the reasons. Whether it is unfetched is: that is a fetch, not a run.
    for r in report.results:
        commit = r.commit[:7] or "—"
        line = f"  {marks[r.state]} {r.state:<7} {r.result:<{width}}  "
        if r.unfetched:
            on = f", on {escape(', '.join(r.remotes))}" if r.remotes else ""
            line += f"{commit:<7}  [mushroom]unfetched{on}[/mushroom]"
        else:
            line += commit
        lines.append(line)
    lines += [f"  {_WARN} {escape(warning)}" for warning in report.warnings]

    counts = report.counts
    unfetched = sum(r.unfetched for r in report.results)
    if not report.results:
        lines.append("[mushroom]The analysis plans no result.[/mushroom]")
    else:
        lines.append("")
        lines.append(
            " · ".join(f"{count} {state}" for state, count in counts.items() if count)
            + (f" · {unfetched} unfetched — `lc fetch` brings them" if unfetched else "")
        )
    _console().print("\n".join(lines))
    if not report.online:
        click.echo(_NOT_ONLINE.format(name=root.name))


#: The one line that says a project is not online, and what puts it there.
_NOT_ONLINE = (
    "Not online yet — `lc publish github.com/OWNER/{name} --private` or `--public` "
    "puts it there"
)


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
    """Bring the bytes of current results, and of named inputs, into this clone.

    A clone, or a pull, holds each result as a small stand-in for its
    bytes until they are fetched; `lc status` marks those results
    unfetched, with the remotes that hold them, and this fetches them:
    the ones that are ok or behind. A result that is stale or missing is not
    fetched: what a remote holds of it is not what the analysis asks for,
    and `lc materialize` makes it again. A target is a result's id
    (`fit#baseline`), an output's id (`fit`) for its result in every
    universe, or an input's id (`catalog`); with none, every result.

    Nothing is made or committed, and no cluster is used. Exits 1 if
    something named could not be fetched. A result that no remote holds
    any more is missing from then on, and `lc materialize` makes it again.
    """
    from rich.markup import escape

    from lightcone.engine import materialize as engine
    from lightcone.engine.project import current_project

    report = engine.fetch(current_project(), targets)
    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
    else:
        lines = [f"  {_DONE} fetched {name}" for name in report.fetched]
        lines += [
            f"  {_TODO} {state} {name} — `lc materialize` makes it again"
            for name, state in report.left.items()
        ]
        lines += [f"  {_FAILED} {name} — {escape(why)}" for name, why in report.failed.items()]
        lines += [f"  {_WARN} {escape(warning)}" for warning in report.warnings]
        def counted(names: Iterable[str]) -> str:
            inputs = sum(name in report.inputs for name in names)
            results = sum(name not in report.inputs for name in names)
            return " and ".join(
                [
                    *([f"{results} result(s)"] if results else []),
                    *([f"{inputs} input(s)"] if inputs else []),
                ]
            )

        if report.failed:
            verdict = f"{_FAILED} {counted(report.failed)} could not be fetched"
        elif report.fetched:
            verdict = f"{_DONE} Fetched {counted(report.fetched)}"
        else:
            verdict = f"{_DONE} Nothing to fetch" + (
                ""
                if report.left
                else " — it is all here"
                if report.inputs
                else " — every result is here"
            )
        _console().print("\n".join([*lines, *([""] if lines else []), verdict]))
    if not report.ok:
        sys.exit(1)


# =============================================================================
# lc publish
# =============================================================================


@main.command()
@click.argument("url", required=False, default="", metavar="[URL]")
@click.option(
    "--private",
    "private",
    is_flag=True,
    help="Create the repository readable only by people you invite.",
)
@click.option(
    "--public",
    "public",
    is_flag=True,
    help=(
        "Create the repository readable by anyone, or make a private one public. "
        "Permanent, and needs a license in pyproject.toml."
    ),
)
@click.option(
    "--data",
    default="",
    metavar="WHERE",
    help=(
        "Where the project's data lives, chosen once: github-lfs (the repository's Git "
        "LFS storage), a new directory on your cluster, or ssh://HOST/DIRECTORY."
    ),
)
@click.option(
    "--confirm-data",
    "confirm",
    default="",
    metavar="WHERE",
    help=(
        "Confirm, once for this clone, that WHERE is where the project's data goes. "
        "Asked of a clone that did not choose it."
    ),
)
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Report what is not published, sending nothing; exit 1 if anything is not.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON on stdout.",
)
def publish(
    url: str,
    private: bool,
    public: bool,
    data: str,
    confirm: str,
    check_only: bool,
    as_json: bool,
) -> None:
    """Put the project online: its commits on GitHub, its data in its store.

    URL names the repository on GitHub (github.com/OWNER/NAME), and is
    needed only the first time. The first publication makes the two
    choices no tool should make for you: who can read the project
    (--private or --public, when lc creates the repository), and where its
    data lives (--data). Both are asked once; every clone learns them. A
    repository that does not exist is created with a token from GH_TOKEN,
    GITHUB_TOKEN or `gh`.

    Every publication then sends, in this order, the bytes of what the
    commit names and the store lacks, git-annex's log of where they are,
    and the current branch. Where some bytes could not be sent, no commit
    is, and it exits 1: run it again to send the rest. Run it again
    whenever there is something new: with nothing to send it says so. A
    plain `git push` sends the commits without their bytes, and every
    clone then sees those results as missing.

    A clone that did not choose the store confirms it once before it
    publishes, with --confirm-data: lc shows where the data goes and
    never confirms it by itself. Reading needs no confirmation.

    Uncommitted changes are not published. Nothing is ever deleted, no
    tag or other branch is sent, and nothing is merged: where GitHub has
    commits this clone lacks, `git pull` first.
    """
    from lightcone.engine import publish as engine
    from lightcone.engine.project import current_project

    if private and public:
        raise click.UsageError("--private and --public are two answers to one question.")
    root = current_project()
    report = engine.publish(
        root,
        url,
        visibility="private" if private else "public" if public else "",
        data=data,
        confirm=confirm,
        check=check_only,
    )
    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
    elif not report.target:
        click.echo(_NOT_ONLINE.format(name=root.name))
    else:
        _render_publish_output(report, dry_run=check_only)
    if not report.ok or (check_only and not report.published):
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
        f"  {_TODO} would run {name} — {escape(why)}"
        for name, why in report.planned.items()
    ]
    lines += [f"  {_DONE} made {name}" for name in report.made]
    # Behind is not a warning and not a problem: it is a fact about where
    # a result came from, and the only line here that tells you something
    # you could not have worked out from the exit code.
    lines += [
        f"  {_BEHIND} behind {name} — {escape(why)}"
        for name, why in report.behind.items()
    ]
    lines += [f"  {_QUIET} ok {name}" for name in report.unchanged]
    lines += [f"  {_FAILED} failed {name}" for name in report.failed]
    lines += [f"  {_FAILED} blocked {name}" for name in report.blocked]
    lines += [f"  {_WARN} {escape(warning)}" for warning in report.warnings]

    if not report.ok:
        verdict = f"{_FAILED} {where} did not finish"
    elif report.up_to_date:
        verdict = f"{_DONE} {where} is up to date — nothing to do"
    elif dry_run:
        verdict = f"{_WARN} {len(report.planned)} result(s) would be made"
    else:
        verdict = f"{_DONE} Made {len(report.made)} result(s) in {where}"
    # On the verdict line as well as in the listing: on a large analysis the
    # listing scrolls away, and this is the one state that reports something
    # rather than doing it.
    if report.behind:
        verdict += f" · {len(report.behind)} behind — `--refresh` remakes them"

    if lines:
        lines.append("")
    _console().print("\n".join([*lines, verdict]))


def _counted(files: dict[str, str]) -> str:
    """Count files by what each is: `2 results, 1 input`."""
    tally: dict[str, int] = {}
    for kind in files.values():
        tally[kind] = tally.get(kind, 0) + 1
    return ", ".join(
        f"{count} {kind}{'s' if count > 1 else ''}"
        for kind in ("result", "input", "file")
        if (count := tally.get(kind))
    )


def _render_publish_output(report: PublishReport, *, dry_run: bool) -> None:
    """Print what a publication sent, or what one would.

    Engine prose, paths and what a remote said are escaped: they are
    data, not markup.
    """
    from rich.markup import escape

    target, store = report.target, escape(report.store)
    lines = []
    if report.created:
        lines.append(f"  {_DONE} created  {target}, {report.visibility}")
    if report.declared:
        lines.append(f"  {_DONE} store    {store}, where this project's data lives from now on")
    if report.confirmed:
        lines.append(f"  {_DONE} store    {store}, confirmed for this clone")
    if report.sent:
        lines.append(f"  {_DONE} data     {_counted(report.sent)} → {store}")
    if report.unsent:
        lines.append(
            f"  {_TODO} data     {_counted(report.unsent)} to send → {store or 'no store yet'}"
        )
        lines += [f"      {escape(name)}" for name in report.unsent]
    lines += [f"  {_FAILED} {escape(name)} — {escape(why)}" for name, why in report.failed.items()]
    if report.nowhere:
        lines.append(
            f"  {_WARN} {len(report.nowhere)} file(s) with bytes in neither this clone nor the "
            "store: other clones see those results as missing"
        )
        lines += [f"      {escape(name)}" for name in report.nowhere]
    if report.commits:
        lines.append(f"  {_DONE} commits  {report.branch} at {report.commit[:7]} → {target}")
    if report.unpushed:
        lines.append(f"  {_TODO} commits  {report.unpushed} of {report.branch} to send → {target}")
    if report.made_public:
        lines.append(f"  {_DONE} license  {escape(report.license)}")
        lines.append(
            f"  {_DONE} visible  {target} is public, "
            + (
                "and its data in GitHub LFS with it"
                if report.store.startswith("GitHub LFS")
                else f"and its data stays in {store}, for those who can reach it"
            )
        )
    lines += [f"  {_WARN} {escape(warning)}" for warning in report.warnings]
    if report.uncommitted:
        lines.append(f"  {_WARN} {report.uncommitted} uncommitted change(s) are not published")

    if report.failed:
        verdict = (
            f"{_FAILED} {len(report.failed)} file(s) could not be sent, so no commit was — "
            "publishing again continues from here"
        )
    elif dry_run:
        at = f"{report.branch} at {report.commit[:7]}"
        verdict = (
            f"{_DONE} {target} holds {at}, and its data"
            if report.published
            else f"{_WARN} {target} does not hold all of {at} — `lc publish` sends it"
        )
    elif report.created or report.declared:
        verdict = (
            f"{_DONE} Published. A clone needs only: git clone https://{target} && lc init"
        )
    elif report.sent or report.commits or report.made_public or report.confirmed:
        verdict = f"{_DONE} Published to {target}"
    else:
        verdict = f"{_DONE} {target} is up to date — nothing to send"
    if lines:
        lines.append("")
    _console().print("\n".join([*lines, verdict]))


# Importing the command definitions does not import Dask or provider implementations.
from lightcone.cli.compute import compute  # noqa: E402

main.add_command(compute)
