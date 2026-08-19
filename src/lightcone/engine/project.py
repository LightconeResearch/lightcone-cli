"""Utilities to manage a Lightcone project.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
        ``False``. It reports what convergence *found* — which is the
        question check mode asks — not "the project is now good".
        """
        return not self.created and not self.repaired and not self.blocked

    def as_dict(self) -> dict[str, object]:
        """The report as JSON-ready data, ``converged`` first.

        Built from :func:`dataclasses.asdict` so a field added to the
        report cannot silently go missing from ``lc init --json``, which is
        the agent-facing contract.
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

        ``is_current`` makes it a *derived* artifact instead, one whose
        agreement with its inputs is the question: a ``uv.lock`` that no
        longer matches ``pyproject.toml``, or a ``.venv`` that no longer
        matches the lock, is exactly as unconverged as a missing one, and
        reports as ``repaired``. It is consulted only when the item is
        present, so a fresh project pays no probe.
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
        """Create *path* from *template* if missing; else offer it to *repair*.

        *template* is a thunk rather than a string, so check mode renders
        nothing at all and a steady-state run renders only the files it
        actually writes. Parent directories are created, so a managed file
        never depends on an earlier item having made its directory first.

        ``repair`` receives the current text and returns the fixed text, or
        ``None`` when the file is already fine. Repairs must be
        conservative by construction — see
        :func:`~lightcone.engine.templates.gitignore_repair`.
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
        ``--check`` fails instead of reporting a file that isn't there.
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
    """Converge *directory* into an ASTRA project. Idempotent.

    Creates whatever is missing, repairs the pieces lightcone manages, and
    never overwrites a file the user owns. A directory that already holds
    an ``astra.yaml`` is adopted, not rejected.

    With ``write=False`` nothing touches the filesystem — not even the
    directory itself — and the returned report describes what a real run
    would have done.

    Raises :class:`ProjectError` if uv is missing or fails — it is the
    environment substrate, so there is no useful project without it.
    """
    # Imported here rather than at module scope because `astra.cli` costs
    # ~0.5 s to import — Click, Rich, and the validation stack, which pulls
    # linkml_runtime. astra-tools#102 moves the scaffold to a stdlib-only
    # `astra.scaffold`; once that lands this can go back up top.
    from astra.cli import create_boilerplate

    directory = directory.resolve()

    require_uv()
    require_git()
    require_git_annex()

    c = _Converger(write=write)

    if write:
        directory.mkdir(parents=True, exist_ok=True)

    # `create_boilerplate` is astra's public scaffold API, the same one
    # `astra init` delegates to: it writes the spec — astra.yaml plus
    # universes/baseline.yaml, which converge as one item because the
    # baseline references the boilerplate's example decision — and nothing
    # else.
    c.item(
        "astra.yaml",
        (directory / SPEC_FILENAME).exists(),
        lambda: create_boilerplate(directory),
    )
    _converge_uv_project(c, directory)
    c.file(
        ".gitignore",
        directory / ".gitignore",
        templates.gitignore,
        repair=templates.gitignore_repair,
    )
    _converge_dataset(c, directory)
    _converge_tracked_dir(c, directory, "data", templates.data_readme)
    _converge_tracked_dir(c, directory, "results", templates.results_readme)
    # The MyST report is a recommended add-on on top of the spec, not part
    # of it — which is why it is scaffolded here and not by `astra init`.
    c.file("myst.yml", directory / "myst.yml", templates.myst_yml)
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
    c.item(
        ".venv",
        (directory / ".venv").exists(),
        lambda: _uv(c, _SYNC_ARGS, directory=directory),
        is_current=lambda: _env_is_current(directory),
    )

    return c.report


def require_uv() -> None:
    """Refuse early when uv is absent — it is the environment substrate."""
    if shutil.which("uv") is None:
        raise ProjectError(
            "uv is required (the environment substrate). Install it: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )


def require_git() -> None:
    """Refuse early when git is absent.

    git is the one tool uv cannot install, and the only admitted exception
    to an otherwise uv-installable stack. Results are versioned in the
    repository, so there is no useful project without it.
    """
    if shutil.which("git") is None:
        raise ProjectError(
            "git is required (results are versioned in the repository). "
            "Install it: https://git-scm.com/downloads"
        )


def require_git_annex() -> None:
    """Refuse early when git-annex is not reachable as git reaches it.

    Probed after :func:`~lightcone.engine.dataset.put_our_bin_first`, and
    by the name git itself searches for: ``git annex`` is git finding a
    ``git-annex`` executable on ``PATH``, not a builtin.
    """
    dataset.put_our_bin_first()
    if shutil.which("git-annex") is None:
        raise ProjectError(
            "git-annex is required (it stores the bytes results are made of) "
            "and is not on PATH. It ships as a wheel and installs with the "
            "engine: `uv sync` in the project, or reinstall lightcone-cli."
        )


def uv_prefix(directory: Path, *, sync: bool) -> list[str]:
    """``uv run``, pinned to *directory* and refusing to drift.

    ``--locked`` makes a stale lock uv's loud error rather than a silent
    relock, and the explicit ``--project`` is there because uv's own
    walk-up discovery is never trusted.

    *sync* is the only thing callers disagree about. A probe syncs, and
    ``--exact`` keeps a previously-installed extra out of the environment
    it is about to describe. A recipe does not: the environment was
    converged before the run started, and syncing per task would have
    every concurrent worker writing the same ``.venv``.
    """
    selection = ["--exact"] if sync else ["--no-sync"]
    return ["uv", "run", "--locked", *selection, "--project", str(directory), "--"]


def sync(directory: Path) -> list[str]:
    """Make ``.venv`` match ``uv.lock``. Returns whatever uv warned about.

    A run calls this before it starts rather than checking and refusing:
    workers pass ``--no-sync`` so nothing else would notice a lock edited
    without a sync, and a manifest recording an environment the recipe did
    not run under is the identity model saying something untrue. Doing it
    is both shorter than reporting it and impossible to ignore.
    """
    return _check_call(["uv", *_SYNC_ARGS, "--project", str(directory)], cwd=directory)


def _converge_uv_project(c: _Converger, directory: Path) -> None:
    """pyproject.toml + .python-version — the environment definition.

    An existing ``pyproject.toml`` is the user's: read, possibly warned
    about, never edited. The warning is decided against the *pre-existing*
    file, so one we just wrote — which always names the engine — can never
    trigger it.
    """
    pyproject_path = directory / "pyproject.toml"
    adopted = pyproject_path.exists()
    c.file(
        "pyproject.toml",
        pyproject_path,
        lambda: templates.pyproject(name=project_name(directory)),
    )
    if adopted and "lightcone-cli" not in pyproject_path.read_text():
        c.warn(
            "pyproject.toml does not depend on lightcone-cli — the "
            "engine should live inside the experiment's lock: "
            "`uv add lightcone-cli`."
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
    one from birth rather than by later adoption. Neither is read back by
    lc; the id is generated once and never regenerated, because ``file``
    writes only what is missing.
    """
    c.item(".git", _in_repository(directory), lambda: dataset.init_git(directory))
    # After the item above, so a fresh project has a repository to annex.
    c.item(
        "git-annex",
        _can_ask_git(directory) and dataset.is_annexed(directory),
        lambda: dataset.init_annex(directory),
    )
    c.file(
        ".gitattributes",
        directory / ".gitattributes",
        templates.gitattributes,
        repair=templates.gitattributes_repair,
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


def project_name(directory: Path) -> str:
    """A PEP 503-ish project name derived from the directory name."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", directory.name).strip("-._").lower()
    return name or "analysis"


#: What makes a directory a project root: the environment ``lc run``
#: enters. ``astra.yaml`` is deliberately not among them — a command can
#: be probed in any uv project, spec or no spec.
_ENVIRONMENT_FILES = ("pyproject.toml", "uv.lock", ".venv")


def current_project(directory: Path | None = None) -> Path:
    """*directory* (default: the working directory), taken as the project root.

    ``lc run`` assumes it is invoked from the root, so the only check is
    that the environment is actually there. There is no walk-up: the
    directory you are in is the directory that is used, or it is an
    error.

    The two ways that fails are different mistakes and get different
    advice. A directory with no project markers at all is the wrong
    *place* — the answer is to go to the right one, and telling someone
    standing in ``$HOME`` to run ``lc init`` there would be telling them
    to scaffold a project in their home directory. A directory that
    holds a ``pyproject.toml`` or an ``astra.yaml`` but lacks the built
    environment is the right place, not yet converged — a fresh clone is
    exactly this, since git carries no ``.venv`` — and there ``lc init``
    is the whole answer.
    """
    directory = (directory or Path.cwd()).resolve()
    missing = [name for name in _ENVIRONMENT_FILES if not (directory / name).exists()]
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


def child_env() -> dict[str, str]:
    """The environment external tools run in: ours, minus ``VIRTUAL_ENV``.

    Every uv invocation names its project explicitly, so an activated
    environment elsewhere is never what we mean — uv agrees, ignoring it and
    warning that it did, once per invocation, which would otherwise land in
    the report and in ``--json``. Explicit flags beat ambient variables.
    """
    return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


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
    """A tool's warnings, lifted out of its progress output.

    uv interleaves warnings with progress on stderr, so relaying the whole
    stream would bury them under a line per installed package. A warning is
    a line starting ``warning:`` plus its continuations, which uv aligns
    under the 9-character ``warning: `` prefix. The two-space floor is what
    separates those from uv's own change list (`` + pkg==1.0``), which is
    indented by exactly one — folding those in swallowed the whole install
    list into the warning text.

    The one that has to reach the user: when the uv cache and the project
    are on different filesystems, uv cannot link and silently falls back to
    copying every package — most of an environment stops being shared, and
    nothing else would say so.
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

    Set-level, not byte-level: uv catches packages the
    lock requires and the environment lacks, but not extras installed by
    hand, which leave ``--check`` reporting "would make no changes". What
    bounds what a recipe can import is the sandbox, not this probe.
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
