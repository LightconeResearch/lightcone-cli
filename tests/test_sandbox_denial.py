"""Tests for the denial UX — the sandbox's primary user interface.

Pure functions over a captured stderr and a policy, so these run
anywhere. The two cases that matter most are the ones where the
classifier *fails*: a command that swallows the PermissionError, and one
that rewraps it past recognition. Both must still leave the user knowing
a sandbox was there.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lightcone.engine.sandbox import denial
from lightcone.engine.sandbox.model import Policy


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "astra.yaml").write_text("inputs: []\n")
    return root


@pytest.fixture
def declared_input(tmp_path: Path) -> Path:
    """A declared input: readable, and never writable.

    The readable-but-not-writable case used to be the project tree. Now
    that the tree is writable, this is what is left of it — and it is
    the honest example, since an input really is somebody else's file.
    """
    source = tmp_path / "inputs"
    source.mkdir()
    (source / "catalog.csv").write_text("id\n")
    return source


@pytest.fixture
def policy(project: Path, declared_input: Path, tmp_path: Path) -> Policy:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    return Policy(
        read=(project, declared_input, Path("/usr")),
        # `results/` only — the rest of the tree is read-only.
        write=(scratch, project / "results"),
        execute=(venv_bin,),
        tmp_home=scratch,
        env={},
    )


# ---- the three denial kinds -----------------------------------------------


def test_an_undeclared_tool_is_named_with_its_remedy(policy: Policy, project: Path) -> None:
    """/usr/bin/id is *readable* under the baseline, so only an
    access-aware check can tell that executing it was the denial."""
    tool = shutil.which("id")
    if tool is None:  # pragma: no cover - a host without coreutils
        pytest.skip("no `id` on this host")
    lines = denial.explain(f"bash: line 1: {tool}: Permission denied\n", policy, cwd=project)
    joined = "\n".join(lines)
    assert f"cannot execute {tool}" in joined
    assert "uv add" in joined
    # The system-layer remedy is real now — `lc build` exists — so the
    # standing cap on remedies makes naming it mandatory, not optional.
    assert "[tool.lightcone.image]" in joined
    assert "apt-install" in joined
    assert "behind" in joined


def test_an_undeclared_data_file_gets_the_astra_snippet(
    policy: Policy, project: Path, tmp_path: Path
) -> None:
    """The remedy is copy-pasteable and matches ASTRA's real schema —
    inputs carry `source`, not `path`."""
    external = tmp_path / "elsewhere.fits"
    external.write_text("")
    stderr = f"PermissionError: [Errno 13] Permission denied: '{external}'\n"
    joined = "\n".join(denial.explain(stderr, policy, cwd=project))
    assert f"cannot read {external}" in joined
    assert "inputs:" in joined
    assert f"source: {external}" in joined


def test_an_in_tree_write_is_its_own_kind_of_denial(policy: Policy, project: Path) -> None:
    """Reading the project was allowed, so an EACCES on a project file can
    only have been a write — and "declare it as an input" would be exactly
    the wrong advice."""
    stderr = "PermissionError: [Errno 13] Permission denied: 'astra.yaml'\n"
    joined = "\n".join(denial.explain(stderr, policy, cwd=project))
    assert "cannot write" in joined
    assert "its own output directory" in joined
    assert "inputs:" not in joined


def test_a_write_to_a_declared_input_says_the_same_thing(
    policy: Policy, project: Path, declared_input: Path
) -> None:
    """An input is somebody else's file: readable because it is declared,
    never writable — and "declare it as an input" would be absurd advice
    for a file that already is one."""
    target = declared_input / "catalog.csv"
    stderr = f"PermissionError: [Errno 13] Permission denied: '{target}'\n"
    joined = "\n".join(denial.explain(stderr, policy, cwd=project))
    assert "cannot write" in joined
    assert "inputs:" not in joined


def test_a_write_into_results_is_not_a_denial_at_all(policy: Policy, project: Path) -> None:
    """`results/` is granted, so an EACCES there is the OS's problem, not
    the sandbox's."""
    (project / "results").mkdir(exist_ok=True)
    target = project / "results" / "out.csv"
    target.write_text("")
    stderr = f"PermissionError: [Errno 13] Permission denied: '{target}'\n"
    assert denial.explain(stderr, policy, cwd=project) == []


# ---- what must not be reported --------------------------------------------


def test_a_granted_path_is_not_reported(policy: Policy, project: Path, tmp_path: Path) -> None:
    """Somebody else's permission problem, inside a path we allow — not
    ours to explain."""
    inside = tmp_path / "scratch" / "f.txt"
    inside.write_text("")
    stderr = f"PermissionError: [Errno 13] Permission denied: '{inside}'\n"
    assert denial.explain(stderr, policy, cwd=project) == []


def test_a_path_that_does_not_exist_is_not_reported(policy: Policy, project: Path) -> None:
    """An ordinary missing-file bug. Claiming the sandbox blocked it
    would send the reader after a file that was never there."""
    stderr = "FileNotFoundError: [Errno 2] No such file: '/nope/missing.txt'\n"
    assert denial.explain(stderr, policy, cwd=project) == []


def test_nothing_recognizable_explains_nothing(policy: Policy, project: Path) -> None:
    assert denial.explain("Traceback: ValueError: bad fit\n", policy, cwd=project) == []


# ---- the fallbacks --------------------------------------------------------


def test_a_swallowed_permission_error_still_gets_the_trailer() -> None:
    """The failure mode that produces hours of confusion: the command
    caught the PermissionError and died of something unrelated, so
    nothing in its output says a sandbox was involved."""
    text = denial.trailer("landlock")
    assert "ran under the lc sandbox (landlock)" in text


def test_a_rewrapped_error_defeats_the_classifier_but_not_the_trailer(
    policy: Policy, project: Path
) -> None:
    stderr = "RuntimeError: could not open the calibration table (see log)\n"
    assert denial.explain(stderr, policy, cwd=project) == []
    assert denial.trailer("seatbelt")


# ---- message shape --------------------------------------------------------


def test_a_bare_command_name_is_resolved_against_the_host(
    policy: Policy, project: Path
) -> None:
    """`command not found` reports a name, not a location. Resolving it
    outside the sandbox turns it into "you have this, it just isn't
    declared"."""
    if shutil.which("id") is None:  # pragma: no cover
        pytest.skip("no `id` on this host")
    joined = "\n".join(denial.explain("bash: line 1: id: command not found\n", policy, cwd=project))
    assert "cannot execute" in joined


def test_a_relative_path_is_resolved_against_the_working_directory(
    policy: Policy, project: Path, tmp_path: Path
) -> None:
    """Python reports the string the command passed, so a denial can
    arrive as a bare relative path with no directory at all."""
    (tmp_path / "elsewhere.fits").write_text("")
    stderr = "PermissionError: [Errno 13] Permission denied: '../elsewhere.fits'\n"
    assert denial.explain(stderr, policy, cwd=project) != []


def test_no_escape_hatch_is_ever_offered(
    policy: Policy, project: Path, declared_input: Path
) -> None:
    """There is no way to run outside the sandbox, so no message may
    suggest one."""
    target = declared_input / "catalog.csv"
    joined = "\n".join(
        denial.explain(
            f"PermissionError: [Errno 13] Permission denied: '{target}'\n", policy, cwd=project
        )
    )
    assert "cannot write" in joined
    assert "--no-sandbox" not in joined
    assert "sandbox-debug" not in joined
