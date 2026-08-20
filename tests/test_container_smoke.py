"""The runtime's answer — the one file that can say the container layer works.

Everything here builds a real image, saves a real archive into a real
annexed repository, and runs real commands through a real runtime's mount
table. Gated exactly like the sandbox enforcement suite: hosts without a
runtime skip, and ``LC_CONTAINER_TESTS_REQUIRED=1`` (set in Linux CI)
turns that skip into a hard failure, with two tests covering the guard
itself — an unfailing guard is worse than none.

Parameterized over the runtimes actually present, because a leak or an
asymmetry only docker catches is still a bug.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from lightcone.engine import assets, container, dataset, image
from lightcone.engine import materialize as engine
from lightcone.engine import run as engine_run
from lightcone.engine.project import ProjectError, child_env

REQUIRED_ENV = "LC_CONTAINER_TESTS_REQUIRED"

_SPEC = """
version: "0.0.13"
name: analysis

inputs:
  - id: catalog
    type: data
    source: data/catalog.fits

outputs:
  - id: sums
    type: metric
    recipe:
      command: echo "2+2" | bc > {output}/sum.txt
"""


def _available() -> list[str]:
    """The runtimes this host can actually run, probed once at collection."""
    found = []
    if shutil.which("podman"):
        found.append("podman")
    if (
        shutil.which("docker")
        and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    ):
        found.append("docker")
    return found


_RUNTIMES = _available()


def _gate(available: list[str]) -> None:
    if available:
        return
    if os.environ.get(REQUIRED_ENV):
        pytest.fail(
            f"{REQUIRED_ENV} is set but this host has no container runtime. "
            "Container tests must not be skipped on CI."
        )
    pytest.skip("no container runtime here")


@pytest.fixture(params=_RUNTIMES or [""])
def runtime(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """One available runtime, with the others hidden from detection."""
    _gate(_RUNTIMES)
    name = str(request.param)
    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda tool, path=None: None
        if tool in ("podman", "docker") and tool != name
        else real_which(tool, path=path),
    )
    return name


@pytest.fixture
def cproject(analysis: Callable[..., Path]) -> Path:
    """A real committed project, containerized after its scaffold commit —
    the way a real project escalates: edit pyproject, commit, build."""
    root = analysis(_SPEC, files={"data/catalog.fits": "stars\n"})
    text = (root / "pyproject.toml").read_text()
    (root / "pyproject.toml").write_text(
        text + '\n[tool.lightcone.image]\napt-install = ["bc"]\n'
    )
    dataset.save(root, [root], "containerize")
    return root


def _inspect_id(runtime: str, image_id: str) -> str:
    argv = {
        "podman": ["podman", "image", "inspect", "--format", "{{.Id}}", image_id],
        "docker": ["docker", "image", "inspect", "--format", "{{.Id}}", image_id],
    }[runtime]
    out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()
    return out.removeprefix("sha256:")


# ---- lc build ---------------------------------------------------------------


def test_build_commits_the_exact_bytes_and_leaves_the_tree_clean(
    runtime: str, cproject: Path
) -> None:
    resolved, action = container.build(cproject)

    assert action == "built"
    archive = image.archive_path(cproject, resolved.image_tag)
    assert archive.is_file()
    assert not dataset.status(cproject), "the archive commit left the tree dirty"
    # The user never sees a Containerfile — not in the tree, not beside
    # the archive.
    assert not list(cproject.rglob("Containerfile"))
    # The id read from the archive is the id the runtime computed.
    assert resolved.image_id == _inspect_id(runtime, resolved.image_id)
    # And the image carries its own identity.
    label = subprocess.run(
        [
            runtime, "image", "inspect",
            "--format", '{{index .Config.Labels "io.lightcone.image"}}',
            resolved.image_id,
        ],  # fmt: skip
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert json.loads(label) == json.loads(image.identity_document(cproject) or "")

    _, again = container.build(cproject)
    assert again == "present"


# ---- lc run: the probe ------------------------------------------------------


def test_the_probe_and_its_boundary(runtime: str, cproject: Path) -> None:
    """One image, the probe's whole contract — each denial beside the
    mutation check that proves the same reach works where it should.

    `lc run` never builds: the first probe refuses naming `lc build`, and
    succeeding after one is the mutation check. The mounts are the
    mechanism: an undeclared host file simply is not there (while the
    host itself reads it fine). And `--network none` is a real denial
    with loopback intact — the meaning of `network: denied`."""
    with pytest.raises(ProjectError, match="lc build"):
        engine_run.probe(cproject, ["bc", "--version"])

    container.build(cproject)
    outcome = engine_run.probe(cproject, ["bc", "--version"])
    assert outcome.returncode == 0
    assert outcome.attestation.mechanism == runtime
    assert outcome.attestation.network == "denied"
    assert outcome.attestation.fs == "declared"

    outside = Path.home() / ".lc-smoke-outside.txt"
    outside.write_text("host secret\n")
    try:
        denied = engine_run.probe(cproject, ["cat", str(outside)])
        assert denied.returncode != 0
        assert outside.read_text() == "host secret\n"  # the host itself can
    finally:
        outside.unlink()

    loopback = engine_run.probe(
        cproject,
        ["python", "-c", 'import socket; socket.socket().bind(("127.0.0.1", 0))'],
    )
    assert loopback.returncode == 0

    egress = engine_run.probe(
        cproject,
        [
            "python", "-c",
            "import socket, urllib.request; socket.setdefaulttimeout(3); "
            'urllib.request.urlopen("http://1.1.1.1")',
        ],  # fmt: skip
    )
    assert egress.returncode != 0


# ---- lc materialize ---------------------------------------------------------


def test_materialize_end_to_end_in_the_image(runtime: str, cproject: Path) -> None:
    """The whole layer at once: the driver builds and commits the image,
    converges the in-image environment, runs the recipe behind the mount
    table, records the runtime facts, and leaves the tree clean — through
    the real Dask cluster, not a stub."""
    report = engine.materialize(cproject, [])

    assert report.ok, report.warnings
    assert report.made == ["baseline/sums"]
    assert (cproject / "results/baseline/sums/sum.txt").read_text() == "4\n"
    assert not dataset.status(cproject)

    manifest = assets.read(cproject / "results/baseline/sums")
    assert manifest is not None
    assert manifest.hermeticity["mechanism"] == runtime
    assert manifest.hermeticity["network"] == "denied"
    assert manifest.hermeticity["fs"] == "declared"
    assert manifest.image is not None
    assert manifest.image["id"] == _inspect_id(runtime, manifest.image["id"])
    assert manifest.image["archive"] == f".datalad/environments/{manifest.image['tag']}/image"

    # The record lists the archive, so a rerun's `datalad get` fetches
    # the exact bytes before the worker runs. Parsed rather than matched
    # as text, because the record is datalad's format, not ours.
    record = dataset._git(["log", "-1", "--format=%B"], cwd=cproject)
    body = record.partition("=== Do not change lines below ===")[2].partition("^^^")[0]
    assert json.loads(body)["extra_inputs"] == [manifest.image["archive"]]


def test_a_rerun_on_a_clone_fetches_the_archive_and_reproduces(
    runtime: str,
    cproject: Path,
    tmp_path: Path,
    engine_dist: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record's whole claim, on the machine that matters: a clone with
    no annex content and no local image. `datalad rerun` fetches the
    archive through the annex (it is in `extra_inputs`), the worker loads
    it and syncs `.lightcone/venv` in-image, and the output reproduces."""
    pytest.importorskip("datalad")
    version, dist = engine_dist
    monkeypatch.setattr(engine, "_engine_requirement", lambda: f"lightcone-cli=={version}")
    report = engine.materialize(cproject, [])
    assert report.ok, report.warnings
    original = assets.read(cproject / "results/baseline/sums")
    assert original is not None

    clone = tmp_path / "clone"
    dataset._git(["clone", "-q", str(cproject), str(clone)], cwd=tmp_path)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        dataset._git(["config", key, value], cwd=clone)
    dataset._git(["annex", "init", "-q", "clone"], cwd=clone)
    # No annex content: the archive here is a pointer until rerun gets it.
    with pytest.raises(assets.ContentNotFetchedError):
        assets.require_fetched(clone / str(original.image["archive"]))  # type: ignore[index]

    proc = subprocess.run(
        [sys.executable, "-c", "from datalad.api import rerun; rerun('HEAD')"],
        cwd=clone,
        capture_output=True,
        text=True,
        env={**child_env(), "UV_FIND_LINKS": str(dist)},
    )

    assert proc.returncode == 0, proc.stderr
    rerun = assets.read(clone / "results/baseline/sums")
    assert rerun is not None
    assert rerun.data_version == original.data_version
    assert not dataset.status(clone)


# ---- the guard on this file itself -----------------------------------------


def test_the_ci_guard_fails_rather_than_skipping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one test here that must pass everywhere: CI cannot go green by
    skipping the rest. Without this, `LC_CONTAINER_TESTS_REQUIRED` is a
    comment."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    with pytest.raises(pytest.fail.Exception, match="must not be skipped"):
        _gate([])


def test_without_the_guard_a_runtimeless_host_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """And off CI it stays a skip — a laptop without podman should run the
    rest of the suite, not fail it."""
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _gate([])
