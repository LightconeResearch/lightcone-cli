"""What a project *is*, and how to converge one.

Two responsibilities, both about the project as an object rather than
about the CLI:

- **discovery** — :func:`find_root`. One rule, shared by every verb (and,
  once it lands, the launcher): the project root is the nearest ancestor,
  including the start directory, that contains an ``astra.yaml``. uv's own
  walk-up discovery is never trusted — every uv invocation downstream
  carries an explicit ``--project <root>`` (spec §4).
- **convergence** — :func:`converge`. Bring a directory to the state a
  lightcone project is defined to have: an ASTRA spec sitting on a uv
  project. Idempotent, and never destructive to files the user owns.

Nothing here knows about Click or the console: convergence returns a
:class:`ConvergenceReport` and raises :class:`ProjectError`; rendering and
exit codes are the CLI's business.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lightcone.engine import templates
from lightcone.engine.constants import DEFAULT_PYTHON

SPEC_FILENAME = "astra.yaml"


class ProjectError(Exception):
    """A project cannot be discovered, read, or converged."""


# =============================================================================
# Discovery
# =============================================================================


def find_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to the project root, or ``None``."""
    p = (start or Path.cwd()).resolve()
    for parent in [p, *p.parents]:
        if (parent / SPEC_FILENAME).is_file():
            return parent
    return None


def project_name(directory: Path) -> str:
    """A PEP 503-ish project name derived from the directory name."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", directory.name).strip("-._").lower()
    return name or "analysis"


# =============================================================================
# The convergence report
# =============================================================================


@dataclass
class ConvergenceReport:
    """What a convergence did, or — in check mode — would have done.

    ``warnings`` carries problems convergence can *see* but must not fix;
    they never affect whether the project counts as converged.
    """

    created: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def converged(self) -> bool:
        return not self.created and not self.repaired

    def as_dict(self) -> dict[str, object]:
        return {
            "converged": self.converged,
            "created": self.created,
            "repaired": self.repaired,
            "unchanged": self.unchanged,
            "warnings": self.warnings,
        }


class _Converger:
    """Decide-then-maybe-write, so check mode reuses the real decisions.

    Every item routes through :meth:`item` or :meth:`file`, which record
    the outcome first and only then apply it when ``write`` is set. That
    is what keeps ``lc init --check`` honest: it is the same code path with
    side effects switched off, not a second implementation.
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
        template: str,
        repair: Callable[[str], str | None] | None = None,
    ) -> None:
        """Create *path* from *template* if missing; else offer it to *repair*.

        ``repair`` receives the current text and returns the fixed text, or
        ``None`` when the file is already fine. Repairs must be
        conservative by construction — see :func:`_repair_gitignore`.
        """
        if not path.exists():
            self.report.created.append(name)
            if self.write:
                path.write_text(template)
        elif repair is not None and (fixed := repair(path.read_text())) is not None:
            self.report.repaired.append(name)
            if self.write:
                path.write_text(fixed)
        else:
            self.report.unchanged.append(name)

    def warn(self, message: str) -> None:
        self.report.warnings.append(message)


# =============================================================================
# Convergence
# =============================================================================


def converge(
    directory: Path,
    *,
    write: bool = True,
    git: bool = True,
    sync: bool = True,
) -> ConvergenceReport:
    """Converge *directory* into an ASTRA project. Idempotent.

    Creates whatever is missing, repairs the pieces lightcone manages, and
    never overwrites a file the user owns. A directory that already holds
    an ``astra.yaml`` is adopted, not rejected.

    With ``write=False`` nothing touches the filesystem — not even the
    directory itself — and the returned report describes what a real run
    would have done.

    Raises :class:`ProjectError` for the two states convergence must not
    paper over: an authored ``Containerfile``, and a missing or failing
    uv.
    """
    directory = directory.resolve()

    _refuse_authored_containerfile(directory)
    _require_uv()

    c = _Converger(write=write)

    if write:
        directory.mkdir(parents=True, exist_ok=True)

    _converge_spec(c, directory)
    _converge_uv_project(c, directory)
    _converge_managed_files(c, directory)
    _converge_results(c, directory)
    _converge_report_template(c, directory)

    if git:
        c.item(
            ".git",
            (directory / ".git").exists(),
            lambda: subprocess.run(["git", "init", "-q"], cwd=directory, check=False),
        )

    # Lock and sync last: they are the expensive steps, and they are
    # meaningless until pyproject.toml and .python-version exist.
    c.item("uv.lock", (directory / "uv.lock").exists(), lambda: _uv_lock(directory))
    if sync:
        c.item(".venv", (directory / ".venv").exists(), lambda: _uv_sync(directory))

    return c.report


def _refuse_authored_containerfile(directory: Path) -> None:
    """Refuse before any write.

    Images are generated from the lock, so a hand-authored Containerfile
    would be silently ignored — a half-state the user has to resolve. The
    user's own file operation is the consent to migrate; no flag can
    substitute for it (spec §8).
    """
    if (directory / "Containerfile").is_file():
        raise ProjectError(
            f"{directory}/Containerfile: images are generated from the "
            "lock — delete or rename it, then re-run `lc init`. Declare "
            "system dependencies in [tool.lightcone.image] instead."
        )


def _require_uv() -> None:
    if shutil.which("uv") is None:
        raise ProjectError(
            "uv is required (the environment substrate). Install it: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )


def _converge_spec(c: _Converger, directory: Path) -> None:
    """The ASTRA spec scaffold: astra.yaml + universes/ + src/."""
    c.item("astra.yaml", (directory / SPEC_FILENAME).exists(), lambda: _scaffold_spec(directory))


def _scaffold_spec(directory: Path) -> None:
    """Write astra's boilerplate, then make it environment-free.

    astra's ``init`` *command* refuses non-empty directories and
    overwrites ``.gitignore`` — both wrong for convergence — so this uses
    the bare scaffold API and manages ``.gitignore`` separately. The
    public ``create_boilerplate`` (LightconeResearch/astra-tools#99) is
    preferred, with the private helper as the fallback on releases that
    predate it; resolved by ``getattr`` rather than an import guard so the
    code type-checks under either.
    """
    from astra import cli as astra_cli

    create_boilerplate = getattr(astra_cli, "create_boilerplate", None)
    if create_boilerplate is not None:
        create_boilerplate(directory)
    else:
        (directory / "universes").mkdir(exist_ok=True)
        astra_cli._create_boilerplate_astra_yaml(directory)

    # The boilerplate recipes reference scripts under src/ (e.g.
    # ``python src/main.py``); astra's own init creates the directory, so
    # the scaffold must too.
    (directory / "src").mkdir(exist_ok=True)

    # ASTRA carries analysis structure only — the environment lives in
    # pyproject.toml + uv.lock. Strip the ``container:`` line the
    # boilerplate ships.
    spec_path = directory / SPEC_FILENAME
    stripped = re.sub(r"(?m)^container:.*\n?", "", spec_path.read_text(), count=1)
    spec_path.write_text(stripped)


def _converge_uv_project(c: _Converger, directory: Path) -> None:
    """pyproject.toml + .python-version — the environment definition.

    An existing ``pyproject.toml`` is the user's: read, possibly warned
    about, never edited.
    """
    pyproject_path = directory / "pyproject.toml"
    if not pyproject_path.exists():
        c.report.created.append("pyproject.toml")
        if c.write:
            pyproject_path.write_text(templates.pyproject(name=project_name(directory)))
    else:
        c.report.unchanged.append("pyproject.toml")
        if "lightcone-cli" not in pyproject_path.read_text():
            c.warn(
                "pyproject.toml does not depend on lightcone-cli — the "
                "engine should live inside the experiment's lock: "
                "`uv add lightcone-cli`."
            )

    c.file(".python-version", directory / ".python-version", f"{DEFAULT_PYTHON}\n")


def _converge_managed_files(c: _Converger, directory: Path) -> None:
    c.file(
        ".gitignore",
        directory / ".gitignore",
        templates.gitignore(),
        repair=_repair_gitignore,
    )


def _converge_results(c: _Converger, directory: Path) -> None:
    results_dir = directory / "results"
    if results_dir.exists() and not results_dir.is_dir():
        c.report.unchanged.extend(["results/", "results/README.md"])
        c.warn(
            "results exists but is not a directory; outputs cannot "
            "materialize until it is one."
        )
        return
    c.item("results/", results_dir.is_dir(), results_dir.mkdir)
    c.file("results/README.md", results_dir / "README.md", templates.results_readme())


def _converge_report_template(c: _Converger, directory: Path) -> None:
    """The MyST report. Recommended add-on on top of the spec, not part of
    it — which is why it is scaffolded here and not by ``astra init``."""
    c.file("myst.yml", directory / "myst.yml", templates.myst_yml())
    c.file(
        "index.md",
        directory / "index.md",
        templates.index_md(title=directory.name or "My Analysis"),
    )


# =============================================================================
# Repairs — append behind a marker, never rewrite
# =============================================================================


def _repair_gitignore(text: str) -> str | None:
    """Ensure every pattern lightcone manages is present, appending only
    the ones that are missing.

    Entry-wise rather than marker-based: idempotency is then structural —
    a pattern already in the file is never added again, whoever put it
    there — and a pattern introduced by a later lc release still reaches
    projects that already have a ``.gitignore``.

    The append preserves template order, which is what keeps
    ``!results/README.md`` after the ``results/*`` it negates. (A file
    holding the negation *without* ``results/*`` would end up with them
    inverted; that state can only be hand-written, and re-ordering
    someone's ignores to fix it would be the more surprising behavior.)
    """
    missing = templates.missing_gitignore_entries(text)
    if not missing:
        return None

    block = "\n".join(missing) + "\n"
    # The header is cosmetic, so add it only when it isn't already there;
    # a second repair later appends bare patterns under the first one.
    if templates.GITIGNORE_HEADER not in text:
        block = templates.GITIGNORE_HEADER + "\n" + block
    if not text.strip():
        return block
    return text.rstrip("\n") + "\n\n" + block


# =============================================================================
# The uv seam
# =============================================================================


def _run_uv(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """One seam for every uv invocation (tests monkeypatch it)."""
    return subprocess.run(["uv", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _uv(args: list[str], *, directory: Path, what: str) -> None:
    """Run uv, surfacing a failure as :class:`ProjectError`.

    Every invocation carries an explicit ``--project``: uv's native
    walk-up discovery is never trusted (spec §4). A failure surfaces here
    with uv's own stderr, because a silently broken lock would fail every
    later verb more confusingly.
    """
    proc = _run_uv([*args, "--project", str(directory)], cwd=directory)
    if proc.returncode != 0:
        raise ProjectError(f"`uv {what}` failed:\n{proc.stderr.strip()}")


def _uv_lock(directory: Path) -> None:
    _uv(["lock"], directory=directory, what="lock")


def _uv_sync(directory: Path) -> None:
    """Converge the environment, with the flags the spec fixes.

    ``--locked`` so a drifted lock is an error rather than a silent
    relock; ``--exact`` because a plain sync is additive; and
    ``--compile-bytecode`` because the environment is read-only at
    execution time (spec §4, §6).
    """
    _uv(
        ["sync", "--locked", "--exact", "--compile-bytecode"],
        directory=directory,
        what="sync",
    )
