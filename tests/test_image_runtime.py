"""Unit tests for the mount set, podman run argv, and machine preflight."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lightcone.engine.image import machine as machine_mod
from lightcone.engine.image.errors import DeclarationError, MachinePreflightError
from lightcone.engine.image.mounts import MountSet, compute_mount_set
from lightcone.engine.image.record import BuildRecord
from lightcone.engine.image.runtime_podman import PodmanRuntime

_RECORD = BuildRecord(
    tag="lc-env-0123456789abcdef",
    image_id="sha256:" + "aa" * 32,
    digest=None,
    platform="linux/amd64",
    env_version="sha256:" + "cc" * 32,
    lc_version="0",
    base="docker.io/library/debian:bookworm-slim@sha256:" + "dd" * 32,
    built_at="2026-08-17T00:00:00+00:00",
    dpkg_snapshot_sha256="ee" * 32,
)


class TestMountSet:
    def test_project_rw_inputs_ro(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        data = tmp_path / "data" / "cat.fits"
        data.parent.mkdir()
        data.write_text("x")
        ms = compute_mount_set(project, external_inputs=[data])
        args = ms.to_podman_args()
        assert f"{project.resolve()}:{project.resolve()}:rw" in args
        assert f"{data.resolve()}:{data.resolve()}:ro" in args

    def test_probe_mounts_project_ro(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        ms = compute_mount_set(project, readonly_project=True)
        assert f"{project.resolve()}:{project.resolve()}:ro" in ms.to_podman_args()

    def test_in_tree_inputs_deduped(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / "data").mkdir(parents=True)
        (project / "data" / "x.txt").write_text("x")
        ms = compute_mount_set(
            project, external_inputs=[project / "data" / "x.txt"]
        )
        assert ms.external_inputs == ()

    def test_nested_external_inputs_deduped(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        outer = tmp_path / "catalogs"
        (outer / "sub").mkdir(parents=True)
        (outer / "sub" / "x.txt").write_text("x")
        ms = compute_mount_set(
            project, external_inputs=[outer / "sub" / "x.txt", outer]
        )
        assert ms.external_inputs == (outer.resolve(),)

    def test_parent_of_project_refused(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        with pytest.raises(DeclarationError, match="widen"):
            compute_mount_set(project, external_inputs=[tmp_path])

    def test_tmpfs_and_shm(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        args = compute_mount_set(project).to_podman_args()
        assert "/tmp:rw,exec" in args
        assert "--shm-size" in args


class TestRunArgv:
    def _runtime(self) -> PodmanRuntime:
        with patch("shutil.which", return_value="/usr/bin/podman"):
            return PodmanRuntime()

    def _argv(self, tmp_path: Path, **kwargs) -> list[str]:  # type: ignore[no-untyped-def]
        project = tmp_path / "proj"
        project.mkdir(exist_ok=True)
        return self._runtime().run_argv(
            record=_RECORD,
            mounts=MountSet(project=project.resolve(), external_inputs=()),
            argv=["/opt/venv/bin/lc", "materialize"],
            **kwargs,
        )

    def test_isolation_flags(self, tmp_path: Path) -> None:
        argv = self._argv(tmp_path)
        for flag in ("--net=none", "--userns=keep-id", "--entrypoint=", "--pull=never"):
            assert flag in argv
        assert "label=disable" in argv

    def test_pinned_by_image_id(self, tmp_path: Path) -> None:
        """The pin point: the image reference in the argv is the recorded
        image id, not the tag — a retagged image cannot substitute."""
        argv = self._argv(tmp_path)
        assert _RECORD.image_id in argv
        assert _RECORD.tag not in argv[argv.index(_RECORD.image_id):]

    def test_identity_env_injected(self, tmp_path: Path) -> None:
        argv = self._argv(tmp_path)
        assert "LC_DELEGATED=1" in argv
        assert "LC_WORKER_RUNTIME=container" in argv
        assert "LC_CONTAINER_NETWORK=none" in argv
        assert f"LC_IMAGE_DIGEST={_RECORD.image_id}" in argv

    def test_env_is_allowlist_not_ambient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "hunter2")
        monkeypatch.setenv("TERM", "xterm-256color")
        argv = self._argv(tmp_path)
        joined = " ".join(argv)
        assert "hunter2" not in joined
        assert "TERM=xterm-256color" in joined

    def test_command_re_enters_lc(self, tmp_path: Path) -> None:
        argv = self._argv(tmp_path)
        assert argv[-2:] == ["/opt/venv/bin/lc", "materialize"]


class TestMachinePreflight:
    def test_linux_noop(self) -> None:
        machine_mod.machine_preflight([Path("/anywhere")])

    def _darwin(self, monkeypatch: pytest.MonkeyPatch, inspect: dict | None) -> None:
        monkeypatch.setattr(machine_mod.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(machine_mod, "_machine_inspect", lambda podman: inspect)

    def test_no_machine_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._darwin(monkeypatch, None)
        with pytest.raises(MachinePreflightError, match="podman machine init"):
            machine_mod.machine_preflight([Path("/Users/x/proj")])

    def test_stopped_machine_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._darwin(monkeypatch, {"State": "stopped"})
        with pytest.raises(MachinePreflightError, match="podman machine start"):
            machine_mod.machine_preflight([Path("/Users/x/proj")])

    def test_unshared_source_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A mount source outside the VM's shares is a refusal naming the
        fix — never a silently empty mount."""
        self._darwin(
            monkeypatch,
            {"State": "running", "Mounts": [{"Source": "/Users"}]},
        )
        with pytest.raises(
            MachinePreflightError, match="podman machine set --volume"
        ):
            machine_mod.machine_preflight([Path("/Volumes/scratch/data")])

    def test_shared_sources_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._darwin(
            monkeypatch,
            {"State": "running", "Mounts": [{"Source": "/Users"}]},
        )
        machine_mod.machine_preflight([Path("/Users/x/proj")])
