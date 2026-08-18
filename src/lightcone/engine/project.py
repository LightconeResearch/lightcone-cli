"""Utilities to manage a Lightcone project.
"""

from __future__ import annotations

import os
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
    _converge_results(c, directory)
    # The MyST report is a recommended add-on on top of the spec, not part
    # of it — which is why it is scaffolded here and not by `astra init`.
    c.file("myst.yml", directory / "myst.yml", templates.myst_yml)
    c.file(
        "index.md",
        directory / "index.md",
        lambda: templates.index_md(title=directory.name or "My Analysis"),
    )
    _converge_git(c, directory)

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
    """Refuse early when uv is absent — it is the environment substrate.

    Shared with ``lc run``: convergence needs uv to build the environment
    and the probe needs it to enter one, and they must not drift into
    telling the user two different things about the same missing tool.
    """
    if shutil.which("uv") is None:
        raise ProjectError(
            "uv is required (the environment substrate). Install it: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )


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


def declares_system_layer(directory: Path) -> bool:
    """Whether *directory* declares a system layer — spec §1's mode rule.

    Mode is *derived, not configured*: declaring the layer **is** the
    escalation to containerized mode. Lives here rather than with the
    verb that refuses it, because the same two inputs are read by
    ``env_version`` (layer 2), the launcher's mode detection (layer 3),
    and ``lc build`` (layer 6) — and they must agree on the key name and
    on what counts as a declaration.
    """
    import tomllib

    if (directory / "Containerfile.extra").exists():
        return True
    pyproject = directory / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{pyproject} is not valid TOML: {e}") from e
    return "image" in data.get("tool", {}).get("lightcone", {})


def find_project(start: Path | None = None) -> Path:
    """The project root containing *start*, by ``astra.yaml`` walk-up.

    The spec is what makes a directory a project, so it is what
    discovery looks for — not ``pyproject.toml``, which would find any
    Python project, and not uv's own walk-up, which lc never trusts
    (spec §4: every uv invocation carries an explicit ``--project``).

    ``lc init`` is handed its directory and needs none of this; every
    verb that *runs* something does.
    """
    start = (start or Path.cwd()).resolve()
    for directory in [start, *start.parents]:
        if (directory / SPEC_FILENAME).exists():
            return directory
    raise ProjectError(
        f"no {SPEC_FILENAME} in {start} or any parent directory — "
        f"not inside an ASTRA project. Create one with `lc init`."
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
    the report and in ``--json``. Explicit flags beat ambient variables
    (spec §13); the launcher's broader ``UV_*`` scrub lands with layer 3.
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

    Set-level, not byte-level, as spec §3 accepts: uv catches packages the
    lock requires and the environment lacks, but not extras installed by
    hand, which leave ``--check`` reporting "would make no changes". What
    bounds what a recipe can import is the sandbox, not this probe.
    """
    return _is_current(["sync", "--locked", "--exact", "--check"], directory=directory)


# --locked so a drifted lock is an error rather than a silent relock;
# --exact because a plain sync is additive; --compile-bytecode because the
# environment is read-only at execution time, so a recipe cannot write .pyc
# as it imports — paying compilation once here instead of on every recipe
# run (spec §4, §6, §7).
_SYNC_ARGS = ["sync", "--locked", "--exact", "--compile-bytecode"]


def _uv(c: _Converger, args: list[str], *, directory: Path) -> None:
    """Run uv against *directory*, recording anything it warns about.

    Every invocation carries an explicit ``--project``: uv's own walk-up
    discovery is never trusted (spec §4). Nothing about linking or caching
    is overridden — uv's defaults already share package content between
    projects — and ``--system-site-packages`` is never used, since it would
    make packages outside the lock importable, which is what the
    environment model exists to prevent (spec §7, G6).
    """
    for warning in _check_call(["uv", *args, "--project", str(directory)], cwd=directory):
        c.warn(f"uv: {warning}")
