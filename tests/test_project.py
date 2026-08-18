"""Tests for `lightcone.engine.project` — how a directory converges into a
project."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import probes, uv_calls

from lightcone.engine import templates
from lightcone.engine.project import (
    SPEC_FILENAME,
    ConvergenceReport,
    ProjectError,
    converge,
    project_name,
)

#: Every path convergence is responsible for.
SCAFFOLD = (
    "astra.yaml",
    "universes/baseline.yaml",
    "pyproject.toml",
    ".python-version",
    ".gitignore",
    "results/README.md",
    "myst.yml",
    "index.md",
    "uv.lock",
    ".venv",
)


# ---- naming ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("My Analysis", "my-analysis"),
        ("SN_Cosmology", "sn_cosmology"),
        ("2024.results", "2024.results"),
        ("...", "analysis"),
    ],
)
def test_project_name_normalizes_the_directory(directory: str, expected: str) -> None:
    assert project_name(Path("/tmp") / directory) == expected


# ---- the report -----------------------------------------------------------


def test_report_is_converged_only_when_nothing_changed() -> None:
    assert ConvergenceReport().converged
    assert ConvergenceReport(unchanged=["astra.yaml"]).converged
    assert not ConvergenceReport(created=["astra.yaml"]).converged
    assert not ConvergenceReport(repaired=[".gitignore"]).converged


def test_warnings_are_advisory_but_blocked_items_are_not() -> None:
    """Warnings are things convergence can see but must not fix, so they
    must not fail `--check`. A blocked item is different: something
    convergence is responsible for is absent."""
    assert ConvergenceReport(unchanged=["astra.yaml"], warnings=["heads up"]).converged
    assert not ConvergenceReport(unchanged=["astra.yaml"], blocked=["results/"]).converged


def test_as_dict_carries_every_field() -> None:
    """`--json` is the agent-facing contract, so it is built from the
    dataclass rather than a hand-written field list."""
    payload = ConvergenceReport(created=["astra.yaml"]).as_dict()
    assert payload["converged"] is False
    assert payload["created"] == ["astra.yaml"]
    assert set(payload) == {
        "converged",
        "created",
        "repaired",
        "unchanged",
        "blocked",
        "warnings",
    }


# ---- the scaffold ---------------------------------------------------------


def test_converge_creates_the_whole_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    report = converge(project)

    assert not report.converged
    for rel in SCAFFOLD:
        assert (project / rel).exists(), f"missing {rel}"


def test_converge_writes_the_templates_verbatim(tmp_path: Path) -> None:
    """Convergence's claim is "the file written is the template, rendered
    for this directory" — the content itself is `test_templates.py`'s."""
    project = tmp_path / "proj"
    converge(project)

    assert (project / "pyproject.toml").read_text() == templates.pyproject(name="proj")
    assert (project / ".python-version").read_text() == templates.python_version()
    assert (project / ".gitignore").read_text() == templates.gitignore()
    assert (project / "results/README.md").read_text() == templates.results_readme()
    assert (project / "myst.yml").read_text() == templates.myst_yml()
    assert (project / "index.md").read_text() == templates.index_md(title="proj")


def test_converge_scaffolds_no_environment_escalation(tmp_path: Path) -> None:
    """Containerized mode is *derived* from a declaration the user makes, so
    the scaffold never writes one. What astra's boilerplate puts
    in `astra.yaml` is astra's business — reconciling that with the
    environment model belongs to the environment layer."""
    project = tmp_path / "proj"
    converge(project)

    assert not (project / "Containerfile").exists()
    assert "[tool.lightcone.image]" not in (project / "pyproject.toml").read_text()


def test_converge_scaffolds_no_src_directory(tmp_path: Path) -> None:
    """astra stopped creating it (astra-tools#100) and so do we: the
    boilerplate's `python src/main.py` is a TODO placeholder, and git drops
    the empty directory from every clone anyway."""
    project = tmp_path / "proj"
    converge(project)
    assert not (project / "src").exists()


def test_converge_writes_no_agent_notes(tmp_path: Path) -> None:
    """`AGENTS.md` is deliberately not scaffolded — see the deviation note
    in CLAUDE.md."""
    project = tmp_path / "proj"
    converge(project)
    assert not (project / "AGENTS.md").exists()


# ---- idempotency and adoption --------------------------------------------


def test_converge_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    converge(project)
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}

    report = converge(project)
    assert report.converged
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before


def test_converge_adopts_an_existing_spec_without_touching_it(tmp_path: Path) -> None:
    """A directory that already holds an astra.yaml is adopted, not
    rejected — and the user's own file is never overwritten."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / SPEC_FILENAME).write_text('name: "mine"\n')

    report = converge(project)
    assert (project / SPEC_FILENAME).read_text() == 'name: "mine"\n'
    assert "astra.yaml" in report.unchanged
    assert (project / "pyproject.toml").exists()


def test_adoption_adds_no_layout_of_its_own(tmp_path: Path) -> None:
    """Neither `src/` nor an empty `universes/` is created beside a
    user-authored spec: git cannot track an empty directory, so converging
    one would report drift on every fresh clone. Where analysis code lives
    is the user's layout (astra-tools#100), and universes are discovered by
    glob — a missing directory yields no universes, not an error."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / SPEC_FILENAME).write_text('name: "mine"\n')

    converge(project)
    assert not (project / "src").exists()
    assert not (project / "universes").exists()
    assert converge(project).converged


def test_a_clone_of_a_converged_project_is_converged(tmp_path: Path) -> None:
    """The property the removed `src/` item broke: everything convergence
    tracks has to be something git can carry, or a fresh clone reports
    drift forever. `.venv` is exempt — it is git-ignored and rebuilt."""
    project = tmp_path / "proj"
    converge(project)

    # What a clone carries: tracked files and a repo — no empty directories,
    # and no .venv.
    clone = tmp_path / "clone"
    for src in project.rglob("*"):
        if src.is_file() and not {".venv", ".git"} & set(src.parts):
            dst = clone / src.relative_to(project)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
    (clone / ".git").mkdir()

    report = converge(clone, write=False)
    assert report.created == [".venv"], report.created


def test_converge_repairs_a_missing_piece(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    converge(project)
    (project / "myst.yml").unlink()

    report = converge(project)
    assert report.created == ["myst.yml"]
    assert (project / "myst.yml").exists()


def test_converge_recreates_a_nested_managed_file(tmp_path: Path) -> None:
    """`results/README.md` must not depend on an earlier item having made
    its directory — `file()` creates parents itself."""
    project = tmp_path / "proj"
    converge(project)
    shutil.rmtree(project / "results")

    converge(project)
    assert (project / "results" / "README.md").exists()


# ---- .gitignore ----------------------------------------------------------


def test_gitignore_repair_preserves_user_content(tmp_path: Path) -> None:
    """A repair only ever appends: the user's own ignores survive intact."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("mine.txt\nbuild/\n")

    report = converge(project)
    assert ".gitignore" in report.repaired

    text = (project / ".gitignore").read_text()
    assert text.startswith("mine.txt\nbuild/\n")
    assert templates.missing_gitignore_entries(text) == []


def test_gitignore_repair_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("mine.txt\n")

    converge(project)
    once = (project / ".gitignore").read_text()
    report = converge(project)

    assert ".gitignore" in report.unchanged
    assert (project / ".gitignore").read_text() == once


# ---- write=False (check mode) --------------------------------------------


def test_check_mode_writes_nothing_at_all(tmp_path: Path) -> None:
    """Not even the project directory — a drift report must be free of
    side effects."""
    project = tmp_path / "proj"
    report = converge(project, write=False)

    assert not report.converged
    assert "astra.yaml" in report.created
    assert not project.exists()


def test_check_mode_agrees_with_a_real_run(tmp_path: Path) -> None:
    """Check mode is the same decisions with side effects off, so its
    report must match what a real run reports."""
    project = tmp_path / "proj"
    dry = converge(project, write=False)
    wet = converge(project)

    assert dry.as_dict() == wet.as_dict()
    assert converge(project, write=False).converged


# ---- warnings and blocked items ------------------------------------------


def test_warns_when_pyproject_lacks_the_engine(tmp_path: Path) -> None:
    """The engine belongs inside the experiment's lock; a pyproject we
    didn't write is the user's, so warn rather than edit."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "mine"\nversion = "0"\n')

    report = converge(project)
    assert any("does not depend on lightcone-cli" in w for w in report.warnings)
    assert 'name = "mine"' in (project / "pyproject.toml").read_text()


def test_no_engine_warning_for_a_pyproject_we_wrote(tmp_path: Path) -> None:
    """The warning is decided against the pre-existing file, so a scaffolded
    one — which always names the engine — can never trigger it."""
    report = converge(tmp_path / "proj")
    assert report.warnings == []


def test_results_that_is_not_a_directory_blocks_convergence(tmp_path: Path) -> None:
    """Reporting `results/README.md` as unchanged when it cannot exist would
    let `--check --json` call a broken project converged."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "results").write_text("not a directory\n")

    report = converge(project)
    assert report.blocked == ["results/"]
    assert any("not a directory" in w for w in report.warnings)
    assert "results/README.md" not in report.unchanged
    assert (project / "results").read_text() == "not a directory\n"

    # And it stays unconverged on re-run, rather than settling into a lie.
    assert not converge(project).converged


# ---- git ------------------------------------------------------------------


def test_initializes_a_repository(tmp_path: Path, tools: list[list[str]]) -> None:
    project = tmp_path / "proj"
    report = converge(project)

    assert ["git", "init", "-q"] in tools
    assert ".git" in report.created


def test_does_not_nest_a_repository_inside_one(tmp_path: Path, tools: list[list[str]]) -> None:
    """`lc init subdir/` inside an existing repository must not create a
    nested one — the check walks up, it doesn't just look in the directory."""
    (tmp_path / ".git").mkdir()
    report = converge(tmp_path / "nested" / "proj")

    assert not any(c[0] == "git" for c in tools)
    assert ".git" in report.unchanged
    assert not (tmp_path / "nested" / "proj" / ".git").exists()


def test_a_linked_worktree_counts_as_version_controlled(tmp_path: Path) -> None:
    """In a linked worktree or submodule `.git` is a *file*, not a
    directory."""
    (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    assert ".git" in converge(tmp_path / "proj").unchanged


def test_git_absent_is_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tools: list[list[str]]
) -> None:
    """git is optional, unlike uv: a project without it is valid, so an
    absent git is nothing to converge rather than a warning."""
    from lightcone.engine import project as project_mod

    monkeypatch.setattr(
        project_mod.shutil,
        "which",
        lambda name, path=None: None if name == "git" else f"/usr/bin/{name}",
    )
    report = converge(tmp_path / "proj")

    assert not any(c[0] == "git" for c in tools)
    assert ".git" not in report.created + report.unchanged
    assert report.warnings == []


def test_surfaces_a_git_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing convergence shells out to gets to fail silently — a `.git`
    reported as created but never made is the kind of lie the report exists
    to prevent."""
    from unittest.mock import MagicMock

    from lightcone.engine import project as project_mod

    def fake_run(argv: list[str], *, cwd: Path) -> MagicMock:
        if argv[0] == "git":
            return MagicMock(returncode=128, stdout="", stderr="fatal: cannot mkdir")
        if argv[:2] == ["uv", "lock"]:
            (cwd / "uv.lock").write_text("version = 1\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(project_mod, "_run", fake_run)
    with pytest.raises(ProjectError, match="cannot mkdir"):
        converge(tmp_path / "proj")


# ---- refusals -------------------------------------------------------------


def test_requires_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lightcone.engine import project as project_mod

    monkeypatch.setattr(project_mod.shutil, "which", lambda name, path=None: None)
    with pytest.raises(ProjectError, match="uv is required"):
        converge(tmp_path / "proj")


# ---- the uv seam ----------------------------------------------------------


def test_locks_then_syncs_exactly(tmp_path: Path, tools: list[list[str]]) -> None:
    """Converge once, with the flags the spec fixes: ``--locked --exact``
    (no drift, no additive sync) and ``--compile-bytecode``."""
    project = tmp_path / "proj"
    converge(project)

    lock, sync = uv_calls(tools)
    assert lock[0] == "lock"
    assert sync[0] == "sync"
    for flag in ("--locked", "--exact", "--compile-bytecode"):
        assert flag in sync
    # uv's own walk-up discovery is never trusted.
    assert lock[-2:] == ["--project", str(project.resolve())]
    assert sync[-2:] == ["--project", str(project.resolve())]


def test_a_fresh_project_is_not_probed(tmp_path: Path, tools: list[list[str]]) -> None:
    """Nothing exists, so nothing needs asking — the created/repaired split
    comes from existence, and only an existing artifact costs a probe."""
    converge(tmp_path / "proj")
    assert probes(tools) == []


def test_a_converged_project_is_verified_not_assumed(
    tmp_path: Path, tools: list[list[str]]
) -> None:
    """The whole point: a second run asks uv whether the lock and the
    environment still agree with their inputs, rather than seeing two paths
    and declaring victory."""
    project = tmp_path / "proj"
    converge(project)
    tools.clear()

    report = converge(project)
    assert report.converged
    assert [p[0] for p in probes(tools)] == ["lock", "sync"]
    # Verification only — the mutating forms never ran.
    assert all("--check" in c for c in uv_calls(tools))


def test_check_mode_only_probes(tmp_path: Path, tools: list[list[str]]) -> None:
    """Check mode may ask uv questions — the probes are read-only (verified
    against uv 0.12.3) — but must never run a mutating form."""
    project = tmp_path / "proj"
    converge(project)
    tools.clear()

    converge(project, write=False)
    assert uv_calls(tools), "expected the probes to run"
    assert all("--check" in c for c in uv_calls(tools))


def test_check_mode_on_a_fresh_project_runs_nothing(
    tmp_path: Path, tools: list[list[str]]
) -> None:
    converge(tmp_path / "proj", write=False)
    assert tools == []


def test_a_stale_lock_is_repaired_not_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Converging by existence made this a no-op: `uv.lock` and `.venv` are
    *derived*, so a lock that no longer matches `pyproject.toml` is exactly
    as unconverged as a missing one."""
    from unittest.mock import MagicMock

    from lightcone.engine import project as project_mod

    project = tmp_path / "proj"
    converge(project)

    # uv now reports the lock as stale; the environment still matches it.
    real_run = project_mod._run

    def drifted(argv: list[str], *, cwd: Path) -> MagicMock:
        if argv[:3] == ["uv", "lock", "--check"]:
            return MagicMock(returncode=1, stdout="", stderr="")
        return real_run(argv, cwd=cwd)  # type: ignore[no-any-return]

    monkeypatch.setattr(project_mod, "_run", drifted)

    report = converge(project, write=False)
    assert not report.converged
    assert report.repaired == ["uv.lock"]
    assert "uv.lock" not in report.unchanged


def test_a_stale_environment_is_repaired_not_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case layer 3's launcher depends on: an environment that no longer
    satisfies the lock must be re-synced, not waved through because `.venv`
    happens to be a directory."""
    from unittest.mock import MagicMock

    from lightcone.engine import project as project_mod

    project = tmp_path / "proj"
    converge(project)
    real_run = project_mod._run

    def drifted(argv: list[str], *, cwd: Path) -> MagicMock:
        if argv[:2] == ["uv", "sync"] and "--check" in argv:
            return MagicMock(returncode=1, stdout="", stderr="")
        return real_run(argv, cwd=cwd)  # type: ignore[no-any-return]

    monkeypatch.setattr(project_mod, "_run", drifted)
    tools_before = converge(project)

    assert tools_before.repaired == [".venv"]
    assert tools_before.unchanged and "uv.lock" in tools_before.unchanged


def test_ambient_virtualenv_is_not_passed_to_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every uv call names its project explicitly, so an activated
    environment elsewhere is never what we mean — and leaving it set makes
    uv warn, once per invocation, into a report agents read."""
    from lightcone.engine.project import child_env

    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/else/.venv")
    monkeypatch.setenv("LC_TEST_CANARY", "kept")

    env = child_env()
    assert "VIRTUAL_ENV" not in env
    assert env["LC_TEST_CANARY"] == "kept", "the rest of the environment is untouched"


def test_relays_uv_warnings_into_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one that matters: a cache on a different filesystem means uv
    copies every package instead of linking it, and nothing else would tell
    the user their environment costs full price."""
    from unittest.mock import MagicMock

    from lightcone.engine import project as project_mod

    stderr = (
        "warning: Failed to hardlink files; falling back to full copy.\n"
        "         If the cache and target directories are on different "
        "filesystems, hardlinking may not be supported.\n"
        "Installed 117 packages in 34ms\n"
    )

    def fake_run(argv: list[str], *, cwd: Path) -> MagicMock:
        if argv[:2] == ["uv", "lock"]:
            (cwd / "uv.lock").write_text("version = 1\n")
        return MagicMock(returncode=0, stdout="", stderr=stderr)

    monkeypatch.setattr(project_mod, "_run", fake_run)
    report = converge(tmp_path / "proj")

    assert any("falling back to full copy" in w for w in report.warnings)
    # The continuation line is folded in, the progress line is not.
    assert any("different filesystems" in w for w in report.warnings)
    assert not any("Installed 117" in w for w in report.warnings)
    # Advisory: a heavy venv is still a converged project.
    assert not report.blocked


def test_tool_warnings_ignores_progress_output() -> None:
    from lightcone.engine.project import tool_warnings

    assert tool_warnings("Installed 3 packages\n + click==8.4\n") == []
    assert tool_warnings("warning: a\nwarning: b\n") == ["a", "b"]


def test_tool_warnings_does_not_swallow_the_change_list() -> None:
    """uv indents its change list by one space and its warning
    continuations by nine, so the continuation rule has to tell them
    apart — otherwise a hundred `+ pkg==ver` lines land inside the
    warning text."""
    from lightcone.engine.project import tool_warnings

    found = tool_warnings(
        "warning: first\n"
        "Resolved 114 packages in 12ms\n"
        " + aiohttp==3.14.3\n"
        " - six==1.17.0\n"
        " ~ click==8.4.2\n"
        "warning: second\n"
        "         continued here\n"
    )
    assert found == ["first", "second continued here"]


def test_surfaces_a_lock_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A silently broken lock would fail every later verb more
    confusingly — so the failure surfaces here, with uv's own stderr."""
    from unittest.mock import MagicMock

    from lightcone.engine import project as project_mod

    monkeypatch.setattr(
        project_mod,
        "_run",
        lambda argv, *, cwd: MagicMock(returncode=1, stdout="", stderr="no solution found"),
    )
    with pytest.raises(ProjectError, match="no solution found"):
        converge(tmp_path / "proj")
