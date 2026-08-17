"""Tests for the build record, context staging, and the podman builder
(mocked subprocess; the real-build smoke lives in test_image_smoke.py)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_project

from lightcone.engine.environment import load_environment
from lightcone.engine.image import ensure_image, image_status, resolve_pinned
from lightcone.engine.image.builder import BuildContext, BuildResult
from lightcone.engine.image.builder_podman import PodmanBuilder
from lightcone.engine.image.errors import (
    AptPackageNotFoundError,
    BaseContractError,
    ImageBuildError,
    ImageMissingError,
    PodmanUnavailableError,
)
from lightcone.engine.image.record import (
    BuildRecord,
    read_record,
    write_record,
)

_RECORD = BuildRecord(
    tag="lc-env-0123456789abcdef",
    image_id="sha256:" + "aa" * 32,
    digest="sha256:" + "bb" * 32,
    platform="linux/amd64",
    env_version="sha256:" + "cc" * 32,
    lc_version="0.0.0",
    base="docker.io/library/debian:bookworm-slim@sha256:" + "dd" * 32,
    built_at="2026-08-17T00:00:00+00:00",
    dpkg_snapshot_sha256="ee" * 32,
)


class TestRecord:
    def test_round_trip(self, tmp_path: Path) -> None:
        write_record(tmp_path, _RECORD, "ii  libc6  2.36\n")
        assert read_record(tmp_path) == _RECORD
        snapshot = tmp_path / ".lightcone/image" / f"dpkg-snapshot-{_RECORD.tag}.txt"
        assert snapshot.read_text() == "ii  libc6  2.36\n"

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        assert read_record(tmp_path) is None

    def test_corrupt_returns_none(self, tmp_path: Path) -> None:
        d = tmp_path / ".lightcone/image"
        d.mkdir(parents=True)
        (d / "record.json").write_text("not json")
        assert read_record(tmp_path) is None


class TestBuildContext:
    def test_stages_exactly_three_files(self, tmp_path: Path) -> None:
        """G5 structural guarantee: no code path admits a fourth file."""
        project = make_project(tmp_path / "proj")
        ctx = BuildContext.from_project(project, "FROM x\n")
        staged = tmp_path / "staged"
        containerfile = ctx.stage(staged)
        assert sorted(p.name for p in staged.iterdir()) == [
            "Containerfile", "pyproject.toml", "uv.lock",
        ]
        assert containerfile.read_text() == "FROM x\n"
        # World-readable (rootless build under a userns).
        for p in staged.iterdir():
            assert p.stat().st_mode & 0o444 == 0o444


class _FakeBuilder:
    def __init__(self, *, exists: bool = False) -> None:
        self._exists = exists
        self.builds: list[str] = []

    def exists(self, tag: str) -> bool:
        return self._exists

    def build(self, context: BuildContext, *, tag: str) -> BuildResult:
        self.builds.append(tag)
        self._exists = True
        return BuildResult(
            tag=tag,
            image_id="sha256:" + "aa" * 32,
            digest=None,
            platform="linux/amd64",
            dpkg_snapshot_text="ii  libc6\n",
        )


class TestEnsureImage:
    def test_builds_and_records(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj", containerized=True)
        env = load_environment(project)
        builder = _FakeBuilder()
        messages: list[str] = []
        record = ensure_image(
            project, env, builder=builder, on_progress=messages.append
        )
        assert builder.builds == [record.tag]
        assert record.tag.startswith("lc-env-")
        assert record.env_version == env.env_version
        assert read_record(project) == record
        assert any("~minutes" in m for m in messages)

    def test_tag_hit_is_noop(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj", containerized=True)
        env = load_environment(project)
        builder = _FakeBuilder()
        first = ensure_image(project, env, builder=builder)
        second = ensure_image(project, env, builder=builder)
        assert builder.builds == [first.tag]  # exactly one build
        assert second == first

    def test_env_edit_rebuilds(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj", containerized=True)
        builder = _FakeBuilder()
        env = load_environment(project)
        ensure_image(project, env, builder=builder)
        (project / "uv.lock").write_text(
            (project / "uv.lock").read_text() + "# drift\n"
        )
        env2 = load_environment(project)
        record2 = ensure_image(project, env2, builder=builder)
        assert len(builder.builds) == 2
        assert builder.builds[1] == record2.tag != builder.builds[0]

    def test_force_rebuilds(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj", containerized=True)
        env = load_environment(project)
        builder = _FakeBuilder()
        ensure_image(project, env, builder=builder)
        ensure_image(project, env, builder=builder, force=True)
        assert len(builder.builds) == 2


class TestResolvePinned:
    def test_missing_image_names_lc_build(self, tmp_path: Path) -> None:
        """`lc run` never builds — the error embeds the exact command."""
        project = make_project(tmp_path / "proj", containerized=True)
        env = load_environment(project)
        with pytest.raises(ImageMissingError, match="lc build"):
            resolve_pinned(project, env, builder=_FakeBuilder())

    def test_resolves_after_build(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj", containerized=True)
        env = load_environment(project)
        builder = _FakeBuilder()
        record = ensure_image(project, env, builder=builder)
        assert resolve_pinned(project, env, builder=builder) == record

    def test_stale_record_after_env_edit_errors(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj", containerized=True)
        env = load_environment(project)
        builder = _FakeBuilder()
        ensure_image(project, env, builder=builder)
        (project / "uv.lock").write_text(
            (project / "uv.lock").read_text() + "# drift\n"
        )
        with pytest.raises(ImageMissingError):
            resolve_pinned(project, load_environment(project), builder=builder)


class TestImageStatus:
    def test_needs_build_then_built(self, tmp_path: Path) -> None:
        project = make_project(tmp_path / "proj", containerized=True)
        env = load_environment(project)
        builder = _FakeBuilder()
        s = image_status(project, env, builder=builder)
        assert not s.built and s.tag.startswith("lc-env-")
        ensure_image(project, env, builder=builder)
        s2 = image_status(project, env, builder=builder)
        assert s2.built and s2.image_id is not None


class TestPodmanBuilderErrors:
    def _builder(self) -> PodmanBuilder:
        with patch(
            "lightcone.engine.image.builder_podman.shutil.which",
            return_value="/usr/bin/podman",
        ):
            return PodmanBuilder()

    def test_missing_podman(self) -> None:
        with patch(
            "lightcone.engine.image.builder_podman.shutil.which",
            return_value=None,
        ):
            with pytest.raises(PodmanUnavailableError, match="podman.io"):
                PodmanBuilder()

    def test_apt_not_found_mapped(self) -> None:
        b = self._builder()
        with pytest.raises(AptPackageNotFoundError, match="apt-cache search rbase"):
            b._raise_mapped("", "E: Unable to locate package rbase\nexit code: 100")

    def test_contract_exit_codes_mapped(self) -> None:
        b = self._builder()
        with pytest.raises(BaseContractError, match="musl"):
            b._raise_mapped("", "…while running runtime: exit status 43")
        with pytest.raises(BaseContractError, match="Containerfile.extra"):
            b._raise_mapped("", "exit status 44")

    def test_arch_miss_mapped(self) -> None:
        b = self._builder()
        with pytest.raises(BaseContractError, match="linux/amd64"):
            b._raise_mapped(
                "", "no image found in manifest list for architecture arm64"
            )

    def test_generic_failure_bounded_tail(self) -> None:
        b = self._builder()
        noise = "\n".join(f"line {i}" for i in range(500))
        with pytest.raises(ImageBuildError) as exc:
            b._raise_mapped(noise, "")
        assert "line 499" in str(exc.value)
        assert "line 0" not in str(exc.value)

    def test_build_argv(self, tmp_path: Path) -> None:
        b = self._builder()
        project = make_project(tmp_path / "proj")
        ctx = BuildContext.from_project(project, "FROM scratch\n")
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[1] == "build":
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[1] == "image":
                return MagicMock(
                    returncode=0,
                    stdout="sha256:aa|sha256:bb|linux/amd64\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="ii libc6\n", stderr="")

        with patch(
            "lightcone.engine.image.builder_podman.subprocess.run",
            side_effect=fake_run,
        ):
            result = b.build(ctx, tag="lc-env-abc")
        build_cmd = calls[0]
        assert build_cmd[0:2] == ["podman", "build"]
        assert "--tag" in build_cmd and "lc-env-abc" in build_cmd
        # Snapshot capture runs offline with the entrypoint cleared.
        snap_cmd = calls[2]
        assert "--net=none" in snap_cmd and "--entrypoint=" in snap_cmd
        assert result.image_id == "sha256:aa"
