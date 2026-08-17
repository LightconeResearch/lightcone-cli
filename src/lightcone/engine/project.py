"""What a project *is*, and how to converge one.

:func:`converge` brings a directory to the state a lightcone project is
defined to have: an ASTRA spec sitting on a uv project. Idempotent, and
never destructive to files the user owns.

Nothing here knows about Click or the console: convergence returns a
:class:`ConvergenceReport` and raises :class:`ProjectError`; rendering and
exit codes are the CLI's business.

Project *discovery* — the ``astra.yaml`` walk-up that finds a root from a
working directory — arrives with the launcher, the first thing that needs
it. ``lc init`` is handed its directory explicitly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lightcone.engine import templates

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

    def item(self, name: str, present: bool, apply: Callable[[], object]) -> None:
        """Converge something whose *presence* is the whole question."""
        if present:
            self.report.unchanged.append(name)
        else:
            self.report.created.append(name)
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

    def derived(
        self,
        name: str,
        path: Path,
        *,
        is_current: Callable[[], bool],
        apply: Callable[[], object],
    ) -> None:
        """Converge a *derived* artifact — one whose agreement with its
        inputs, not its mere existence, is the question.

        ``uv.lock`` and ``.venv`` are derived: a lock that no longer matches
        ``pyproject.toml``, or an environment that no longer matches the
        lock, is exactly as unconverged as a missing one. Existence alone
        would make convergence a no-op on drift — and layer 3's launcher
        converges on every invocation precisely to guarantee the environment
        matches the lock.

        ``is_current`` is only consulted when the artifact exists, so a
        fresh project pays no probe, and the created/repaired distinction
        falls out of the same check.
        """
        if not path.exists():
            self.report.created.append(name)
        elif is_current():
            self.report.unchanged.append(name)
            return
        else:
            self.report.repaired.append(name)
        if self.write:
            apply()

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
    directory = directory.resolve()

    _require_uv()

    c = _Converger(write=write)

    if write:
        directory.mkdir(parents=True, exist_ok=True)

    # The spec is one item, keyed on astra.yaml: astra treats it and
    # universes/baseline.yaml as a unit, because the baseline references the
    # boilerplate's example decision and must never land beside a
    # user-authored spec. Nothing else about the layout is converged —
    # `src/` and an empty `universes/` are directories git cannot track, so
    # converging them would report drift on every fresh clone; where
    # analysis code lives is the user's layout, not ours (astra-tools#100).
    c.item(
        "astra.yaml",
        (directory / SPEC_FILENAME).exists(),
        lambda: _scaffold_spec(directory),
    )
    _converge_uv_project(c, directory)
    c.file(
        ".gitignore",
        directory / ".gitignore",
        templates.gitignore,
        repair=templates.gitignore_repair,
    )
    _converge_results(c, directory)
    _converge_report_template(c, directory)
    _converge_git(c, directory)

    # Lock and sync last: they are the expensive steps, and they are
    # meaningless until pyproject.toml and .python-version exist.
    c.derived(
        "uv.lock",
        directory / "uv.lock",
        is_current=lambda: _lock_is_current(directory),
        apply=lambda: _uv_lock(c, directory),
    )
    c.derived(
        ".venv",
        directory / ".venv",
        is_current=lambda: _env_is_current(directory),
        apply=lambda: _uv_sync(c, directory),
    )

    return c.report


def _require_uv() -> None:
    if shutil.which("uv") is None:
        raise ProjectError(
            "uv is required (the environment substrate). Install it: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )


def _scaffold_spec(directory: Path) -> None:
    """Write astra's boilerplate spec: ``astra.yaml`` + ``universes/``.

    ``create_boilerplate`` is astra's public scaffold API, split out
    (astra-tools#99) so downstream tools can scaffold into existing
    directories under their own conventions: it writes the spec and nothing
    else — no ``.gitignore``, no git init, no emptiness checks. ``astra
    init`` delegates to the same function, so this is the shared path, not
    a back door.

    Its boilerplate carries a ``container:`` key. **lightcone-cli ignores
    the ASTRA ``container:`` directive entirely** — not stripped, not
    validated, not migrated. The environment is ``pyproject.toml`` +
    ``uv.lock``, and no code path here reads that key.
    """
    from astra.cli import create_boilerplate

    create_boilerplate(directory)


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


def _converge_results(c: _Converger, directory: Path) -> None:
    results_dir = directory / "results"
    if results_dir.exists() and not results_dir.is_dir():
        c.blocked(
            "results/",
            "results exists but is not a directory; outputs cannot "
            "materialize until it is one.",
        )
        return
    c.item("results/", results_dir.is_dir(), results_dir.mkdir)
    c.file("results/README.md", results_dir / "README.md", templates.results_readme)


def _converge_report_template(c: _Converger, directory: Path) -> None:
    """The MyST report. Recommended add-on on top of the spec, not part of
    it — which is why it is scaffolded here and not by ``astra init``."""
    c.file("myst.yml", directory / "myst.yml", templates.myst_yml)
    c.file(
        "index.md",
        directory / "index.md",
        lambda: templates.index_md(title=directory.name or "My Analysis"),
    )


def _converge_git(c: _Converger, directory: Path) -> None:
    """``git init``, unless the project is already under version control.

    Whether the directory *itself* holds a ``.git`` is the wrong question:
    ``lc init subdir/`` inside an existing repository must not create a
    nested one, so the check walks up. (``.git`` can be a file — a linked
    worktree or submodule — hence ``exists`` rather than ``is_dir``.)

    git is optional, unlike uv: a project without it is perfectly valid, so
    an absent git is silently nothing to converge rather than a warning.
    """
    if shutil.which("git") is None:
        return
    already = any((p / ".git").exists() for p in [directory, *directory.parents])
    c.item(".git", already, lambda: _check_call(["git", "init", "-q"], cwd=directory))


def project_name(directory: Path) -> str:
    """A PEP 503-ish project name derived from the directory name."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", directory.name).strip("-._").lower()
    return name or "analysis"


# =============================================================================
# The external-tool seam
# =============================================================================


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """One seam for every external tool convergence invokes.

    Tests monkeypatch this, so the suite never shells out and every call is
    inspectable.
    """
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


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
    a line starting ``warning:`` plus any indented continuation.

    Worth relaying at all because of one warning in particular: when the uv
    cache and the project are on different filesystems, uv cannot link and
    silently falls back to copying every package. That is the difference
    between a project costing a few MB and costing the whole environment
    again (measured: ~148 MB of a 216 MB environment is shared when linking
    works), and nothing else would tell the user.
    """
    found: list[str] = []
    for line in stderr.splitlines():
        if line.startswith("warning:"):
            found.append(line.removeprefix("warning:").strip())
        elif found and line.startswith((" ", "\t")) and line.strip():
            found[-1] += " " + line.strip()
    return found


def _probe(argv: list[str], *, cwd: Path) -> bool:
    """Ask a tool a yes/no question.

    A nonzero exit is the answer "no", not a failure — so unlike
    :func:`_check_call` this never raises. Whatever the tool wrote to stderr
    is its explanation of the "no", which the caller is about to act on
    anyway.
    """
    return _run(argv, cwd=cwd).returncode == 0


def _lock_is_current(directory: Path) -> bool:
    """Whether ``uv.lock`` still agrees with ``pyproject.toml``.

    uv's own no-write verification, so the answer is uv's rather than a
    heuristic of ours (verified read-only against uv 0.12.3).
    """
    return _probe(["uv", "lock", "--check", "--project", str(directory)], cwd=directory)


def _env_is_current(directory: Path) -> bool:
    """Whether ``.venv`` still satisfies ``uv.lock``.

    **Honest limitation** (uv 0.12.3, measured): this catches packages the
    lock requires and the environment lacks, but *not* extras — a package
    installed by hand into the environment leaves ``--check`` reporting
    "would make no changes", even though a real ``--exact`` sync would
    remove it. So a hand-polluted environment still reads as current. The
    design already accepts set-level rather than byte-level environment
    checks (spec §3); what ultimately bounds what a recipe can import is
    the sandbox, not this probe.
    """
    argv = ["uv", "sync", "--locked", "--exact", "--check", "--project", str(directory)]
    return _probe(argv, cwd=directory)


def _uv(c: _Converger, args: list[str], *, directory: Path) -> None:
    """Run uv against *directory*, recording anything it warns about.

    Every invocation carries an explicit ``--project``: uv's native
    walk-up discovery is never trusted (spec §4).

    Nothing about linking or caching is overridden. uv's defaults already
    share package content between projects — it clones (copy-on-write) or
    hard links out of a global cache — and the docs discourage forcing
    ``symlink`` mode, which would couple every environment to the cache's
    survival. `--system-site-packages` is likewise never used: it would
    make packages outside the lock importable, which is precisely what the
    environment model exists to prevent (spec §7 G6).
    """
    for warning in _check_call(["uv", *args, "--project", str(directory)], cwd=directory):
        c.warn(f"uv: {warning}")


def _uv_lock(c: _Converger, directory: Path) -> None:
    _uv(c, ["lock"], directory=directory)


def _uv_sync(c: _Converger, directory: Path) -> None:
    """Converge the environment, with the flags the spec fixes.

    ``--locked`` so a drifted lock is an error rather than a silent relock;
    ``--exact`` because a plain sync is additive; and ``--compile-bytecode``
    because the environment is read-only at execution time, so a recipe
    cannot write ``.pyc`` files as it imports (spec §4, §6, §7).

    ``--compile-bytecode`` is the environment's one genuinely per-project
    cost: bytecode is generated into the venv rather than linked from the
    cache, ~55 MB of a 216 MB environment here. The alternative is paying
    compilation on every recipe run instead, since the sandbox denies the
    write — so this is a deliberate trade, not an oversight.
    """
    _uv(c, ["sync", "--locked", "--exact", "--compile-bytecode"], directory=directory)
