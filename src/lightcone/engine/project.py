"""Utilities to manage a Lightcone project.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Literal

from astra.scaffold import create_boilerplate

from lightcone.engine import dataset, templates

SPEC_FILENAME = "astra.yaml"


class ProjectError(Exception):
    """A project cannot be read or converged."""


# =============================================================================
# The convergence report
# =============================================================================


@dataclass
class ConvergenceReport:
    """What a convergence did, or — in check mode — would have done.

    ``warnings`` carries problems convergence can *see* but must not fix;
    they are advisory and never affect ``converged``. ``blocked`` is
    different: an item convergence cannot complete at all, which does
    count — a report must never claim a project is converged while
    something convergence is responsible for is absent.
    """

    created: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def converged(self) -> bool:
        """Whether the project needed nothing done to it.

        Note the tense: after a write run that created files this is
        ``False``. It reports what convergence *found*, not whether the
        project is now good.
        """
        return not self.created and not self.repaired and not self.blocked

    def as_dict(self) -> dict[str, object]:
        """Return the report as JSON-ready data, ``converged`` first.

        Built from :func:`dataclasses.asdict`, so a field added to the
        report cannot silently go missing from ``lc init --json``.

        Returns:
            Every field, with ``converged`` first.
        """
        return {"converged": self.converged, **asdict(self)}


class _Converger:
    """Decide-then-maybe-write, so check mode reuses the real decisions.

    Every item routes through :meth:`item`, :meth:`file`, or
    :meth:`blocked`, which record the outcome first and only then apply it
    when ``write`` is set. That is what keeps ``lc init --check`` honest: it
    is the same code path with side effects switched off, not a second
    implementation.
    """

    def __init__(self, *, write: bool) -> None:
        self.write = write
        self.report = ConvergenceReport()

    def item(
        self,
        name: str,
        present: bool,
        apply: Callable[[], object],
        *,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        """Converge something whose *presence* is the question.

        Args:
            name: What to call it in the report.
            present: Whether it is already there.
            apply: Creates or repairs it. Called only when writing.
            is_current: Makes it a *derived* artifact instead, one whose
                agreement with its inputs is the question — a ``uv.lock``
                that no longer matches ``pyproject.toml`` is exactly as
                unconverged as a missing one, and reports as ``repaired``.
                Consulted only when present, so a fresh project pays no
                probe.
        """
        if not present:
            self.report.created.append(name)
        elif is_current is None or is_current():
            self.report.unchanged.append(name)
            return
        else:
            self.report.repaired.append(name)
        if self.write:
            apply()

    def file(
        self,
        name: str,
        path: Path,
        template: Callable[[], str],
        repair: Callable[[str], str | None] | None = None,
    ) -> None:
        """Create a file from a template, or offer it to a repair.

        Args:
            name: What to call it in the report.
            path: Where it goes. Parent directories are created, so a
                managed file never depends on an earlier item.
            template: Renders the file. A thunk rather than a string, so
                check mode renders nothing and a steady-state run renders
                only what it writes.
            repair: Receives the current text and returns the fixed text,
                or ``None`` when the file is already fine. Must be
                conservative by construction.
        """
        if not path.exists():
            self.report.created.append(name)
            if self.write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(template())
        elif repair is not None and (fixed := repair(path.read_text())) is not None:
            self.report.repaired.append(name)
            if self.write:
                path.write_text(fixed)
        else:
            self.report.unchanged.append(name)

    def blocked(self, name: str, reason: str) -> None:
        """Record an item convergence cannot complete, and why.

        Stronger than :meth:`warn`: the project is not converged, so
        ``--check`` fails rather than reporting a file that is not there.

        Args:
            name: What to call it in the report.
            reason: What the user must do, recorded as a warning.
        """
        self.report.blocked.append(name)
        self.report.warnings.append(reason)

    def warn(self, message: str) -> None:
        """Record something convergence can see but must not fix."""
        self.report.warnings.append(message)


# =============================================================================
# Convergence
# =============================================================================


def converge(directory: Path, *, write: bool = True) -> ConvergenceReport:
    """Converge a directory into an ASTRA project. Idempotent.

    Creates whatever is missing, repairs the pieces lightcone manages, and
    never overwrites a file the user owns. A directory that already holds
    an ``astra.yaml`` is adopted, not rejected.

    Args:
        directory: The project root, created if absent.
        write: False for check mode, which touches nothing — not even the
            directory — and reports what a real run would have done.

    Returns:
        What was created, repaired, left alone or blocked, plus warnings.

    Raises:
        ProjectError: If uv, git or git-annex is missing, or any of them
            fails.
    """
    directory = directory.resolve()

    require_uv()
    require_git()
    require_git_annex()

    c = _Converger(write=write)

    if write:
        directory.mkdir(parents=True, exist_ok=True)

    # `astra.scaffold` is astra's public scaffold API, the same one
    # `astra init` delegates to: it writes the spec — astra.yaml plus
    # universes/baseline.yaml, which converge as one item because the
    # baseline references the boilerplate's example decision — and nothing
    # else. It is stdlib-only and imports in milliseconds, which is why it
    # sits at module scope where the validation stack cannot.
    c.item(
        "astra.yaml",
        (directory / SPEC_FILENAME).exists(),
        lambda: create_boilerplate(directory),
    )
    _converge_uv_project(c, directory)
    c.file(
        ".gitignore",
        directory / ".gitignore",
        partial(templates.read, "gitignore.tmpl"),
        repair=templates.gitignore_repair,
    )
    _converge_dataset(c, directory)
    _converge_tracked_dir(c, directory, "data", partial(templates.read, "data-README.md.tmpl"))
    _converge_tracked_dir(
        c, directory, "results", partial(templates.read, "results-README.md.tmpl")
    )
    # The MyST report is a recommended add-on on top of the spec, not part
    # of it — which is why it is scaffolded here and not by `astra init`.
    c.file("myst.yml", directory / "myst.yml", partial(templates.read, "myst.yml.tmpl"))
    c.file(
        "index.md",
        directory / "index.md",
        lambda: templates.index_md(title=directory.name or "My Analysis"),
    )

    # Lock and sync last: they are the expensive steps, and they are
    # meaningless until pyproject.toml and .python-version exist.
    c.item(
        "uv.lock",
        (directory / "uv.lock").exists(),
        lambda: _uv(c, ["lock"], directory=directory),
        is_current=lambda: _lock_is_current(directory),
    )
    # Locking is resolution and works on the bare host in both modes; a
    # *sync* of a containerized project is the host-sync deadlock in
    # miniature — the lock's system-level dependencies (the reason the
    # project containerized at all) are not on the host, so the sync
    # fails and `--check` would report unconverged forever. The
    # environment converge in containerized mode belongs to the verbs,
    # which run it inside the image.
    if mode(directory) == "direct":
        c.item(
            ".venv",
            (directory / ".venv").exists(),
            lambda: _uv(c, _SYNC_ARGS, directory=directory),
            is_current=lambda: _env_is_current(directory),
        )

    return c.report


def require_uv() -> None:
    """Refuse early when uv is absent — it is the environment substrate.

    Raises:
        ProjectError: If uv is not on ``PATH``.
    """
    if shutil.which("uv") is None:
        raise ProjectError(
            "uv is required (the environment substrate). Install it: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )


def require_git() -> None:
    """Refuse early when git is absent.

    The one tool uv cannot install, and the only admitted exception to an
    otherwise uv-installable stack.

    Raises:
        ProjectError: If git is not on ``PATH``.
    """
    if shutil.which("git") is None:
        raise ProjectError(
            "git is required (results are versioned in the repository). "
            "Install it: https://git-scm.com/downloads"
        )


def require_git_annex() -> None:
    """Refuse early when git-annex is not reachable as git reaches it.

    Probed by the name git itself searches for: ``git annex`` is git
    finding a ``git-annex`` executable on ``PATH``, not a builtin. Every
    install channel puts one there by construction — lightcone-cli
    declares the git-annex wheel's entry points as its own, so installers
    link them beside ``lc`` — which makes this a refusal for broken
    installs only.

    Raises:
        ProjectError: If ``git-annex`` is not on ``PATH``.
    """
    if shutil.which("git-annex") is None:
        raise ProjectError(
            "git-annex is required (it stores the bytes results are made of) "
            "and is not on PATH. It installs with lc itself: "
            "`uv tool install --force lightcone-cli` repairs the install."
        )


def uv_prefix(directory: Path, *, sync: bool) -> list[str]:
    """Build the ``uv run`` hop that pins a command to a project.

    ``--locked`` makes a stale lock uv's loud error rather than a silent
    relock, and ``--project`` is explicit because uv's walk-up discovery
    is never trusted.

    Args:
        directory: The project to pin to.
        sync: True for a probe, which converges the environment it is
            about to describe. False for a recipe: the environment was
            converged before the run, and syncing per task would have
            every concurrent worker writing the same ``.venv``.

    Returns:
        The argv prefix, ending in ``--``.
    """
    selection = ["--exact"] if sync else ["--no-sync"]
    return ["uv", "run", "--locked", *selection, "--project", str(directory), "--"]


def sync(directory: Path) -> list[str]:
    """Make the host ``.venv`` match ``uv.lock``. Direct mode's converge.

    Both entry points that execute recipes converge before they start,
    rather than checking and refusing: workers pass ``--no-sync``, so
    nothing else would notice a lock edited without a sync, and a manifest
    recording an environment the recipe did not run under is the identity
    model saying something untrue. The containerized twin is
    ``container.sync``, which runs the same uv command inside the image —
    callers pick by :func:`mode`, because only they know whether an image
    has been ensured.

    Args:
        directory: The project root.

    Returns:
        Whatever uv warned about.

    Raises:
        ProjectError: If uv fails.
    """
    return _check_call(["uv", *_SYNC_ARGS, "--project", str(directory)], cwd=directory)


def _converge_uv_project(c: _Converger, directory: Path) -> None:
    """pyproject.toml + .python-version — the environment definition.

    An existing ``pyproject.toml`` is the user's: read, never edited.
    """
    c.file(
        "pyproject.toml",
        directory / "pyproject.toml",
        lambda: templates.pyproject(name=project_name(directory)),
    )
    c.file(".python-version", directory / ".python-version", templates.python_version)


def _converge_tracked_dir(
    c: _Converger, directory: Path, name: str, readme: Callable[[], str]
) -> None:
    """A directory the repository tracks, plus the README that makes it exist.

    Git carries no empty directories, so a tracked directory that starts
    empty needs a file to survive a clone. The README is that file, and it
    is also where what belongs in the directory is written down.
    """
    path = directory / name
    if path.exists() and not path.is_dir():
        c.blocked(f"{name}/", f"{name} exists but is not a directory.")
        return
    c.item(f"{name}/", path.is_dir(), path.mkdir)
    c.file(f"{name}/README.md", path / "README.md", readme)


def _converge_dataset(c: _Converger, directory: Path) -> None:
    """The repository: git for the pointers, git-annex for the bytes.

    Whether the directory *itself* holds a ``.git`` is the wrong question
    for the repository: ``lc init subdir/`` inside an existing one must not
    create a nested repository, so the check walks up. (``.git`` can be a
    file — a linked worktree or submodule — hence ``exists`` rather than
    ``is_dir``.) The annex is asked about the same way git-annex asks
    itself, so an enclosing repository that already has one is adopted.

    Then the two files that make the storage policy: ``.gitattributes``
    routes results and inputs into the annex and keeps manifests in git,
    and ``.datalad/config`` carries a dataset id — the one thing a git +
    git-annex repository lacks to *be* a DataLad dataset, so a project is
    one from birth rather than by later adoption. The id is generated
    once and never regenerated, because ``file`` writes only what is
    missing; the run record reads it back through ``dataset.dataset_id``.
    """
    c.item(".git", _in_repository(directory), lambda: dataset.init_git(directory))
    # After the item above, so a fresh project has a repository to annex.
    c.item(
        "git-annex",
        _can_ask_git(directory) and dataset.is_annexed(directory),
        lambda: dataset.init_annex(directory),
    )
    attributes = directory / ".gitattributes"
    # Read before the repair, not after: the check is on the text a repair
    # would leave behind, and check mode never writes one.
    authored = attributes.read_text() if attributes.exists() else ""
    c.file(
        ".gitattributes",
        attributes,
        partial(templates.read, "gitattributes.tmpl"),
        repair=templates.gitattributes_repair,
    )
    if misplaced := templates.gitattributes_disorder(authored):
        c.blocked(
            ".gitattributes",
            f".gitattributes would put `{misplaced}` after a line that has to "
            "come before it, and git-annex takes the last match — so results "
            "would be committed to git as plain blobs instead of reaching the "
            "annex. Convergence only ever appends, so it cannot reorder a file "
            "the user wrote. Put lightcone's lines in this order:\n  "
            + "\n  ".join(templates.entries("gitattributes.tmpl")),
        )
    c.file(
        ".datalad/config",
        directory / ".datalad" / "config",
        lambda: templates.datalad_config(dataset_id=str(uuid.uuid4())),
    )
    _converge_committable(c, directory)


def _converge_committable(c: _Converger, directory: Path) -> None:
    """Refuse to call a project converged while its outputs are unignorable.

    Results and declared inputs are committed, so an ignore rule covering
    either is not a preference — it is a project where materializing
    reports success and commits nothing, silently, because ``git add``
    skips ignored paths without a word.

    A repair is not available: ``.gitignore`` convergence only ever
    appends, deliberately, so a rule the user wrote — or one an older
    scaffold wrote before results were tracked — stays until they delete
    it. Blocked rather than warned, because it is convergence failing at
    something it is responsible for.
    """
    if not _can_ask_git(directory):
        return
    for name in ("results", "data"):
        # Asked with the trailing slash, because the rule that matters most
        # here — the `results/*` an older lc scaffold wrote — ignores the
        # directory's *contents*, and does not match the bare name at all.
        if rule := dataset.ignore_rule(directory, f"{name}/"):
            c.blocked(
                f"{name}/",
                f"{name}/ is git-ignored by `{rule}`, so nothing in it can be "
                f"committed — and {name}/ is versioned in the repository. "
                "Delete that line and run `lc init` again.",
            )


def _in_repository(directory: Path) -> bool:
    """Whether *directory* is inside a git work tree, its own or an ancestor's.

    A pure filesystem question, so it answers for a directory that does not
    exist yet — which is the whole point in check mode: ``lc init --check``
    on a new subdirectory of a repository must report that the repository
    is already there, not that one would be created.
    """
    return any((p / ".git").exists() for p in [directory, *directory.parents])


def _can_ask_git(directory: Path) -> bool:
    """Whether git can be *run* here — a stricter question than the above.

    Every git invocation needs an existing working directory, and check
    mode does not create one. Inside an enclosing repository the walk-up
    says "in a repository" for a directory that is not there yet, and
    running git in it raises ``FileNotFoundError`` out of ``Popen`` rather
    than answering anything.
    """
    return directory.is_dir() and _in_repository(directory)


def mode(directory: Path) -> Literal["direct", "containerized"]:
    """Read which execution world this project declares.

    Derived, never configured: a ``[tool.lightcone.image]`` table in
    ``pyproject.toml`` *is* the escalation to containerized mode, and
    deleting it is the way back. Presence only — what the table means is
    :mod:`~lightcone.engine.image`'s question, asked by the verbs that
    consume it, so an invalid declaration refuses there with the line at
    fault rather than here with none.

    Args:
        directory: The project root.

    Returns:
        ``"containerized"`` if the table is declared, else ``"direct"`` —
        including for a directory with no ``pyproject.toml`` yet, which is
        what lets convergence scaffold one.

    Raises:
        ProjectError: If ``pyproject.toml`` is not valid TOML.
    """
    # image.py is the table's one reader; imported lazily because it
    # imports ProjectError from here.
    from lightcone.engine import image

    return "direct" if image._table(directory) is None else "containerized"


def env_dir(directory: Path) -> Path:
    """Locate the project environment for this project's mode.

    The one spelling of where the environment lives. Direct mode syncs
    ``.venv`` on the host; containerized mode syncs ``.lightcone/venv``
    *inside* the image, against the baked interpreter — so its symlinks
    dangle on the host, deliberately, and the host ``.venv`` (if any) is
    inert. Two directories, because sharing one means every sync flips it
    between worlds and breaks whichever side is not looking.

    Args:
        directory: The project root.

    Returns:
        The environment prefix for the current mode.
    """
    if mode(directory) == "containerized":
        return directory / ".lightcone" / "venv"
    return directory / ".venv"


def project_name(directory: Path) -> str:
    """Derive a project name from a directory name.

    Args:
        directory: The project root.

    Returns:
        A PEP 503-ish name, or ``analysis`` if nothing usable remains.
    """
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", directory.name).strip("-._").lower()
    return name or "analysis"


#: What the repository itself carries of the environment. ``astra.yaml``
#: is deliberately not among them — a command can be probed in any uv
#: project, spec or no spec.
_DECLARED_FILES = ("pyproject.toml", "uv.lock")

#: …plus the built environment. ``.venv`` is the one piece that is local
#: state rather than repository content, which is the whole difference
#: between the two questions below.
_ENVIRONMENT_FILES = (*_DECLARED_FILES, ".venv")


def declared_project(directory: Path | None = None) -> Path:
    """Take a directory as a project root, needing only what git carries.

    The weaker of the two questions: it asks whether the project is
    *declared*, not whether it is built. That is what the worker entry
    point needs — a clone holds the lock and no ``.venv``, and the worker
    converges the environment for itself a moment later.

    Args:
        directory: Defaults to the working directory.

    Returns:
        The resolved project root.

    Raises:
        ProjectError: If ``pyproject.toml`` or ``uv.lock`` is absent.
    """
    return _project_root(directory, _DECLARED_FILES)


def current_project(directory: Path | None = None) -> Path:
    """Take a directory as the project root, built environment and all.

    The question every verb that runs something asks, and the stronger of
    the two: a ``.venv`` that is not there is not something these callers
    are going to create. In containerized mode the host ``.venv`` is
    inert and never required — the environment those verbs enter is
    ``.lightcone/venv``, which their own converge creates inside the
    image, so only what the repository carries is asked for.

    Args:
        directory: Defaults to the working directory.

    Returns:
        The resolved project root.

    Raises:
        ProjectError: If any of the required environment is absent.
    """
    resolved = (directory or Path.cwd()).resolve()
    if mode(resolved) == "containerized":
        return _project_root(resolved, _DECLARED_FILES)
    return _project_root(resolved, _ENVIRONMENT_FILES)


def _project_root(directory: Path | None, required: Sequence[str]) -> Path:
    """Check *directory* against *required* and resolve it.

    There is no walk-up: the directory you are in is the directory that is
    used, or it is an error.

    Raises:
        ProjectError: If anything in *required* is absent. The two ways
            that fails get different advice — a directory with no project
            markers is the wrong *place*, while one that declares a
            project but lacks a piece of the environment is the right
            place, not yet converged.
    """
    directory = (directory or Path.cwd()).resolve()
    missing = [name for name in required if not (directory / name).exists()]
    if not missing:
        return directory
    declared = (directory / "pyproject.toml").exists() or (directory / SPEC_FILENAME).exists()
    if not declared:
        raise ProjectError(
            f"{directory} is not a Lightcone project — `lc run` uses the "
            "directory it is invoked from, and there is no project here. "
            "cd to the root of one and try again."
        )
    raise ProjectError(
        f"{directory} is a Lightcone project that has not been built yet "
        f"— missing {', '.join(missing)}. Run `lc init` here first."
    )


# =============================================================================
# The external-tool seam
# =============================================================================


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """One seam for every external tool convergence invokes.

    Tests monkeypatch this, so the suite never shells out and every call is
    inspectable.

    """
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=False, env=child_env()
    )


#: The ambient ``UV_*`` variables :func:`child_env` keeps. Plumbing only —
#: where bytes come from and how fast, never *what* a sync installs: the
#: cache location (shared-filesystem hosts point it at scratch), network
#: timeouts and concurrency, TLS trust, air-gap mode, and index
#: credentials. Anything with install semantics (``UV_NO_BINARY``,
#: ``UV_PYTHON``, ``UV_INDEX_URL``, …) is dropped: the same settings are
#: hashed into ``env_version`` when a project declares them, so an ambient
#: spelling would steer a sync while every hash agrees nothing changed.
_UV_KEPT = frozenset(
    {
        "UV_CACHE_DIR",
        "UV_HTTP_TIMEOUT",
        "UV_REQUEST_TIMEOUT",
        "UV_CONCURRENT_BUILDS",
        "UV_CONCURRENT_DOWNLOADS",
        "UV_CONCURRENT_INSTALLS",
        "UV_NATIVE_TLS",
        "UV_INSECURE_HOST",
        "UV_OFFLINE",
        # uv's own recursion guard, set on every `uv run` child — lc
        # itself frequently *is* one. Dropping it disables the guard and
        # makes the scrub report uv's variable as the user's.
        "UV_RUN_RECURSION_DEPTH",
    }
)


def _uv_scrubbed(name: str) -> bool:
    """Decide whether one ambient variable is dropped by the UV scrub."""
    if not name.startswith("UV_"):
        return False
    # UV_INTERNAL__* is uv talking to its own children, never a setting.
    if name in _UV_KEPT or name.startswith("UV_INTERNAL__"):
        return False
    # Index credentials (UV_INDEX_<NAME>_USERNAME / _PASSWORD) are how a
    # private registry authenticates; the registry itself is the
    # project's `[tool.uv.index]` to declare.
    return not (
        name.startswith("UV_INDEX_") and name.endswith(("_USERNAME", "_PASSWORD"))
    )


def child_env() -> dict[str, str]:
    """Build the environment external tools run in.

    Ours, minus ``VIRTUAL_ENV`` and minus every ``UV_*`` variable outside
    the :data:`_UV_KEPT` plumbing allowlist. Every uv invocation names its
    project explicitly, so an activated environment elsewhere is never
    what we mean — and an ambient install setting would change what a
    sync installs without moving ``env_version``, which is the identity
    hole the scrub closes.

    Returns:
        The current environment without ``VIRTUAL_ENV`` or scrubbed ``UV_*``.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if k != "VIRTUAL_ENV" and not _uv_scrubbed(k)
    }


def scrubbed_uv_vars() -> list[str]:
    """Name the ambient non-empty ``UV_*`` variables the scrub drops.

    The run verbs surface these as a warning: a user whose ``UV_PYTHON``
    stopped steering a sync deserves a pointer to why. One predicate with
    :func:`child_env`, so the report can never disagree with the scrub.

    Returns:
        Sorted variable names, set and non-empty in this process.
    """
    return sorted(k for k, v in os.environ.items() if v and _uv_scrubbed(k))


def _check_call(argv: list[str], *, cwd: Path) -> list[str]:
    """Run a tool, surfacing a nonzero exit as :class:`ProjectError`.

    Nothing convergence shells out to is allowed to fail silently: a
    broken lock, or a ``git init`` that didn't happen, would surface later
    as something far more confusing. Returns the tool's own warnings.
    """
    proc = _run(argv, cwd=cwd)
    if proc.returncode != 0:
        raise ProjectError(f"`{' '.join(argv)}` failed:\n{proc.stderr.strip()}")
    return tool_warnings(proc.stderr)


def tool_warnings(stderr: str) -> list[str]:
    """Lift a tool's warnings out of its progress output.

    uv interleaves warnings with progress, so relaying the whole stream
    would bury them under a line per installed package. A warning is a
    line starting ``warning:`` plus its continuations, which uv indents by
    at least two — one space is uv's own change list (`` + pkg==1.0``).

    The one that must reach the user: when the uv cache and the project
    are on different filesystems, uv silently falls back to copying every
    package, and nothing else would say so.

    Args:
        stderr: A tool's captured stderr.

    Returns:
        One entry per warning, continuations folded in.
    """
    found: list[str] = []
    for line in stderr.splitlines():
        if line.startswith("warning:"):
            found.append(line.removeprefix("warning:").strip())
        elif found and line.startswith(("  ", "\t")) and line.strip():
            found[-1] += " " + line.strip()
    return found


def _is_current(argv: list[str], *, directory: Path) -> bool:
    """Ask uv whether an artifact still agrees with its inputs.

    A nonzero exit is the answer "no", not a failure, so unlike
    :func:`_check_call` this never raises. Both probes are uv's own
    no-write verification (confirmed read-only against uv 0.12.3), so the
    answer is uv's rather than a heuristic of ours.
    """
    return _run(["uv", *argv, "--project", str(directory)], cwd=directory).returncode == 0


def _lock_is_current(directory: Path) -> bool:
    """Whether ``uv.lock`` still agrees with ``pyproject.toml``."""
    return _is_current(["lock", "--check"], directory=directory)


def _env_is_current(directory: Path) -> bool:
    """Whether ``.venv`` still satisfies ``uv.lock``.

    Set-level, not byte-level: uv catches packages the lock requires and
    the environment lacks, but not extras installed by hand. What bounds
    what a recipe can import is the sandbox, not this probe.
    """
    return _is_current(["sync", "--locked", "--exact", "--check"], directory=directory)


# --locked so a drifted lock is an error rather than a silent relock;
# --exact because a plain sync is additive, and because it is what puts the
# environment back in agreement with the lock after anything a run did to
# it; --compile-bytecode to pay compilation once here rather than on the
# first import of every run.
_SYNC_ARGS = ["sync", "--locked", "--exact", "--compile-bytecode"]


def _uv(c: _Converger, args: list[str], *, directory: Path) -> None:
    """Run uv against *directory*, recording anything it warns about.

    Every invocation carries an explicit ``--project``: uv's own walk-up
    discovery is never trusted. Nothing about linking or caching
    is overridden — uv's defaults already share package content between
    projects — and ``--system-site-packages`` is never used, since it would
    make packages outside the lock importable, which is what the
    environment model exists to prevent.
    """
    for warning in _check_call(["uv", *args, "--project", str(directory)], cwd=directory):
        c.warn(f"uv: {warning}")
