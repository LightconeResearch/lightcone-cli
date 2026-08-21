"""Tests for `lightcone.engine.project` — how a directory converges into a
project."""

from __future__ import annotations

import shutil
import uuid
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
    assert (project / ".gitignore").read_text() == templates.read("gitignore.tmpl")
    assert (project / "results/README.md").read_text() == templates.read("results-README.md.tmpl")
    assert (project / "myst.yml").read_text() == templates.read("myst.yml.tmpl")
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


def test_a_containerized_project_converges_no_host_venv(
    tmp_path: Path, tools: list[list[str]]
) -> None:
    """The host sync is the host-sync deadlock in miniature: the lock's
    system-level dependencies — the reason the project containerized at
    all — are not on the host, so a host `uv sync` fails and `--check`
    would report unconverged forever. The verbs converge the environment
    inside the image instead; init owes neither podman nor minutes."""
    project = tmp_path / "proj"
    converge(project)
    text = (project / "pyproject.toml").read_text()
    (project / "pyproject.toml").write_text(
        text + '\n[tool.lightcone.image]\napt-install = ["bc"]\n'
    )
    shutil.rmtree(project / ".venv")

    report = converge(project)

    assert ".venv" not in [*report.created, *report.repaired, *report.unchanged]
    assert report.converged
    assert not (project / ".venv").exists()
    # The lock still converges: locking is resolution, which the bare
    # host can do in both modes.
    assert "uv.lock" in report.unchanged


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
    drift forever.

    Two items are exempt, and both for the same reason — they are local
    state git does not clone. `.venv` is git-ignored and rebuilt from the
    lock, and `git clone` of an annexed repository leaves the annex
    uninitialized until someone runs `git annex init`."""
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
    assert report.created == ["git-annex", ".venv"], report.created


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
    assert templates.missing("gitignore.tmpl", text) == []


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


def test_an_adopted_pyproject_is_never_edited(tmp_path: Path) -> None:
    """A pyproject we didn't write is the user's: read, never edited."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "mine"\nversion = "0"\n')

    report = converge(project)
    assert report.warnings == []
    assert 'name = "mine"' in (project / "pyproject.toml").read_text()


def test_a_fresh_scaffold_warns_about_nothing(tmp_path: Path) -> None:
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


def test_a_gitattributes_the_repair_cannot_order_blocks_convergence(
    tmp_path: Path,
) -> None:
    """The repair only appends, so a file already opting `results/` into
    the annex gets the `*` default added *below* it — and git-annex takes
    the last match, so every result would be committed to git as a plain
    blob while the report said the file was repaired."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitattributes").write_text("results/** annex.largefiles=anything\n")

    report = converge(project)
    assert ".gitattributes" in report.blocked
    assert not report.converged
    assert any("git-annex takes the last match" in w for w in report.warnings)

    # And it stays blocked, rather than the repair settling it into a lie.
    assert not converge(project).converged


def test_a_gitattributes_in_the_right_order_is_repaired_and_converges(
    tmp_path: Path,
) -> None:
    """The mutation check on the test above: the same two lines the other
    way round take the ordinary append and leave the project converged."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitattributes").write_text(
        "* annex.largefiles=nothing\nresults/** annex.largefiles=anything\n"
    )

    report = converge(project)
    assert ".gitattributes" not in report.blocked
    assert ".gitattributes" in report.repaired
    assert converge(project).converged


# ---- the repository -------------------------------------------------------


def test_initializes_a_repository_with_an_annex(
    tmp_path: Path, tools: list[list[str]]
) -> None:
    """git for the pointers, git-annex for the bytes — a project is both
    from birth, because results are versioned in it."""
    project = tmp_path / "proj"
    report = converge(project)

    assert ["git", "init", "-q"] in tools
    assert ["git", "annex", "init", "-q"] in tools
    assert ".git" in report.created
    assert "git-annex" in report.created


def test_writes_the_storage_policy_and_the_dataset_id(tmp_path: Path) -> None:
    """`.gitattributes` is what routes bytes to the annex; `.datalad/config`
    is the one thing that makes the repository a DataLad dataset."""
    project = tmp_path / "proj"
    report = converge(project)

    assert (project / ".gitattributes").read_text() == templates.read("gitattributes.tmpl")
    assert ".datalad/config" in report.created
    config = (project / ".datalad" / "config").read_text()
    assert '[datalad "dataset"]' in config
    uuid.UUID(config.split("id =")[1].strip())


def test_the_dataset_id_is_generated_once(tmp_path: Path) -> None:
    """It identifies the dataset across clones and siblings — regenerating
    it on a re-`init` would make a project a different dataset every time."""
    project = tmp_path / "proj"
    converge(project)
    first = (project / ".datalad" / "config").read_text()

    converge(project)
    assert (project / ".datalad" / "config").read_text() == first


def test_does_not_nest_a_repository_inside_one(tmp_path: Path, tools: list[list[str]]) -> None:
    """`lc init subdir/` inside an existing repository must not create a
    nested one — the check walks up, it doesn't just look in the directory."""
    (tmp_path / ".git").mkdir()
    report = converge(tmp_path / "nested" / "proj")

    assert not any(c[:2] == ["git", "init"] for c in tools)
    assert ".git" in report.unchanged
    assert not (tmp_path / "nested" / "proj" / ".git").exists()


def test_a_linked_worktree_counts_as_version_controlled(tmp_path: Path) -> None:
    """In a linked worktree or submodule `.git` is a *file*, not a
    directory."""
    (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    assert ".git" in converge(tmp_path / "proj").unchanged


def test_check_mode_can_ask_about_a_directory_that_does_not_exist_yet(
    tmp_path: Path,
) -> None:
    """Check mode creates nothing, so inside an enclosing repository the
    walk-up says "in a repository" for a directory that is not there — and
    running git with a cwd that does not exist raises out of `Popen`
    rather than answering anything."""
    (tmp_path / ".git").mkdir()

    report = converge(tmp_path / "newproj", write=False)

    assert ".git" in report.unchanged
    assert "git-annex" in report.created
    assert not (tmp_path / "newproj").exists()


def test_an_existing_annex_is_adopted(tmp_path: Path, tools: list[list[str]]) -> None:
    """The annex is asked about the way git-annex asks itself, so an
    enclosing repository that already has one is not re-initialized."""
    project = tmp_path / "proj"
    converge(project)
    tools.clear()

    report = converge(project)
    assert "git-annex" in report.unchanged
    assert not any(c[:3] == ["git", "annex", "init"] for c in tools)


def test_results_ignored_by_an_older_scaffold_blocks_convergence(tmp_path: Path) -> None:
    """A `.gitignore` written before results were versioned keeps them
    uncommittable, and `.gitignore` convergence only ever appends — so
    convergence cannot fix this and must not call the project converged.
    `git add` skips ignored paths in silence; a materialize would report
    success and commit nothing."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("results/*\n")

    report = converge(project)
    assert "results/" in report.blocked
    assert not report.converged
    assert any("results/*" in w and ".gitignore:1" in w for w in report.warnings)


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
    _absent(monkeypatch, "uv")
    with pytest.raises(ProjectError, match="uv is required"):
        converge(tmp_path / "proj")


def test_requires_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git stops being optional the moment results are versioned in the
    repository: there is no useful project without it, so an absent git is
    a refusal rather than nothing to converge."""
    _absent(monkeypatch, "git")
    with pytest.raises(ProjectError, match="git is required"):
        converge(tmp_path / "proj")


def test_requires_git_annex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """And by the name git itself searches for: `git annex` is not a
    builtin, it is git finding a `git-annex` executable on PATH."""
    _absent(monkeypatch, "git-annex")
    with pytest.raises(ProjectError, match="git-annex is required"):
        converge(tmp_path / "proj")


def _absent(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """Make exactly one tool unfindable, leaving the others resolvable."""
    from lightcone.engine import project as project_mod

    monkeypatch.setattr(
        project_mod.shutil,
        "which",
        lambda name, path=None: None if name == missing else f"/usr/bin/{name}",
    )


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
    """An environment that no longer satisfies the lock must be re-synced,
    not waved through because `.venv` happens to be a directory."""
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


def test_ambient_uv_install_settings_are_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ambient install setting would steer what a sync installs without
    moving env_version — the identity hole the scrub closes. Plumbing (the
    cache, timeouts, index credentials) survives: it decides where bytes
    come from and how fast, never what gets installed."""
    import os

    from lightcone.engine.project import child_env, uv_scrub_warning

    for name in [k for k in os.environ if k.startswith("UV_")]:
        monkeypatch.delenv(name)  # the suite itself may run under `uv run`
    monkeypatch.setenv("UV_NO_BINARY", "1")
    monkeypatch.setenv("UV_PYTHON", "3.10")
    monkeypatch.setenv("UV_INDEX_URL", "https://elsewhere.invalid/simple")
    monkeypatch.setenv("UV_CACHE_DIR", "/scratch/uv")
    monkeypatch.setenv("UV_INDEX_INTERNAL_PASSWORD", "hunter2")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/scratch/uv/python")
    monkeypatch.setenv("UV_LINK_MODE", "copy")
    monkeypatch.setenv("LC_TEST_CANARY", "kept")

    env = child_env()
    assert "UV_NO_BINARY" not in env
    assert "UV_PYTHON" not in env
    assert "UV_INDEX_URL" not in env
    assert env["UV_CACHE_DIR"] == "/scratch/uv", "shared-cache plumbing survives"
    assert env["UV_INDEX_INTERNAL_PASSWORD"] == "hunter2", "credentials survive"
    assert env["UV_OFFLINE"] == "1", "air-gap mode survives"
    assert env["UV_PYTHON_INSTALL_DIR"] == "/scratch/uv/python", (
        "the interpreter store is plumbing, and it has no project-level spelling"
    )
    assert env["UV_LINK_MODE"] == "copy", "link-mode is not an audited setting either"
    assert env["LC_TEST_CANARY"] == "kept"
    assert "UV_INDEX_URL, UV_NO_BINARY, UV_PYTHON" in uv_scrub_warning(), (
        "the warning names exactly what the scrub dropped"
    )


def test_converge_reports_the_uv_scrub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lc init` resolves and syncs, so a user whose ambient UV_INDEX_URL
    was dropped must hear it here — not only on the verbs they have not
    reached when resolution fails with uv's raw error."""
    monkeypatch.setenv("UV_INDEX_URL", "https://mirror.invalid/simple")

    report = converge(tmp_path / "proj")

    assert any("UV_INDEX_URL" in w for w in report.warnings)


def test_an_empty_scrubbed_variable_is_not_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty variable steers nothing, so warning about it is noise."""
    from lightcone.engine.project import uv_scrub_warning

    monkeypatch.setenv("UV_NO_BUILD", "")
    assert "UV_NO_BUILD" not in uv_scrub_warning()


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


def test_license_of_reads_every_spelling(tmp_path: Path) -> None:
    """Publication intent, derived never configured — the crate is
    maintained iff [project].license is declared, in any of its forms."""
    from lightcone.engine.project import license_of

    cases = {
        'license = "MIT"': "MIT",
        'license = { text = "BSD-3-Clause" }': "BSD-3-Clause",
        'license = { file = "LICENSE" }': "LICENSE",
        "": "",
    }
    for spelling, expected in cases.items():
        (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\n{spelling}\n')
        assert license_of(tmp_path) == expected, spelling
