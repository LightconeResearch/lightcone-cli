"""Tests for the apptainer backend — the bind table as the mechanism.

Pure, and run on every OS: the wrap is a function of the policy and the
backend's fields, so the argv a containerized recipe would get is checked
here with nothing spawned and apptainer nowhere on the host.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lightcone.engine.sandbox import boundary, exec_policy
from lightcone.engine.sandbox.apptainer import ApptainerBackend
from lightcone.engine.sandbox.model import Policy

_IMAGE_ID = "956ea01f6c5b94522bedc346c9646f81d0707b2a00b2a9ed8b4e5b6a8d2d00d1"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "analysis"
    (project / "results").mkdir(parents=True)
    (project / "data").mkdir()
    (project / "data" / "catalog.fits").write_text("stars\n")
    return project


@pytest.fixture
def policy(root: Path) -> Policy:
    built = exec_policy(
        root,
        read_paths=[root / "data" / "catalog.fits"],
        env_dir=root / ".lightcone" / "venv",
        containerized=True,
    )
    yield built
    shutil.rmtree(built.tmp_home, ignore_errors=True)


def _backend(root: Path) -> ApptainerBackend:
    return ApptainerBackend(sif=root / ".lightcone" / "images" / f"{_IMAGE_ID}.sif", root=root)


# ---- the wrap ---------------------------------------------------------------


def test_wrap_is_pure(root: Path, policy: Policy, tmp_path: Path) -> None:
    before = set(tmp_path.rglob("*"))
    backend = _backend(root)
    assert backend.wrap(policy, ["true"]) == backend.wrap(policy, ["true"])
    assert set(tmp_path.rglob("*")) == before


def test_reads_bind_ro_and_writes_bind_rw(root: Path, policy: Policy) -> None:
    argv = _backend(root).wrap(policy, ["true"])
    results = root / "results"
    assert f"{root.resolve()}:{root}:ro" in argv
    assert f"{results.resolve()}:{results}:rw" in argv
    # Read binds land before write binds, so the writable results
    # directory nests over the read-only tree the way apptainer resolves
    # natively.
    assert argv.index(f"{root.resolve()}:{root}:ro") < argv.index(
        f"{results.resolve()}:{results}:rw"
    )
    # Every bind is introduced by its own flag.
    for index, part in enumerate(argv):
        if part.count(":") == 2 and part.endswith((":ro", ":rw")):
            assert argv[index - 1] == "--bind"


def test_a_symlinked_input_binds_at_its_declared_path(root: Path, tmp_path: Path) -> None:
    """Resolved source, declared destination — the bind must name the
    real file while the recipe addresses the path it declared."""
    store = tmp_path / "store"
    store.mkdir()
    (store / "catalog.h5").write_text("stars\n")
    link = tmp_path / "data-link"
    link.symlink_to(store)
    declared = link / "catalog.h5"

    built = exec_policy(
        root,
        read_paths=[declared],
        env_dir=root / ".lightcone" / "venv",
        containerized=True,
    )
    try:
        assert f"{store / 'catalog.h5'}:{declared}:ro" in _backend(root).wrap(built, ["true"])
    finally:
        shutil.rmtree(built.tmp_home, ignore_errors=True)


def test_containall_is_the_isolation(root: Path, policy: Policy) -> None:
    """A SIF is read-only by construction, so there is no `--read-only`
    to ask for; `--containall` is what hides the undeclared host and
    supplies the private `/tmp`."""
    argv = _backend(root).wrap(policy, ["true"])
    assert "--containall" in argv
    assert "--cleanenv" in argv
    # Nothing may ask for a writable overlay — that would put a stray
    # write into an ephemeral layer while the run attests `fs: declared`.
    assert not any(part.startswith("--writable") for part in argv)


def test_the_private_home_is_a_flag_not_an_env_var(root: Path, policy: Policy) -> None:
    """apptainer refuses to set HOME through `--env` (it warns and keeps
    the host's), so the policy's `tmp_home` arrives as `--home` — and
    must not also be emitted as an env var nobody would read."""
    argv = _backend(root).wrap(policy, ["true"])
    assert argv[argv.index("--home") + 1] == str(policy.tmp_home)
    assert not any(part.startswith("--env=HOME=") for part in argv)


def test_execution_pins_the_converted_image_by_id(root: Path, policy: Policy) -> None:
    backend = _backend(root)
    argv = backend.wrap(policy, ["bash", "-c", "true"])
    assert _IMAGE_ID in backend.sif.name
    assert argv[argv.index(str(backend.sif)) + 1 :] == ["bash", "-c", "true"]
    assert argv[argv.index("--pwd") + 1] == str(root)


def test_no_flag_touches_the_network(root: Path, policy: Policy) -> None:
    """`--net` is the flag that would; without it the `allowed`
    attestation is what the argv actually says."""
    assert "--net" not in _backend(root).wrap(policy, ["true"])


def test_the_environment_is_an_allowlist_never_ambient(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret in the invoking shell must never reach the container."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "hunter2")
    argv = _backend(root).wrap(policy, ["true"])
    assert not any("hunter2" in part or "AWS_SECRET" in part for part in argv)
    for key, value in policy.env.items():
        if key != "HOME":
            assert f"--env={key}={value}" in argv
    assert "--env=LC_SANDBOX=apptainer" in argv


# ---- the attestation --------------------------------------------------------


def test_the_attestation_is_derived_from_the_flags(root: Path, policy: Policy) -> None:
    attested = _backend(root).attest(policy)
    assert attested.mechanism == "apptainer"
    assert attested.fs == "declared"
    assert attested.network == "allowed"
    assert attested.landlock_abi is None


# ---- the seam's composition -------------------------------------------------


class _Recorder:
    """A Popen stand-in that records the argv and exits as told."""

    def __init__(self, returncode: int = 0) -> None:
        self.argv: list[str] | None = None
        self.returncode = returncode

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.argv = list(argv)
        code = self.returncode

        class _Proc:
            import io

            stderr = io.StringIO("")
            returncode = code

            def wait(self) -> int:
                return code

        return _Proc()


def test_the_prefix_runs_inside_the_sif(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv is part of the world being entered, so the `uv run` hop lands
    after the image rather than in front of apptainer."""
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)

    boundary.run(
        _backend(root),
        policy,
        ["bash", "-c", "true"],
        cwd=root,
        env={},
        prefix=["uv", "run", "--locked", "--no-sync", "--project", str(root), "--"],
    )

    assert recorder.argv is not None
    assert recorder.argv[:2] == ["apptainer", "exec"]
    assert recorder.argv[recorder.argv.index(str(_backend(root).sif)) + 1 :] == [
        "uv", "run", "--locked", "--no-sync", "--project", str(root), "--",
        "bash", "-c", "true",
    ]  # fmt: skip


def test_exit_255_is_apptainers_own_failure(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apptainer reports its fatals as 255 where the podman family uses
    125 — the note is keyed on the mechanism, so each gets its own."""
    monkeypatch.setattr(subprocess, "Popen", _Recorder(returncode=255))
    outcome = boundary.run(_backend(root), policy, ["true"], cwd=root, env={})
    assert any("runtime failed before the command ran" in note for note in outcome.notes)
    assert not any("ran under the lc sandbox" in note for note in outcome.notes)


def test_exit_125_is_not_apptainers(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The podman family's code, under a mechanism that does not use it:
    a recipe legitimately exiting 125 must be diagnosed as the command's
    failure, not blamed on the runtime."""
    monkeypatch.setattr(subprocess, "Popen", _Recorder(returncode=125))
    outcome = boundary.run(_backend(root), policy, ["true"], cwd=root, env={})
    assert not any("runtime failed before" in note for note in outcome.notes)
    assert any("ran under the lc sandbox" in note for note in outcome.notes)
