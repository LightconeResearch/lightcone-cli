"""Tests for the OCI backend — the mount table as the mechanism.

Pure, and run on every OS: the wrap is a function of the policy and the
backend's fields, so the argv a containerized recipe would get is checked
here with nothing spawned and no runtime installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from lightcone.engine.sandbox import boundary, exec_policy
from lightcone.engine.sandbox.boundary import Unavailable
from lightcone.engine.sandbox.model import Policy
from lightcone.engine.sandbox.oci import OCIBackend

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
    import shutil

    shutil.rmtree(built.tmp_home, ignore_errors=True)


def _backend(runtime: str = "podman", **kwargs: Any) -> OCIBackend:
    flags = ("--userns=keep-id", "--pull=never") if runtime == "podman" else ("--user", "1000:1000")
    return OCIBackend(
        runtime=runtime,  # type: ignore[arg-type]
        image_id=_IMAGE_ID,
        root=kwargs.pop("root"),
        user_flags=flags,
    )


# ---- the containerized policy shape ----------------------------------------


def test_the_containerized_policy_is_the_project_world_only(root: Path, policy: Policy) -> None:
    """The image is the OS baseline and the exec set — everything present
    in it was declared — so the path sets carry only what becomes mounts."""
    assert all(str(p).startswith(str(root)) for p in policy.read)
    assert policy.execute == ()
    assert root.resolve() in policy.read
    assert (root / "results").resolve() in policy.write
    assert policy.tmp_home in policy.write


def test_the_containerized_home_lives_under_the_project(root: Path, policy: Policy) -> None:
    """It is a mount source, and on macOS the podman machine shares the
    project's tree while the host's temp roots arrive empty."""
    assert policy.tmp_home.is_relative_to((root / ".lightcone").resolve())


def test_the_overlay_points_uv_at_the_image_environment(root: Path, policy: Policy) -> None:
    """The `uv run` hop executes inside the container; this is how it
    finds `.lightcone/venv` instead of inventing a `.venv`."""
    env_dir = root / ".lightcone" / "venv"
    assert policy.env["UV_PROJECT_ENVIRONMENT"] == str(env_dir)
    assert policy.env["PATH"].startswith(str(env_dir / "bin"))
    # The PATH tail is the image's own FHS, never the host allowlist
    # search path — whose NixOS entry no Debian-family image has.
    assert "/run/current-system" not in policy.env["PATH"]


# ---- the wrap ---------------------------------------------------------------


def test_wrap_is_pure(root: Path, policy: Policy, tmp_path: Path) -> None:
    before = set(tmp_path.rglob("*"))
    backend = _backend(root=root)
    assert backend.wrap(policy, ["true"]) == backend.wrap(policy, ["true"])
    assert set(tmp_path.rglob("*")) == before


def test_reads_mount_ro_and_writes_mount_rw(root: Path, policy: Policy) -> None:
    argv = _backend(root=root).wrap(policy, ["true"])
    assert f"--volume={root.resolve()}:{root.resolve()}:ro" in argv
    results = (root / "results").resolve()
    assert f"--volume={results}:{results}:rw" in argv
    catalog = (root / "data" / "catalog.fits").resolve()
    assert f"--volume={catalog}:{catalog}:ro" in argv
    # Read mounts land before write mounts, so the writable results
    # directory nests over the read-only tree the way the runtimes
    # resolve natively.
    assert argv.index(f"--volume={root.resolve()}:{root.resolve()}:ro") < argv.index(
        f"--volume={results}:{results}:rw"
    )


def test_execution_pins_the_image_by_id_never_a_tag(root: Path, policy: Policy) -> None:
    argv = _backend(root=root).wrap(policy, ["bash", "-c", "true"])
    assert _IMAGE_ID in argv
    assert argv[argv.index(_IMAGE_ID) + 1 :] == ["bash", "-c", "true"]
    assert not any("lc-env-" in part for part in argv)


def test_the_network_is_denied_by_flag(root: Path, policy: Policy) -> None:
    argv = _backend(root=root).wrap(policy, ["true"])
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"


def test_runtimes_differ_only_in_their_spellings(root: Path, policy: Policy) -> None:
    podman = _backend("podman", root=root).wrap(policy, ["true"])
    docker = _backend("docker", root=root).wrap(policy, ["true"])
    assert "--userns=keep-id" in podman and "--pull=never" in podman
    assert "--user" in docker and "1000:1000" in docker
    strip = {
        "--userns=keep-id", "--pull=never", "--user", "1000:1000", "podman", "docker",
        "--env=LC_SANDBOX=podman", "--env=LC_SANDBOX=docker",
    }  # fmt: skip
    assert [a for a in podman if a not in strip] == [a for a in docker if a not in strip]


def test_the_environment_is_an_allowlist_never_ambient(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret in the invoking shell must never reach the container."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "hunter2")
    argv = _backend(root=root).wrap(policy, ["true"])
    assert not any("hunter2" in part or "AWS_SECRET" in part for part in argv)
    for key, value in policy.env.items():
        assert f"--env={key}={value}" in argv
    assert "--env=LC_SANDBOX=podman" in argv


def test_no_host_resolved_env_binary_in_the_argv(root: Path, policy: Policy) -> None:
    """The overlay travels as `--env` flags: a host path for `env` (NixOS
    keeps it under /run/current-system/sw) need not exist in the image,
    and argv[0] dying there would blame the user's command."""
    argv = _backend(root=root).wrap(policy, ["true"])
    assert not any(part.endswith("/env") for part in argv)


# ---- the attestation --------------------------------------------------------


def test_the_attestation_is_derived_from_the_flags(root: Path, policy: Policy) -> None:
    for runtime in ("podman", "docker"):
        attested = _backend(runtime, root=root).attest(policy)
        assert attested.mechanism == runtime
        assert attested.fs == "declared"
        assert attested.network == "denied"
        assert attested.landlock_abi is None


# ---- the seam's composition -------------------------------------------------


class _Recorder:
    """A Popen stand-in that records the argv and exits cleanly."""

    def __init__(self) -> None:
        self.argv: list[str] | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.argv = list(argv)

        class _Proc:
            import io

            stderr = io.StringIO("")
            returncode = 0

            def wait(self) -> int:
                return 0

        return _Proc()


def test_a_world_backend_takes_the_prefix_inside(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a container there is no trusted host plumbing: the `uv run` hop
    is part of the world being entered, so it lands after the image in
    the argv rather than in front of the runtime."""
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)

    boundary.run(
        _backend(root=root),
        policy,
        ["bash", "-c", "true"],
        cwd=root,
        env={},
        prefix=["uv", "run", "--locked", "--no-sync", "--project", str(root), "--"],
    )

    assert recorder.argv is not None
    assert recorder.argv[0] == "podman"
    assert recorder.argv[recorder.argv.index(_IMAGE_ID) + 1 :] == [
        "uv", "run", "--locked", "--no-sync", "--project", str(root), "--",
        "bash", "-c", "true",
    ]  # fmt: skip


def test_a_host_backend_keeps_the_prefix_outside(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing composition, pinned: uv's config and caches are
    trusted plumbing outside a host mechanism's rewrite."""
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)

    boundary.run(
        Unavailable(),
        policy,
        ["bash", "-c", "true"],
        cwd=root,
        env={},
        prefix=["uv", "run", "--"],
    )

    assert recorder.argv is not None
    assert recorder.argv[:3] == ["uv", "run", "--"]
