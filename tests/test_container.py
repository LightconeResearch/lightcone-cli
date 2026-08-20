"""Tests for `lightcone.engine.container` — the image lifecycle, stubbed.

Every runtime command goes through `project._run`, so these tests hand it
a fake that models each command's observable effect and records every
argv — the same discipline as convergence's `tools` fixture. What a real
runtime does with the argv is `test_container_smoke.py`'s question.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lightcone.engine import container, image, project
from lightcone.engine.project import ProjectError

_TABLE = '[tool.lightcone.image]\napt-install = ["bc"]\n'
_CONFIG = b'{"architecture":"amd64","os":"linux"}'


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project_dir = tmp_path / "analysis"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(
        '[project]\nname = "analysis"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n' + _TABLE
    )
    (project_dir / ".python-version").write_text("3.12.11\n")
    return project_dir


def _write_archive(path: Path, config: bytes = _CONFIG) -> str:
    """Write a minimal but structurally real docker-archive at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as tar:
        for name, data in (
            ("abc.json", config),
            ("manifest.json", json.dumps([{"Config": "abc.json", "RepoTags": []}]).encode()),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    import hashlib

    return hashlib.sha256(config).hexdigest()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """A podman-having host: records argv, models each command's effect.

    Every tool name resolves except ``podman-hpc`` — a site wrapper no
    laptop has, and it would win detection everywhere. The host's
    architecture is pinned to the test archives' ``amd64`` so the arch
    gate answers the same on every CI machine.
    """
    calls: list[list[str]] = []
    loaded: set[str] = set()

    def run(argv: list[str], *, cwd: Path) -> MagicMock:
        calls.append(list(argv))
        if argv[0] in ("podman", "docker", "podman-hpc"):
            if argv[1] == "image":  # the loaded probe, both spellings
                return MagicMock(returncode=0 if argv[3] in loaded else 1)
            if argv[1] == "load":
                loaded.add(container.archive_identity(Path(argv[3]))[0])
                return MagicMock(returncode=0, stdout="", stderr="")
            if argv[1] == "save":
                _write_archive(Path(argv[argv.index("-o") + 1]))
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        if argv[:2] == ["uv", "cache"]:
            return MagicMock(returncode=0, stdout="/home/user/.cache/uv\n", stderr="")
        if argv[:3] == ["git", "diff", "--cached"]:
            return MagicMock(returncode=1)  # something staged: commits proceed
        if argv[:2] == ["git", "check-attr"]:
            return MagicMock(
                returncode=0, stdout=f"{argv[-1]}: annex.largefiles: anything\n", stderr=""
            )
        if argv[:3] == ["git", "annex", "get"]:
            _write_archive(cwd / argv[-1])  # the fetch's observable effect
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(project, "_run", run)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, path=None: None if name == "podman-hpc" else f"/usr/bin/{name}",
    )
    monkeypatch.setattr(container.platform, "machine", lambda: "x86_64")
    return calls


@pytest.fixture
def hpc(
    fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> list[list[str]]:
    """The same host with the site's podman-hpc wrapper installed too."""
    monkeypatch.setattr(shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    return fake


def _argvs(calls: list[list[str]], *head: str) -> list[list[str]]:
    return [c for c in calls if c[: len(head)] == list(head)]


# ---- runtime detection ------------------------------------------------------


def test_podman_is_preferred(root: Path, fake: list[list[str]]) -> None:
    assert container.runtime_name(root) == "podman"


def test_docker_without_its_daemon_is_a_refusal(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`docker` on PATH with the daemon down is the common broken state;
    'cannot connect to the socket' mid-run is a worse message."""
    monkeypatch.setattr(
        shutil, "which", lambda name, path=None: f"/usr/bin/{name}" if name == "docker" else None
    )
    monkeypatch.setattr(
        project, "_run", lambda argv, *, cwd: MagicMock(returncode=1, stdout="", stderr="")
    )
    with pytest.raises(ProjectError, match="daemon"):
        container.runtime_name(root)


def test_no_runtime_is_a_refusal_naming_both(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None)
    with pytest.raises(ProjectError, match="podman"):
        container.runtime_name(root)


# ---- the three strictnesses -------------------------------------------------


def test_a_direct_project_resolves_without_touching_a_runtime(
    tmp_path: Path, fake: list[list[str]]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')

    runtime = container.runtime_for_run(plain, build=False)

    assert runtime.mode == "direct"
    assert runtime.env_dir == plain / ".venv"
    assert fake == []


def test_a_missing_archive_refuses_unless_the_caller_may_build(
    root: Path, fake: list[list[str]]
) -> None:
    """`lc run` never builds and the worker never writes git — and the
    mutation check: the same state under a build-allowed caller succeeds."""
    with pytest.raises(ProjectError, match="lc build"):
        container.runtime_for_run(root, build=False)
    assert _argvs(fake, "podman", "build") == []
    assert _argvs(fake, "git", "commit") == []

    runtime = container.runtime_for_run(root, build=True)

    assert runtime.mode == "containerized"
    assert runtime.image_tag == image.tag(root)
    assert container.runtime_for_run(root, build=False).image_id == runtime.image_id


def test_unfetched_archive_content_is_fetched_by_lc_itself(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody is ever asked to run a git-annex command by hand — the
    storage invariant. lc gets its own artifact; the refusal is reserved
    for a fetch with no reachable copy, which is the second half."""
    archive = image.archive_path(root, image.tag(root))
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"/annex/objects/SHA256E-s323--abc\n")  # the pointer shape

    runtime = container.runtime_for_run(root, build=False)

    assert runtime.image_id
    (got,) = [c for c in fake if c[:3] == ["git", "annex", "get"]]
    assert got[-1] == f".datalad/environments/{image.tag(root)}/image"

    archive.write_bytes(b"/annex/objects/SHA256E-s323--abc\n")  # unfetched again
    inner = project._run

    def failing(argv: list[str], *, cwd: Path) -> MagicMock:
        if argv[:3] == ["git", "annex", "get"]:
            return MagicMock(returncode=1, stdout="", stderr="no reachable copy")
        return inner(argv, cwd=cwd)

    monkeypatch.setattr(project, "_run", failing)
    with pytest.raises(ProjectError, match="fetching it failed"):
        container.runtime_for_run(root, build=False)


def test_a_present_archive_is_loaded_once_and_reused(
    root: Path, fake: list[list[str]]
) -> None:
    expected = _write_archive(image.archive_path(root, image.tag(root)))

    first = container.runtime_for_run(root, build=False)
    second = container.runtime_for_run(root, build=False)

    assert first.image_id == expected and second.image_id == expected
    assert first.arch == "amd64"
    assert first.archive == f".datalad/environments/{image.tag(root)}/image"
    assert len(_argvs(fake, "podman", "load")) == 1  # the second run hit the store


# ---- building ---------------------------------------------------------------


def test_the_build_context_holds_only_the_containerfile(
    root: Path, fake: list[list[str]]
) -> None:
    """No project file ever enters the context — what makes 'code edits
    never trigger a build' structural rather than observed."""
    container.runtime_for_run(root, build=True)

    (build,) = _argvs(fake, "podman", "build")
    context = Path(build[-1])
    assert not context.is_relative_to(root)
    containerfile = Path(build[build.index("-f") + 1])
    assert containerfile.name == "Containerfile"
    assert build[build.index("-t") + 1] == image.tag(root)


def test_the_build_saves_and_commits_the_archive(root: Path, fake: list[list[str]]) -> None:
    """The dataset is the image store: the archive and the datalad
    containers config land in one scoped commit."""
    runtime = container.runtime_for_run(root, build=True)

    (save,) = _argvs(fake, "podman", "save")
    # Saved beside its final name and renamed into place, so a save that
    # dies midway leaves no partial archive for the dirty refusal to
    # tell the user to commit.
    archive = image.archive_path(root, runtime.image_tag)
    assert save[save.index("-o") + 1] == str(archive.parent / "image.partial")
    assert archive.is_file() and not (archive.parent / "image.partial").exists()
    assert "--format" in save and save[save.index("--format") + 1] == "docker-archive"
    configured = {c[-2] for c in _argvs(fake, "git", "config", "-f", ".datalad/config")}
    assert f"datalad.containers.{runtime.image_tag}.image" in configured
    assert f"datalad.containers.{runtime.image_tag}.cmdexec" in configured
    (add,) = [c for c in fake if c[0] == "git" and "add" in c]
    assert f".datalad/environments/{runtime.image_tag}" in " ".join(add)
    # The dot-path routing: without it the archive is a full blob in git.
    assert "annex.dotfiles=true" in add
    assert len(_argvs(fake, "git", "commit")) == 1


def test_an_unrouted_archive_refuses_before_building(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive is committed, and `.gitattributes` is a user-authored
    file lc only appends to — so an archive it does not route to the
    annex would land in git as a several-hundred-MB blob, silently. The
    probe fires before any build is paid for. Mutation check: the `fake`
    fixture's routed answer is what every other build test passes with."""
    original = project._run

    def unrouted(argv: list[str], *, cwd: Path) -> MagicMock:
        if argv[:2] == ["git", "check-attr"]:
            return MagicMock(
                returncode=0, stdout=f"{argv[-1]}: annex.largefiles: unspecified\n", stderr=""
            )
        return original(argv, cwd=cwd)

    monkeypatch.setattr(project, "_run", unrouted)
    with pytest.raises(ProjectError, match="annex.largefiles=anything"):
        container.runtime_for_run(root, build=True)
    assert _argvs(fake, "podman", "build") == []


def test_a_bad_apt_name_is_parsed_out_of_the_build_log(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _failing_build("E: Unable to locate package texlive-latex-bass", monkeypatch)
    with pytest.raises(ProjectError, match="texlive-latex-bass"):
        container.runtime_for_run(root, build=True)


def _failing_build(stderr: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route only the build through failure; everything else keeps the
    `fake` fixture's modelled answers (the routing probe included)."""
    from lightcone.engine import project as project_module

    inner = project_module._run

    def failing(argv: list[str], *, cwd: Path) -> MagicMock:
        if argv[:2] == ["podman", "build"]:
            return MagicMock(returncode=1, stdout="", stderr=stderr)
        return inner(argv, cwd=cwd)

    monkeypatch.setattr(project_module, "_run", failing)


@pytest.mark.parametrize(
    ("code", "instruction", "expected"),
    [
        ("43", "RUN if ldd --version 2>&1 | grep -qi musl", "glibc"),
        ("44", "RUN command -v bash >/dev/null", "bash"),
        ("45", "RUN command -v apt-get >/dev/null", "apt"),
    ],
)
def test_a_contract_violation_names_the_base_not_the_log(
    root: Path,
    fake: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    instruction: str,
    expected: str,
) -> None:
    _failing_build(
        f'Error: building at STEP "{instruction}": while running runtime: '
        f"exit status {code}",
        monkeypatch,
    )
    with pytest.raises(ProjectError, match=expected):
        container.runtime_for_run(root, build=True)


def test_a_run_command_exiting_a_contract_code_is_not_misdiagnosed(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """curl exits 43 for its own reasons; blaming the base for it would
    point the user away from their own failing command. The anchor is
    the failing instruction, not the code alone."""
    _failing_build(
        'Error: building at STEP "RUN curl -fsSL https://example.org/tool.tar": '
        "exit status 43",
        monkeypatch,
    )
    with pytest.raises(ProjectError, match="curl") as raised:
        container.runtime_for_run(root, build=True)
    assert "musl" not in str(raised.value)


def test_lc_build_refuses_a_dirty_tree(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive commit takes the whole index with it, and the tag
    derives from pyproject.toml — the declaration commits first."""

    def dirty(argv: list[str], *, cwd: Path) -> MagicMock:
        if argv[:2] == ["git", "status"]:
            return MagicMock(returncode=0, stdout=" M pyproject.toml\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(project, "_run", dirty)
    with pytest.raises(ProjectError, match="[Cc]ommit"):
        container.build(root)


def test_lc_build_is_idempotent(root: Path, fake: list[list[str]]) -> None:
    _, first = container.build(root)
    _, second = container.build(root)
    assert (first, second) == ("built", "present")
    assert len(_argvs(fake, "podman", "build")) == 1


def test_lc_build_on_a_direct_project_says_so(tmp_path: Path, fake: list[list[str]]) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')
    with pytest.raises(ProjectError, match="direct mode"):
        container.build(plain)


# ---- the containerized converge ---------------------------------------------


def test_sync_runs_uv_inside_the_image_with_the_host_cache(
    root: Path, fake: list[list[str]]
) -> None:
    runtime = container.runtime_for_run(root, build=True)
    fake.clear()

    container.sync(root, runtime)

    (sync,) = _argvs(fake, "podman", "run")
    assert f"{root}:{root}:rw" in " ".join(sync)
    assert "/home/user/.cache/uv:/home/user/.cache/uv:rw" in " ".join(sync)
    assert f"UV_PROJECT_ENVIRONMENT={root / '.lightcone' / 'venv'}" in " ".join(sync)
    assert "--userns=keep-id" in sync
    assert runtime.image_id in sync
    tail = sync[sync.index(runtime.image_id) + 1 :]
    assert tail[:2] == ["uv", "sync"]
    assert "--locked" in tail and "--exact" in tail and "--compile-bytecode" in tail


# ---- the podman machine (macOS) ---------------------------------------------


def _darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(container.sys, "platform", "darwin")
    assert sys.platform  # the global module object is patched; reverted after


def test_no_podman_machine_is_a_refusal_naming_the_setup(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _darwin(monkeypatch)
    monkeypatch.setattr(
        project, "_run", lambda argv, *, cwd: MagicMock(returncode=1, stdout="", stderr="")
    )
    with pytest.raises(ProjectError, match="podman machine init"):
        container.runtime_name(root)


def test_a_project_outside_the_machine_shares_is_a_refusal(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bind mount from outside the VM's shared directories arrives
    *empty* — no error, just a project with nothing in it — so the
    preflight names the exact `podman machine set`. The mutation check:
    a share that covers the project passes."""
    _darwin(monkeypatch)

    def machine(shares: list[str]) -> None:
        inspect = json.dumps(
            [{"State": "running", "Mounts": [{"Source": s} for s in shares]}]
        )
        monkeypatch.setattr(
            project,
            "_run",
            lambda argv, *, cwd: MagicMock(returncode=0, stdout=inspect, stderr=""),
        )

    machine(["/Users"])
    with pytest.raises(ProjectError, match="podman machine set"):
        container.runtime_name(root)

    # A machine sharing nothing at all is the same refusal, not a pass.
    machine([])
    with pytest.raises(ProjectError, match="podman machine set"):
        container.runtime_name(root)

    machine([str(root.parent)])
    assert container.runtime_name(root) == "podman"


def test_a_stopped_machine_is_a_refusal_naming_start(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`podman machine inspect` succeeds on a stopped machine — without
    the state check the preflight passes and the run dies later on a raw
    connection error, far from the one-command fix."""
    _darwin(monkeypatch)
    inspect = json.dumps([{"State": "stopped", "Mounts": [{"Source": "/Users"}]}])
    monkeypatch.setattr(
        project,
        "_run",
        lambda argv, *, cwd: MagicMock(returncode=0, stdout=inspect, stderr=""),
    )
    with pytest.raises(ProjectError, match="podman machine start"):
        container.runtime_name(root)


# ---- state for status and the CLI -------------------------------------------


def test_image_state_reports_repository_facts_only(root: Path, fake: list[list[str]]) -> None:
    tag = image.tag(root)
    relative = f".datalad/environments/{tag}/image"
    assert container.image_state(root) == ("absent", tag, relative)

    archive = image.archive_path(root, tag)
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"/annex/objects/SHA256E-s1--abc\n")
    assert container.image_state(root)[0] == "unfetched"

    _write_archive(archive)
    assert container.image_state(root)[0] == "present"
    assert not any(c[0] in ("podman", "docker") for c in fake)


def test_archive_identity_matches_the_runtime_id_computation(tmp_path: Path) -> None:
    """The same sha256-of-the-config-blob podman and docker report, and
    the value the smoke test compares against a real `podman inspect`."""
    config = b'{"architecture":"arm64"}'
    expected = _write_archive(tmp_path / "image", config)
    found, arch = container.archive_identity(tmp_path / "image")
    assert found == expected
    assert arch == "arm64"


def test_a_garbled_archive_is_a_pointed_refusal(tmp_path: Path) -> None:
    (tmp_path / "image").write_bytes(b"not a tar at all" * 4096)
    with pytest.raises(ProjectError, match="lc build"):
        container.archive_identity(tmp_path / "image")


# ---- podman-hpc -------------------------------------------------------------


def test_podman_hpc_is_preferred_where_present(root: Path, hpc: list[list[str]]) -> None:
    """Where the site wrapper exists, the bare podman beside it is the one
    whose store compute nodes cannot see."""
    assert container.runtime_hint() == "podman-hpc"
    assert container.runtime_name(root) == "podman-hpc"


def test_podman_hpc_loads_then_migrates(root: Path, hpc: list[list[str]]) -> None:
    expected = _write_archive(image.archive_path(root, image.tag(root)))

    runtime = container.runtime_for_run(root, build=False)

    assert runtime.runtime == "podman-hpc"
    assert _argvs(hpc, "podman-hpc", "image") == [["podman-hpc", "image", "exists", expected]]
    assert len(_argvs(hpc, "podman-hpc", "load")) == 1
    assert _argvs(hpc, "podman-hpc", "migrate") == [["podman-hpc", "migrate", expected]]


def test_migrate_runs_even_when_the_store_already_holds_the_image(
    root: Path, hpc: list[list[str]]
) -> None:
    """The migrate lives outside the load branch: a store hit (or a fresh
    build) must still put the squashed copy where compute nodes look."""
    _write_archive(image.archive_path(root, image.tag(root)))

    container.runtime_for_run(root, build=False)
    container.runtime_for_run(root, build=False)

    assert len(_argvs(hpc, "podman-hpc", "load")) == 1
    assert len(_argvs(hpc, "podman-hpc", "migrate")) == 2


def test_podman_hpc_builds_saves_and_migrates(root: Path, hpc: list[list[str]]) -> None:
    """The wrapper is build-capable — NERSC login nodes are where the
    matching-arch archive comes from — and a fresh build still migrates."""
    runtime, verdict = container.build(root)

    assert verdict == "built"
    assert len(_argvs(hpc, "podman-hpc", "build")) == 1
    (save,) = _argvs(hpc, "podman-hpc", "save")
    assert save[2:4] == ["--format", "docker-archive"]
    assert _argvs(hpc, "podman-hpc", "migrate") == [["podman-hpc", "migrate", runtime.image_id]]


def test_podman_hpc_keeps_the_uid_and_forbids_pulling(root: Path, hpc: list[list[str]]) -> None:
    _write_archive(image.archive_path(root, image.tag(root)))
    runtime = container.runtime_for_run(root, build=False)

    assert container.uid_flags("podman-hpc") == ["--userns=keep-id"]
    backend = container.backend(runtime)
    assert backend.capability.kind == "podman-hpc"
    assert "--pull=never" in backend.user_flags  # type: ignore[attr-defined]
    assert "--userns=keep-id" in backend.user_flags  # type: ignore[attr-defined]


# ---- the architecture gate --------------------------------------------------


def test_a_foreign_arch_archive_refuses_before_loading(
    root: Path, fake: list[list[str]]
) -> None:
    """A wrong-arch load *succeeds* and then dies as `exec format error`
    deep inside a recipe — so the refusal comes first, naming the fix."""
    _write_archive(
        image.archive_path(root, image.tag(root)), b'{"architecture":"arm64","os":"linux"}'
    )

    with pytest.raises(ProjectError, match="built for arm64 and this host is amd64"):
        container.runtime_for_run(root, build=False)

    assert _argvs(fake, "podman", "load") == []


def test_the_matching_arch_loads(root: Path, fake: list[list[str]]) -> None:
    _write_archive(image.archive_path(root, image.tag(root)))

    assert container.runtime_for_run(root, build=False).arch == "amd64"
    assert len(_argvs(fake, "podman", "load")) == 1


def test_an_unknown_arch_is_not_refused(
    root: Path, fake: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ignorance is not a mismatch — an archive that does not say, or a
    host machine outside the map, passes rather than refusing blind."""
    _write_archive(image.archive_path(root, image.tag(root)), b'{"os":"linux"}')
    container.runtime_for_run(root, build=False)

    monkeypatch.setattr(container.platform, "machine", lambda: "riscv64")
    _write_archive(image.archive_path(root, image.tag(root)))
    container.runtime_for_run(root, build=False)
