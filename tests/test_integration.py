"""Integration tests for the Dagster execution layer."""

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def project_dir(tmp_path, runner):
    """Create a project with init, then add inline recipes."""
    project = tmp_path / "test-project"
    runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])

    # Overwrite astra.yaml with recipes
    (project / "astra.yaml").write_text("""
version: "1.0"
name: "Integration Test"
inputs:
  - id: raw_data
    type: data
outputs:
  - id: cleaned
    type: data
    recipe:
      command: python scripts/clean.py
      container: python:3.11
  - id: result
    type: metric
    recipe:
      command: python scripts/analyze.py
      inputs: [cleaned]
      container: python:3.11
decisions: {}
""")
    return project


class TestIntegration:
    def test_init_creates_dagster_yaml(self, project_dir):
        assert (project_dir / ".lightcone" / "dagster.yaml").exists()

    def test_init_creates_project_structure(self, project_dir):
        assert (project_dir / "astra.yaml").exists()
        assert (project_dir / "universes").is_dir()
        assert (project_dir / "results").is_dir()
        assert (project_dir / "scripts").is_dir()
        assert (project_dir / ".lightcone").is_dir()

    def test_init_creates_containerfile(self, tmp_path, runner):
        project = tmp_path / "container-project"
        runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
        assert (project / "Containerfile").exists()
        content = (project / "Containerfile").read_text()
        assert "FROM python:3.12-slim" in content

    def test_init_creates_requirements_txt(self, tmp_path, runner):
        project = tmp_path / "req-project"
        runner.invoke(main, ["init", str(project), "--no-git", "--no-venv"])
        assert (project / "requirements.txt").exists()
        content = (project / "requirements.txt").read_text()
        assert "numpy" in content

    def test_status_shows_pending(self, project_dir):
        """Status should show 'pending' when nothing has been materialized."""
        # Ensure a universe exists
        (project_dir / "universes" / "baseline.yaml").write_text(
            "id: baseline\ndecisions: {}\n"
        )
        from lightcone.engine.status import get_output_status

        status = get_output_status(project_dir, "baseline")
        assert status["cleaned"] == "pending"
        assert status["result"] == "pending"

    def test_status_shows_materialized(self, project_dir, monkeypatch):
        """Status should show 'materialized' when Dagster event exists."""
        import dagster as dg
        from conftest import materialize_via_dagster

        (project_dir / "universes" / "baseline.yaml").write_text(
            "id: baseline\ndecisions: {}\n"
        )

        # chdir so relative paths in dagster.yaml resolve to project_dir
        monkeypatch.chdir(project_dir)

        instance = dg.DagsterInstance.from_config(str(project_dir / ".lightcone"))
        materialize_via_dagster(instance, "baseline", "cleaned")

        from lightcone.engine.status import get_output_status

        status = get_output_status(project_dir, "baseline", instance=instance)
        assert status["cleaned"] == "materialized"
        assert status["result"] == "pending"

    def test_status_cli_output(self, project_dir, runner, monkeypatch):
        """lc status should run without error."""
        monkeypatch.chdir(project_dir)
        (project_dir / "universes" / "baseline.yaml").write_text(
            "id: baseline\ndecisions: {}\n"
        )
        result = runner.invoke(main, ["status", "--universe", "baseline"])
        assert result.exit_code == 0
        assert "cleaned" in result.output or "pending" in result.output

    def test_cluster_save_and_load(self, tmp_path):
        """Cluster config round-trip via the public CRUD API."""
        from lightcone.engine.clusters import (
            list_clusters,
            load_cluster_config,
            save_cluster_config,
        )

        save_cluster_config("test-cluster", {
            "site": "perlmutter",
            "account": "m1234",
            "workers": [{"nodes": 1}],
        })
        loaded = load_cluster_config("test-cluster")
        assert loaded is not None
        assert loaded["site"] == "perlmutter"
        assert list_clusters() == ["test-cluster"]

    def test_io_manager_paths(self, project_dir):
        """IO manager should produce correct paths."""
        from lightcone.engine.io_manager import ASTRAIOManager

        mgr = ASTRAIOManager(project_root=str(project_dir))
        path = mgr.get_output_path("cleaned", "baseline")
        assert path == project_dir / "results" / "baseline" / "cleaned"

    def test_runner_executes_in_venv(self, project_dir):
        """Runner should fall back to venv execution without a container runtime."""
        import subprocess
        import sys

        from lightcone.engine.runner import ASTRAContainerRunner

        # Create a minimal .venv so the venv backend can use it
        subprocess.run(
            [sys.executable, "-m", "venv", str(project_dir / ".venv")],
            check=True, capture_output=True,
        )

        runner = ASTRAContainerRunner(
            project_root=str(project_dir),
            backend="venv",
        )
        result = runner.execute(
            command="python -c 'print(1)'",
            output_id="cleaned",
            universe_id="baseline",
        )
        assert result.exit_code == 0
        assert result.metadata.get("backend") == "venv"
