"""Tests for Prism CLI commands."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from prism.cli import main


@pytest.fixture
def runner():
    """Return a CLI runner."""
    return CliRunner()


class TestInitCommand:
    """Tests for the prism init command."""

    def test_init_creates_project_structure(self, runner: CliRunner, tmp_path: Path):
        """Test that basic init creates the project structure."""
        project_dir = tmp_path / "my-analysis"
        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv", "--permissions", "recommended"],
        )
        assert result.exit_code == 0
        assert "Created ASTRA analysis project" in result.output

        # Check directory structure
        assert (project_dir / "astra.yaml").exists()
        assert (project_dir / ".gitignore").exists()
        assert (project_dir / "universes").is_dir()
        assert (project_dir / "universes" / "baseline.yaml").exists()
        assert (project_dir / "scripts").is_dir()
        assert (project_dir / "results").is_dir()

    def test_init_astra_yaml_content(self, runner: CliRunner, tmp_path: Path):
        """Test that the generated astra.yaml has the expected content."""
        project_dir = tmp_path / "content-test"
        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv", "--permissions", "recommended"],
        )
        assert result.exit_code == 0

        content = (project_dir / "astra.yaml").read_text()
        assert "content-test" in content
        assert "version:" in content
        assert "name:" in content
        assert "description:" in content
        assert "decisions:" in content

    def test_init_gitignore_content(self, runner: CliRunner, tmp_path: Path):
        """Test gitignore content."""
        project_dir = tmp_path / "gitignore-test"
        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv", "--permissions", "recommended"],
        )
        assert result.exit_code == 0

        gitignore = (project_dir / ".gitignore").read_text()
        assert "results/" in gitignore
        assert "__pycache__/" in gitignore

    def test_init_refuses_if_astra_yaml_exists(self, runner: CliRunner, tmp_path: Path):
        """Test that init refuses to run in an existing ASTRA project."""
        project_dir = tmp_path / "already-init"
        runner.invoke(main, [
            "init", str(project_dir), "--no-git", "--no-venv",
            "--permissions", "recommended",
        ])
        assert (project_dir / "astra.yaml").exists()

        result = runner.invoke(main, [
            "init", str(project_dir), "--no-git", "--no-venv",
            "--permissions", "recommended",
        ])
        assert result.exit_code == 1
        assert "already an ASTRA project" in result.output

    def test_init_existing_nonempty_dir_decline(self, runner: CliRunner, tmp_path: Path):
        """Test declining to overwrite existing non-empty directory."""
        project_dir = tmp_path / "existing"
        project_dir.mkdir()
        (project_dir / "some_file.txt").write_text("existing content")

        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv", "--permissions", "recommended"],
            input="n\n",
        )
        assert result.exit_code == 0
        assert not (project_dir / "astra.yaml").exists()

    def test_init_existing_nonempty_dir_confirm(self, runner: CliRunner, tmp_path: Path):
        """Test confirming to overwrite existing non-empty directory."""
        project_dir = tmp_path / "existing-confirm"
        project_dir.mkdir()
        (project_dir / "some_file.txt").write_text("existing content")

        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv", "--permissions", "recommended"],
            input="y\n",
        )
        assert result.exit_code == 0
        assert (project_dir / "astra.yaml").exists()

    def test_init_creates_dagster_yaml(self, runner: CliRunner, tmp_path: Path):
        """Test that init creates dagster.yaml."""
        project_dir = tmp_path / "dagster-test"
        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv",
             "--permissions", "recommended"],
        )
        assert result.exit_code == 0
        assert (project_dir / "dagster.yaml").exists()

    def test_init_with_target_creates_prism_yaml(self, runner: CliRunner, tmp_path: Path):
        """Test that --target creates prism.yaml with a flat target key."""
        project_dir = tmp_path / "target-test"
        with patch("prism.dagster.targets.load_target", return_value={"site": "perlmutter"}):
            result = runner.invoke(
                main,
                ["init", str(project_dir), "--no-git", "--no-venv",
                 "--target", "perlmutter-gpu",
                 "--permissions", "recommended"],
            )
        assert result.exit_code == 0
        assert (project_dir / "prism.yaml").exists()

        import yaml
        config = yaml.safe_load((project_dir / "prism.yaml").read_text())
        assert config["target"] == "perlmutter-gpu"

    def test_init_without_target_uses_default(self, runner: CliRunner, tmp_path: Path):
        """Test that without --target, prism.yaml uses default target from user config."""
        project_dir = tmp_path / "no-target-test"
        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv",
             "--permissions", "recommended"],
        )
        assert result.exit_code == 0
        assert (project_dir / "prism.yaml").exists()

        import yaml
        prism_cfg = yaml.safe_load(
            (project_dir / "prism.yaml").read_text()
        )
        # conftest sets default_target: fake
        assert prism_cfg["target"] == "fake"

class TestVersionOption:
    """Tests for version option."""

    def test_version(self, runner: CliRunner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output


class TestHelpOption:
    """Tests for help option."""

    def test_help(self, runner: CliRunner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Prism" in result.output

    def test_init_help(self, runner: CliRunner):
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0

    def test_setup_help(self, runner: CliRunner):
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0


class TestSetupCommand:
    """Tests for the prism setup command."""

    def test_setup_help(self, runner: CliRunner):
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        assert "target" in result.output.lower() or "Setup" in result.output

    def test_setup_list_empty(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["setup", "--list"])
        assert result.exit_code == 0
        assert "no additional targets" in result.output.lower() or "local" in result.output

    def test_setup_list_with_targets(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "perlmutter-gpu.yaml").write_text("site: perlmutter\n")
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: perlmutter-gpu\n")
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)
        result = runner.invoke(main, ["setup", "--list"])
        assert result.exit_code == 0
        assert "perlmutter-gpu" in result.output

    def test_setup_show(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "perlmutter-gpu.yaml").write_text("site: perlmutter\nbackend: slurm\n")
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        result = runner.invoke(main, ["setup", "--show", "perlmutter-gpu"])
        assert result.exit_code == 0
        assert "slurm" in result.output

    def test_setup_show_nonexistent(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        result = runner.invoke(main, ["setup", "--show", "nonexistent"])
        assert result.exit_code == 1

    def test_setup_wizard_known_site(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test the wizard flow with a known site (perlmutter)."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")

        # hpc=yes, site=1(perlmutter), username, account,
        # node_type=1(gpu), qos=2(debug), target_name=default,
        # resource limits=defaults (4x Enter)
        input_lines = "y\n1\ntestuser\nm1234\n1\n2\n\n\n\n\n\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0
        assert "Default target: perlmutter-gpu" in result.output

        # Should create only the selected node type + local
        assert (targets_dir / "perlmutter-gpu.yaml").exists()
        assert not (targets_dir / "perlmutter-gpu_hbm80.yaml").exists()
        assert not (targets_dir / "perlmutter-cpu.yaml").exists()
        assert (targets_dir / "local.yaml").exists()
        assert (tmp_path / "config.yaml").exists()

        # Verify target shape — flat, no defaults nesting
        import yaml
        target = yaml.safe_load((targets_dir / "perlmutter-gpu.yaml").read_text())
        assert target["constraint"] == "gpu"
        assert target["qos"] == "debug"
        assert target["backend"] == "slurm"
        assert target["max_nodes"] == 4

        # Verify closing message
        assert "prism target --list" in result.output
        assert "prism target add" in result.output
        assert "prism target edit" in result.output

    def test_setup_wizard_local(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test the wizard flow choosing local (no HPC)."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")

        # hpc=no
        input_lines = "n\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0
        assert "local" in result.output

        import yaml
        target = yaml.safe_load(
            (targets_dir / "local.yaml").read_text()
        )
        assert target["backend"] == "local"
        config = yaml.safe_load(
            (tmp_path / "config.yaml").read_text()
        )
        assert config["default_target"] == "local"

    def test_setup_wizard_sets_default(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test that wizard sets the default_target in config.yaml."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")

        # hpc=yes, site=1(perlmutter), username, account,
        # node_type=1(gpu), qos=2(debug), target_name=default,
        # resource limits=defaults (4x Enter)
        input_lines = "y\n1\ntestuser\nm1234\n1\n2\n\n\n\n\n\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0

        import yaml
        config = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert config["default_target"] == "perlmutter-gpu"

    def test_setup_default_local(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test --default local works without a target config file."""
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["setup", "--default", "local"])
        assert result.exit_code == 0

        import yaml
        config = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert config["default_target"] == "local"

    def test_setup_default_existing_target(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """Test --default works with a configured target."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "perlmutter-gpu.yaml").write_text("site: perlmutter\n")
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["setup", "--default", "perlmutter-gpu"])
        assert result.exit_code == 0

        import yaml
        config = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert config["default_target"] == "perlmutter-gpu"

    def test_setup_default_nonexistent(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """Test --default fails for a non-existent target."""
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        result = runner.invoke(main, ["setup", "--default", "nonexistent"])
        assert result.exit_code == 1

    def test_setup_menu_shown_when_config_exists(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """When config exists, setup shows the menu. Choosing 5 re-runs wizard."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: local\n")
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)

        # action 6 (re-run wizard), then wizard: no HPC
        input_lines = "6\nn\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0
        assert "Change permission level" in result.output
        assert "Default target: local" in result.output

    def test_setup_menu_change_permissions(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """Menu action 1 changes the permission tier."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: local\n")
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)

        # action 1, tier 2 (recommended)
        input_lines = "1\n2\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0

        import yaml
        config = yaml.safe_load(config_path.read_text())
        assert config["default_permission_tier"] == "recommended"

    def test_setup_menu_change_default(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """Menu action 5 changes the default target."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        (targets_dir / "perlmutter-gpu.yaml").write_text("site: perlmutter\n")
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: local\n")
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)

        # action 5, pick target 2 (perlmutter-gpu — local is 1)
        input_lines = "5\n2\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0

        import yaml
        config = yaml.safe_load(config_path.read_text())
        assert config["default_target"] == "perlmutter-gpu"

    def test_setup_menu_exit(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """Menu action 7 (exit) returns immediately. Also the default."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: local\n")
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)

        # Just press Enter — default is 7 (exit)
        input_lines = "\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0
        assert "Prism Setup" in result.output
        # Should NOT have entered the wizard
        assert "Configure a remote" not in result.output


class TestAutoTrigger:
    """Tests for the auto-trigger setup check."""

    def test_init_without_setup_triggers_wizard(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """Commands should trigger setup when no config exists."""
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["init", str(tmp_path / "proj"), "--no-git", "--no-venv"])
        assert "Prism Setup" in result.output or "No execution environment" in result.output

    def test_setup_command_skips_auto_trigger(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """prism setup itself should not trigger the auto-trigger."""
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        result = runner.invoke(main, ["setup", "--list"])
        assert "no additional targets" in result.output.lower() or "local" in result.output

    def test_target_command_skips_auto_trigger(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """prism target itself should not trigger the auto-trigger."""
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        result = runner.invoke(main, ["target", "--list"])
        assert "no additional targets" in result.output.lower() or "local" in result.output

    def test_version_skips_auto_trigger(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """--version should not trigger setup."""
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output

    def test_help_skips_auto_trigger(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """--help should not trigger setup."""
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0

    def test_commands_work_after_setup(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Commands should work normally when config exists."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: perlmutter-gpu\n")
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)
        result = runner.invoke(
            main,
            ["init", str(tmp_path / "proj"), "--no-git", "--no-venv",
             "--permissions", "recommended"],
        )
        assert result.exit_code == 0
        assert "Created ASTRA analysis project" in result.output


class TestTargetCommand:
    """Tests for the prism target command."""

    def test_target_no_prism_yaml(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["target"])
        assert result.exit_code == 0
        assert "No prism.yaml" in result.output

    def test_target_shows_current(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import yaml
        (tmp_path / "prism.yaml").write_text(yaml.dump({"target": "perlmutter-gpu"}))
        with patch("prism.dagster.targets.load_target", return_value={
            "backend": "slurm", "connection": {"hostname": "perlmutter.nersc.gov"},
        }):
            result = runner.invoke(main, ["target"])
        assert result.exit_code == 0
        assert "perlmutter-gpu" in result.output

    def test_target_set(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import yaml
        (tmp_path / "prism.yaml").write_text(yaml.dump({"target": "local"}))
        with patch("prism.dagster.targets.load_target", return_value={"backend": "slurm"}):
            result = runner.invoke(main, ["target", "--set", "perlmutter-gpu"])
        assert result.exit_code == 0
        assert "perlmutter-gpu" in result.output
        config = yaml.safe_load((tmp_path / "prism.yaml").read_text())
        assert config["target"] == "perlmutter-gpu"

    def test_target_set_nonexistent(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import yaml
        (tmp_path / "prism.yaml").write_text(yaml.dump({"target": "local"}))
        with patch("prism.dagster.targets.load_target", return_value=None):
            with patch("prism.dagster.targets.list_targets", return_value=["local"]):
                result = runner.invoke(main, ["target", "--set", "nonexistent"])
        assert result.exit_code == 1

    def test_target_list(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "perlmutter-gpu.yaml").write_text("site: perlmutter\n")
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: perlmutter-gpu\n")
        monkeypatch.setattr("prism.dagster.targets.get_config_path",
                            lambda: config_path)
        result = runner.invoke(main, ["target", "--list"])
        assert result.exit_code == 0
        assert "perlmutter-gpu" in result.output

    def test_target_show(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "perlmutter-gpu.yaml").write_text(
            "site: perlmutter\nbackend: slurm\n"
        )
        monkeypatch.setattr("prism.dagster.targets.get_targets_dir",
                            lambda: targets_dir)
        result = runner.invoke(main, ["target", "--show", "perlmutter-gpu"])
        assert result.exit_code == 0
        assert "slurm" in result.output

    def test_target_help(self, runner: CliRunner):
        result = runner.invoke(main, ["target", "--help"])
        assert result.exit_code == 0


class TestRemoteCommandRemoved:
    """Verify that the old remote commands are gone."""

    def test_remote_not_a_command(self, runner: CliRunner):
        result = runner.invoke(main, ["remote", "--help"])
        assert result.exit_code != 0 or "No such command" in result.output \
            or "Error" in result.output


class TestTargetResolution:
    """Integration tests for target resolution flow."""

    def test_run_with_local_target(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test that prism run with a local target resolves correctly."""
        monkeypatch.chdir(tmp_path)
        import yaml

        # Create minimal astra.yaml with no outputs (just metadata)
        (tmp_path / "astra.yaml").write_text(yaml.dump({
            "name": "test-project",
            "version": "0.1.0",
            "description": "Test",
            "decisions": [],
        }, sort_keys=False))

        # Create prism.yaml with a local target
        (tmp_path / "prism.yaml").write_text(yaml.dump({
            "target": "local",
        }, sort_keys=False))

        # Create dagster.yaml
        (tmp_path / "results").mkdir()
        (tmp_path / "dagster.yaml").write_text(yaml.dump({
            "storage": {"sqlite": {"base_dir": str(tmp_path / "results" / ".dagster")}}
        }, sort_keys=False))

        # Run — should not error on target resolution
        result = runner.invoke(main, ["run"])
        assert "Unknown target" not in (result.output or "")
        assert "ImportError" not in (result.output or "")

    def test_run_with_named_target(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test that prism run --target loads target config directly."""
        monkeypatch.chdir(tmp_path)
        import yaml

        (tmp_path / "astra.yaml").write_text(yaml.dump({
            "name": "test-project",
            "version": "0.1.0",
            "description": "Test",
            "decisions": [],
        }, sort_keys=False))

        (tmp_path / "prism.yaml").write_text(yaml.dump({
            "target": "local",
        }, sort_keys=False))

        (tmp_path / "results").mkdir()
        (tmp_path / "dagster.yaml").write_text(yaml.dump({
            "storage": {"sqlite": {"base_dir": str(tmp_path / "results" / ".dagster")}}
        }, sort_keys=False))

        # Mock load_target for perlmutter-gpu
        target_config = {
            "site": "perlmutter",
            "backend": "slurm",
            "connection": {"hostname": "perlmutter.nersc.gov", "username": "testuser"},
            "account": "m1234",
            "container_runtime": "podman-hpc",
            "constraint": "gpu",
            "qos": "debug",
        }
        with patch("prism.dagster.targets.load_target", return_value=target_config):
            result = runner.invoke(main, ["run", "--target", "perlmutter-gpu"])
        # Should not fail on target resolution
        assert "Unknown target" not in (result.output or "")


class TestUpdateCommand:
    """Tests for the prism update command."""

    def _patch_updater(self, monkeypatch, pull_results, pkg_results=None):
        """Patch updater functions for update command tests."""
        monkeypatch.setattr("prism.updater._get_lightcone_root", lambda: Path("/fake"))
        monkeypatch.setattr("prism.updater.pull_repos", lambda root: pull_results)
        monkeypatch.setattr("prism.updater.reinstall_packages",
                            lambda root: pkg_results or [])
        monkeypatch.setattr("prism.updater._CHECK_FILE", Path("/tmp/.fake_update_check"))

    def test_update_no_reinstall_when_up_to_date(self, runner: CliRunner, monkeypatch):
        """When all repos are up to date, skip reinstall and project sync."""
        self._patch_updater(monkeypatch, [
            ("ASTRA", True, "already up to date"),
            ("Prism", True, "already up to date"),
            ("Prism-UI", True, "already up to date"),
        ])

        result = runner.invoke(main, ["update"])
        assert result.exit_code == 0
        assert "Reinstalling" not in result.output
        assert "Sync updated" not in result.output

    def test_update_reinstalls_when_updated(self, runner: CliRunner, monkeypatch):
        """When repos have updates, reinstall packages and prompt for sync."""
        self._patch_updater(monkeypatch, [
            ("ASTRA", True, "updated (3 new commits)"),
            ("Prism", True, "already up to date"),
        ], [
            ("ASTRA", True, "installed"),
            ("Prism", True, "installed"),
        ])

        result = runner.invoke(main, ["update"], input="skip\n")
        assert result.exit_code == 0
        assert "Reinstalling" in result.output
        assert "Sync updated" in result.output

    def test_update_check_only(self, runner: CliRunner, monkeypatch):
        """--check flag only checks, doesn't pull or reinstall."""
        monkeypatch.setattr("prism.updater._get_lightcone_root", lambda: Path("/fake"))
        monkeypatch.setattr("prism.updater.check_for_updates",
                            lambda quiet_if_current=True: "Updates available for: Prism")

        result = runner.invoke(main, ["update", "--check"])
        assert result.exit_code == 0
        assert "Updates available" in result.output
        assert "Reinstalling" not in result.output


class TestSyncProjectPlugins:
    """Tests for _sync_project_plugins."""

    def _make_project(self, tmp_path: Path) -> Path:
        """Create a minimal ASTRA project for sync testing."""
        project = tmp_path / "my-project"
        project.mkdir()
        (project / "astra.yaml").write_text("name: my-project\n")
        claude_dir = project / ".claude"
        claude_dir.mkdir()
        # Write a CLAUDE.md with user content below the separator
        (project / "CLAUDE.md").write_text(
            "# CLAUDE.md\n\n## Project: my-project\n\nOld managed content.\n\n"
            "---\n\n"
            "## Analysis Context\n\n"
            "My custom analysis notes that should be preserved.\n"
        )
        return project

    def _make_plugin_source(self, tmp_path: Path) -> Path:
        """Create a fake plugin source directory."""
        plugin = tmp_path / "plugin_source"
        plugin.mkdir()
        # Skills
        skills = plugin / "skills" / "prism-build"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("# build skill v2\n")
        # Scripts
        scripts = plugin / "scripts"
        scripts.mkdir()
        (scripts / "session-start.sh").write_text("#!/bin/bash\necho hi\n")
        # Hooks
        hooks = plugin / "hooks"
        hooks.mkdir()
        (hooks / "langfuse_hook.py").write_text("# hook v2\n")
        # Template
        templates = plugin / "templates"
        templates.mkdir()
        (templates / "CLAUDE.md").write_text(
            "# CLAUDE.md\n\n## Project: {{name}}\n\nNew managed content from template.\n\n"
            "---\n\n"
            "<!-- AUTOGENERATED -->\n"
            "## Analysis Context\n\n"
            "_Default context._\n"
        )
        return plugin

    def test_sync_copies_plugin_dirs(self, tmp_path: Path):
        """Sync should copy skills, hooks, scripts into .claude/."""
        from prism.cli import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        with patch("prism.cli._get_plugin_source_dir", return_value=plugin):
            result = _sync_project_plugins(project)

        assert result is True
        assert (project / ".claude" / "skills" / "prism-build" / "SKILL.md").exists()
        assert (project / ".claude" / "scripts" / "session-start.sh").exists()
        assert (project / ".claude" / "hooks" / "langfuse_hook.py").exists()

    def test_sync_scripts_executable(self, tmp_path: Path):
        """Synced scripts should be executable."""
        from prism.cli import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        with patch("prism.cli._get_plugin_source_dir", return_value=plugin):
            _sync_project_plugins(project)

        sh = project / ".claude" / "scripts" / "session-start.sh"
        assert sh.stat().st_mode & 0o111

    def test_sync_preserves_analysis_context(self, tmp_path: Path):
        """Sync should update managed CLAUDE.md section but preserve Analysis Context."""
        from prism.cli import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        with patch("prism.cli._get_plugin_source_dir", return_value=plugin):
            _sync_project_plugins(project)

        content = (project / "CLAUDE.md").read_text()
        # New managed content from template
        assert "New managed content from template" in content
        # Old managed content replaced
        assert "Old managed content" not in content
        # User content preserved
        assert "My custom analysis notes that should be preserved" in content

    def test_sync_substitutes_project_name(self, tmp_path: Path):
        """CLAUDE.md template should have {{name}} replaced with project dir name."""
        from prism.cli import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        with patch("prism.cli._get_plugin_source_dir", return_value=plugin):
            _sync_project_plugins(project)

        content = (project / "CLAUDE.md").read_text()
        assert "my-project" in content
        assert "{{name}}" not in content

    def test_sync_rejects_non_astra_project(self, tmp_path: Path):
        """Sync should fail for directories without astra.yaml."""
        from prism.cli import _sync_project_plugins

        not_a_project = tmp_path / "random-dir"
        not_a_project.mkdir()

        result = _sync_project_plugins(not_a_project)
        assert result is False

    def test_sync_replaces_stale_skills(self, tmp_path: Path):
        """Sync should replace existing skills with fresh ones."""
        from prism.cli import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        # Put stale skill in project
        old_skill = project / ".claude" / "skills" / "prism-build"
        old_skill.mkdir(parents=True)
        (old_skill / "SKILL.md").write_text("# old skill v1\n")

        with patch("prism.cli._get_plugin_source_dir", return_value=plugin):
            _sync_project_plugins(project)

        content = (project / ".claude" / "skills" / "prism-build" / "SKILL.md").read_text()
        assert "v2" in content
        assert "v1" not in content
