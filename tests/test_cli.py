"""Tests for the redesigned lightcone CLI."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~/.lightcone/`` to a temp dir so tests don't pollute the user's
    real config. The global config is auto-created on first ``lc`` invocation."""
    fake_home = tmp_path / "_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


# ---- top-level ------------------------------------------------------------


def test_help_lists_core_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "run", "status", "verify", "build"):
        assert cmd in result.output


def test_help_does_not_advertise_removed_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert "  dev " not in result.output
    assert "  cluster " not in result.output
    assert "  setup " not in result.output


def test_first_invocation_auto_creates_global_config(
    runner: CliRunner, _isolated_home: Path, tmp_path: Path
) -> None:
    config = _isolated_home / ".lightcone" / "config.yaml"
    assert not config.exists()
    # Any real subcommand triggers the group callback; ``init`` runs cleanly
    # without a pre-existing project.
    project = tmp_path / "proj"
    result = runner.invoke(
        main, ["init", str(project), "--no-git", "--no-venv"]
    )
    assert result.exit_code == 0, result.output
    assert config.exists()
    assert "runtime: auto" in config.read_text()


# ---- lc init --------------------------------------------------------------


def test_init_creates_project(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output
    assert (project / "astra.yaml").exists()
    assert (project / "CLAUDE.md").exists()
    assert (project / ".gitignore").exists()
    assert (project / ".lightcone").is_dir()
    assert (project / "results").is_dir()
    assert (project / "universes").is_dir()


def test_init_containerfile_is_gateway_worker_capable(
    runner: CliRunner, tmp_path: Path
) -> None:
    """The scaffold image must be able to run as a Dask Gateway worker.

    On the kubernetes runtime the pod image IS the recipe environment, so it
    has to carry lightcone-cli (which pulls dask/distributed/dask-gateway at
    pinned versions). A plain ``FROM python:3.12-slim`` that only installs
    requirements.txt cannot start as a worker.
    """
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output

    containerfile = (project / "Containerfile").read_text()
    assert "lightcone-cli[gateway]" in containerfile
    # uv refuses a non-venv install without --system (see plan/LCR-176).
    assert "--system" in containerfile
    # The spec points at the project Containerfile, not the slim base.
    assert "container: Containerfile" in (project / "astra.yaml").read_text()


def test_init_creates_report_template(runner: CliRunner, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code == 0, result.output

    myst_yml = (project / "myst.yml").read_text()
    assert "mystra.mjs" in myst_yml
    assert "index.md" in myst_yml

    index_md = (project / "index.md").read_text()
    assert index_md.startswith("# proj\n")
    # References must track the astra init boilerplate element ids.
    assert "{astra}`decisions.example_method`" in index_md
    assert "{astra:value}`outputs.main_result`" in index_md

    assert "_build/" in (project / ".gitignore").read_text()


def test_init_refuses_when_astra_yaml_exists(
    runner: CliRunner, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text("# already here\n")
    result = runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_init_venv_uses_uv_when_available(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output

    assert ["uv", "venv", "--python", "3.12", ".venv"] in calls
    assert ["uv", "pip", "install", "--python", ".venv/bin/python", "lightcone-cli"] in calls


def test_init_venv_falls_back_to_python_when_uv_missing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    project = tmp_path / "proj"
    result = runner.invoke(main, ["init", str(project), "--no-git"])
    assert result.exit_code == 0, result.output

    assert ["python", "-m", "venv", ".venv"] in calls
    assert [".venv/bin/python", "-m", "pip", "install", "-q", "lightcone-cli"] in calls


# ---- lc verify ------------------------------------------------------------


def test_verify_clean_project_returns_zero(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty project (no materialized outputs yet) is a clean state, not
    a verification failure."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text(
        "outputs:\n  - id: foo\n    recipe:\n      command: echo\n"
    )
    monkeypatch.chdir(project)
    result = runner.invoke(main, ["verify"])
    assert result.exit_code == 0


# ---- lc run command building ------------------------------------------------


def test_run_cmd_inserts_separator_before_targets() -> None:
    """Regression test for issue #87.

    snakemake's --rerun-triggers uses nargs=+ so it greedily consumes the
    first positional target path as an extra trigger value, producing:
        error: argument --rerun-triggers: invalid choice:
        'results/baseline/map_fit/.lightcone-manifest.json'
    A '--' separator between the trigger values and target paths terminates
    argparse flag processing and prevents this.
    """
    from lightcone.cli.commands import _build_snakemake_cmd

    targets = ["results/baseline/map_fit/.lightcone-manifest.json"]
    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/proj/.lightcone/Snakefile"),
        project=Path("/proj"),
        n="4",
        rerun_triggers="code,input,mtime,params",
        targets=targets,
        force=False,
        has_outputs=True,
    )

    assert "--" in cmd, "missing '--' separator; first target will be consumed as a trigger value"
    sep_idx = cmd.index("--")
    rt_idx = cmd.index("--rerun-triggers")
    assert sep_idx > rt_idx, "'--' must appear after --rerun-triggers"
    target_idx = cmd.index(targets[0])
    assert target_idx > sep_idx, "target path must appear after '--'"


def test_run_cmd_no_separator_when_no_targets() -> None:
    """When no targets are supplied snakemake runs 'rule all'; '--' is unnecessary."""
    from lightcone.cli.commands import _build_snakemake_cmd

    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/proj/.lightcone/Snakefile"),
        project=Path("/proj"),
        n="4",
        rerun_triggers="code,input,mtime,params",
        targets=[],
        force=False,
        has_outputs=False,
    )

    assert "--" not in cmd


def test_run_cmd_gateway_scopes_shared_fs_and_latency() -> None:
    """Gateway workers share only the project volume with the driver, not
    its HOME (source cache) or install prefix, and see it through NFS.
    Validated on the lightcone-hub deployment: without --shared-fs-usage
    the child snakemake mkdir's the driver's ~/.cache path (PermissionError
    in the worker pod), and without --latency-wait the driver's NFS
    attribute cache declares freshly written outputs missing."""
    from lightcone.cli.commands import _build_snakemake_cmd

    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/shared/proj/.lightcone/Snakefile"),
        project=Path("/shared/proj"),
        n="4",
        rerun_triggers="code,input,mtime,params",
        targets=[],
        force=False,
        has_outputs=False,
        gateway=True,
    )

    fs_idx = cmd.index("--shared-fs-usage")
    values = cmd[fs_idx + 1 : fs_idx + 5]
    assert set(values) == {
        "input-output",
        "persistence",
        "sources",
        "storage-local-copies",
    }
    assert "source-cache" not in cmd, "driver ~/.cache is not visible to worker pods"
    assert "software-deployment" not in cmd, "driver interpreter path differs in pods"
    assert cmd[cmd.index("--latency-wait") + 1] == "60"


def test_run_cmd_default_keeps_snakemake_shared_fs_defaults() -> None:
    """Local/SLURM workers genuinely share the driver's environment —
    the gateway-only flags must not leak into those paths."""
    from lightcone.cli.commands import _build_snakemake_cmd

    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/proj/.lightcone/Snakefile"),
        project=Path("/proj"),
        n="4",
        rerun_triggers="code,input,mtime,params",
        targets=[],
        force=False,
        has_outputs=False,
    )

    assert "--shared-fs-usage" not in cmd
    assert "--latency-wait" not in cmd


def test_gateway_branch_active_matches_cluster_for_run_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gateway_branch_active must mirror cluster_for_run's branch order:
    an explicit scheduler address outranks the gateway environment."""
    from lightcone.engine.dask_cluster import gateway_branch_active

    for var in (
        "DASK_SCHEDULER_ADDRESS",
        "DASK_GATEWAY__ADDRESS",
        "LIGHTCONE_GATEWAY_CLUSTER",
    ):
        monkeypatch.delenv(var, raising=False)
    assert gateway_branch_active() is False

    monkeypatch.setenv("DASK_GATEWAY__ADDRESS", "http://proxy/services/dask-gateway/")
    assert gateway_branch_active() is True

    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://existing:8786")
    assert gateway_branch_active() is False


def test_run_cmd_multiple_triggers_all_before_separator() -> None:
    """All four trigger tokens must precede the '--' separator."""
    from lightcone.cli.commands import _build_snakemake_cmd

    targets = ["results/baseline/out/.lightcone-manifest.json"]
    cmd = _build_snakemake_cmd(
        snakefile_path=Path("/proj/.lightcone/Snakefile"),
        project=Path("/proj"),
        n="1",
        rerun_triggers="code,input,mtime,params",
        targets=targets,
        force=False,
        has_outputs=True,
    )

    sep_idx = cmd.index("--")
    for trigger in ("code", "input", "mtime", "params"):
        assert trigger in cmd, f"trigger '{trigger}' missing from cmd"
        assert cmd.index(trigger) < sep_idx, f"trigger '{trigger}' must come before '--'"


# ---- lc build / lc run worker image on a hub (kubernetes + BinderHub) ------


@pytest.fixture
def hub_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A minimal project declaring a project-level Containerfile."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text(
        "name: proj\n"
        "container: Containerfile\n"
        "outputs:\n  - id: foo\n    recipe:\n      command: echo\n"
    )
    (project / "Containerfile").write_text("FROM python:3.12-slim\n")
    monkeypatch.chdir(project)
    return project


def test_build_kubernetes_builds_via_hub_service(
    runner: CliRunner, hub_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a hub (BinderHub reachable) `lc build` drives a real image build
    instead of printing off-hub publish instructions."""
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")
    captured: dict[str, object] = {}

    def fake_ensure(project, spec, *, commit=True, on_progress=None):  # noqa: ANN001, ANN202
        captured["spec"] = spec
        captured["commit"] = commit
        return "reg/binder/proj:abc123"

    monkeypatch.setattr(
        "lightcone.engine.binder.ensure_worker_image", fake_ensure
    )
    result = runner.invoke(main, ["build", "--runtime", "kubernetes"])
    assert result.exit_code == 0, result.output
    assert "reg/binder/proj:abc123" in result.output
    assert captured == {"spec": "Containerfile", "commit": True}


def test_build_kubernetes_no_commit_flag(
    runner: CliRunner, hub_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")
    captured: dict[str, object] = {}

    def fake_ensure(project, spec, *, commit=True, on_progress=None):  # noqa: ANN001, ANN202
        captured["commit"] = commit
        return "reg/binder/proj:abc123"

    monkeypatch.setattr(
        "lightcone.engine.binder.ensure_worker_image", fake_ensure
    )
    result = runner.invoke(
        main, ["build", "--runtime", "kubernetes", "--no-commit"]
    )
    assert result.exit_code == 0, result.output
    assert captured["commit"] is False


def test_build_kubernetes_build_error_is_user_facing(
    runner: CliRunner, hub_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")

    def fake_ensure(project, spec, *, commit=True, on_progress=None):  # noqa: ANN001, ANN202
        from lightcone.engine.binder import BinderBuildError

        raise BinderBuildError("the build broke")

    monkeypatch.setattr(
        "lightcone.engine.binder.ensure_worker_image", fake_ensure
    )
    result = runner.invoke(main, ["build", "--runtime", "kubernetes"])
    assert result.exit_code != 0
    assert "the build broke" in result.output
    assert "Traceback" not in result.output


def test_build_kubernetes_off_hub_falls_back_to_registry_report(
    runner: CliRunner, hub_project: Path
) -> None:
    """Without a reachable BinderHub service (e.g. kubernetes runtime
    configured off-hub) the old passive registry report still runs."""
    result = runner.invoke(main, ["build", "--runtime", "kubernetes"])
    assert result.exit_code == 0, result.output
    assert "not built" in result.output


def test_worker_image_for_run_ensures_via_binder(
    hub_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.cli.commands import _worker_image_for_run

    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")
    monkeypatch.setattr(
        "lightcone.engine.binder.ensure_worker_image",
        lambda p, s, *, commit=True, on_progress=None: "reg/binder/proj:sha1",
    )
    assert (
        _worker_image_for_run(hub_project, verbose=False)
        == "reg/binder/proj:sha1"
    )


def test_worker_image_for_run_registry_spec_passes_through(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared registry image needs no build — used as the worker image
    verbatim, even on a hub."""
    from lightcone.cli.commands import _worker_image_for_run

    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text(
        "name: proj\n"
        "container: ghcr.io/org/worker:1.0\n"
        "outputs:\n  - id: foo\n    recipe:\n      command: echo\n"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")

    def _boom(*args: object, **kwargs: object) -> str:
        raise AssertionError("registry specs must not trigger a build")

    monkeypatch.setattr("lightcone.engine.binder.ensure_worker_image", _boom)
    assert (
        _worker_image_for_run(project, verbose=False) == "ghcr.io/org/worker:1.0"
    )


def test_worker_image_for_run_no_container_uses_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lightcone.cli.commands import _worker_image_for_run

    project = tmp_path / "proj"
    project.mkdir()
    (project / "astra.yaml").write_text(
        "name: proj\noutputs:\n  - id: foo\n    recipe:\n      command: echo\n"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "tok")
    assert _worker_image_for_run(project, verbose=False) is None
