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


def _backend(root: Path, runtime: str = "podman") -> OCIBackend:
    flags = (
        ("--user", "1000:1000")
        if runtime == "docker"
        else ("--userns=keep-id", "--pull=never")
    )
    return OCIBackend(
        runtime=runtime,  # type: ignore[arg-type]
        image_id=_IMAGE_ID,
        root=root,
        user_flags=flags,
    )


# ---- the containerized policy shape ----------------------------------------


def test_the_containerized_policy_is_the_project_world_only(root: Path, policy: Policy) -> None:
    """The image is the OS baseline and the exec set — everything present
    in it was declared — so the path sets carry only what becomes mounts.
    Declared spellings, deliberately unresolved: they become mount
    *destinations*, and the recipe addresses the declared path."""
    assert all(str(p).startswith(str(root)) for p in policy.read)
    assert policy.execute == ()
    assert root in policy.read
    assert root / "results" in policy.write
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
    backend = _backend(root)
    assert backend.wrap(policy, ["true"]) == backend.wrap(policy, ["true"])
    assert set(tmp_path.rglob("*")) == before


def test_reads_mount_ro_and_writes_mount_rw(root: Path, policy: Policy) -> None:
    argv = _backend(root).wrap(policy, ["true"])
    assert f"--volume={root.resolve()}:{root}:ro" in argv
    results = root / "results"
    assert f"--volume={results.resolve()}:{results}:rw" in argv
    catalog = root / "data" / "catalog.fits"
    assert f"--volume={catalog.resolve()}:{catalog}:ro" in argv
    # Read mounts land before write mounts, so the writable results
    # directory nests over the read-only tree the way the runtimes
    # resolve natively.
    assert argv.index(f"--volume={root.resolve()}:{root}:ro") < argv.index(
        f"--volume={results.resolve()}:{results}:rw"
    )


def test_a_symlinked_input_mounts_at_its_declared_path(root: Path, tmp_path: Path) -> None:
    """The HPC case: `/data` is a symlink into a shared store. The bind's
    source must be the real file, but the destination is the path the
    analysis declared — resolving both would leave the container with no
    `/data` at all and the recipe's literal path ENOENT."""
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
        argv = _backend(root).wrap(built, ["true"])
        assert f"--volume={store / 'catalog.h5'}:{declared}:ro" in argv
    finally:
        import shutil

        shutil.rmtree(built.tmp_home, ignore_errors=True)


def test_the_rootfs_is_read_only_and_labels_are_disabled(root: Path, policy: Policy) -> None:
    """`--read-only`: without it a write outside the declared set
    *succeeds* into the container's ephemeral layer and vanishes, while
    the run attests `fs: declared` — the silent-loss path. And
    `label=disable`: SELinux hosts otherwise refuse every bind read."""
    argv = _backend(root).wrap(policy, ["true"])
    assert "--read-only" in argv
    assert "--security-opt" in argv
    assert argv[argv.index("--security-opt") + 1] == "label=disable"


def test_execution_pins_the_image_by_id_never_a_tag(root: Path, policy: Policy) -> None:
    argv = _backend(root).wrap(policy, ["bash", "-c", "true"])
    assert _IMAGE_ID in argv
    assert argv[argv.index(_IMAGE_ID) + 1 :] == ["bash", "-c", "true"]
    assert not any("lc-env-" in part for part in argv)


def test_the_network_is_denied_by_flag(root: Path, policy: Policy) -> None:
    argv = _backend(root).wrap(policy, ["true"])
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"


def test_runtimes_differ_only_in_their_spellings(root: Path, policy: Policy) -> None:
    podman = _backend(root, "podman").wrap(policy, ["true"])
    docker = _backend(root, "docker").wrap(policy, ["true"])
    hpc = _backend(root, "podman-hpc").wrap(policy, ["true"])
    assert "--userns=keep-id" in podman and "--pull=never" in podman
    assert "--user" in docker and "1000:1000" in docker
    # The site wrapper is podman's argv with only the runtime word swapped.
    swap = {"podman": "podman-hpc", "--env=LC_SANDBOX=podman": "--env=LC_SANDBOX=podman-hpc"}
    assert hpc == [swap.get(a, a) for a in podman]
    strip = {
        "--userns=keep-id", "--pull=never", "--user", "1000:1000",
        "podman", "docker", "podman-hpc",
        "--env=LC_SANDBOX=podman", "--env=LC_SANDBOX=docker", "--env=LC_SANDBOX=podman-hpc",
    }  # fmt: skip
    p, d, h = ([a for a in argv if a not in strip] for argv in (podman, docker, hpc))
    assert p == d == h


def test_the_environment_is_an_allowlist_never_ambient(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret in the invoking shell must never reach the container."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "hunter2")
    argv = _backend(root).wrap(policy, ["true"])
    assert not any("hunter2" in part or "AWS_SECRET" in part for part in argv)
    for key, value in policy.env.items():
        assert f"--env={key}={value}" in argv
    assert "--env=LC_SANDBOX=podman" in argv


def test_no_host_resolved_env_binary_in_the_argv(root: Path, policy: Policy) -> None:
    """The overlay travels as `--env` flags: a host path for `env` (NixOS
    keeps it under /run/current-system/sw) need not exist in the image,
    and argv[0] dying there would blame the user's command."""
    argv = _backend(root).wrap(policy, ["true"])
    assert not any(part.endswith("/env") for part in argv)


# ---- the attestation --------------------------------------------------------


def test_the_attestation_is_derived_from_the_flags(root: Path, policy: Policy) -> None:
    for runtime in ("podman", "docker", "podman-hpc"):
        attested = _backend(root, runtime).attest(policy)
        assert attested.mechanism == runtime
        assert attested.fs == "declared"
        assert attested.network == "denied"
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


def test_a_world_backend_takes_the_prefix_inside(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a container there is no trusted host plumbing: the `uv run` hop
    is part of the world being entered, so it lands after the image in
    the argv rather than in front of the runtime."""
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


def test_exit_97_is_the_shims_only_under_landlock(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """97 is the shim's reserved code, and there is no shim in a
    container — a recipe legitimately exiting 97 must not be told lc
    could not set up the sandbox."""
    monkeypatch.setattr(subprocess, "Popen", _Recorder(returncode=97))
    outcome = boundary.run(_backend(root), policy, ["true"], cwd=root, env={})
    assert not any("could not set up" in note for note in outcome.notes)


def test_exit_125_names_the_runtime_not_the_command(
    root: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runtimes reserve 125 for their own failures — the command
    never ran, so neither the denial heuristics nor the trailer should
    point at it."""
    monkeypatch.setattr(subprocess, "Popen", _Recorder(returncode=125))
    outcome = boundary.run(_backend(root), policy, ["true"], cwd=root, env={})
    assert any("runtime failed before the command ran" in note for note in outcome.notes)
    assert not any("ran under the lc sandbox" in note for note in outcome.notes)
