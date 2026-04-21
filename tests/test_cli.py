"""Tests for lightcone-cli CLI commands."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main


@pytest.fixture
def runner():
    """Return a CLI runner."""
    return CliRunner()


class TestInitCommand:
    """Tests for the lc init command."""

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
        assert (project_dir / ".lightcone").is_dir()

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
        """Test that init creates .lightcone/dagster.yaml."""
        project_dir = tmp_path / "dagster-test"
        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv",
             "--permissions", "recommended"],
        )
        assert result.exit_code == 0
        assert (project_dir / ".lightcone" / "dagster.yaml").exists()

    def test_init_with_target_creates_lightcone_yaml(self, runner: CliRunner, tmp_path: Path):
        """Test that --target creates .lightcone/lightcone.yaml with a flat target key."""
        project_dir = tmp_path / "target-test"
        with patch("lightcone.engine.targets.load_target", return_value={"site": "perlmutter"}):
            result = runner.invoke(
                main,
                ["init", str(project_dir), "--no-git", "--no-venv",
                 "--target", "perlmutter-gpu",
                 "--permissions", "recommended"],
            )
        assert result.exit_code == 0
        assert (project_dir / ".lightcone" / "lightcone.yaml").exists()

        import yaml
        config = yaml.safe_load((project_dir / ".lightcone" / "lightcone.yaml").read_text())
        assert config["target"] == "perlmutter-gpu"

    def test_init_without_target_uses_default(self, runner: CliRunner, tmp_path: Path):
        """Test that without --target, .lightcone/lightcone.yaml uses the default target from user config."""  # noqa: E501
        project_dir = tmp_path / "no-target-test"
        result = runner.invoke(
            main,
            ["init", str(project_dir), "--no-git", "--no-venv",
             "--permissions", "recommended"],
        )
        assert result.exit_code == 0
        assert (project_dir / ".lightcone" / "lightcone.yaml").exists()

        import yaml
        lightcone_cfg = yaml.safe_load(
            (project_dir / ".lightcone" / "lightcone.yaml").read_text()
        )
        # conftest sets default_target: fake
        assert lightcone_cfg["target"] == "fake"

class TestInitExistingProject:
    """Tests for lc init --existing-project."""

    def test_existing_project_in_place(self, runner: CliRunner, tmp_path: Path):
        """Test --existing-project . adds infrastructure in place."""
        project_dir = tmp_path / "my-existing-code"
        project_dir.mkdir()
        (project_dir / "train.py").write_text("print('hello')\n")
        (project_dir / "requirements.txt").write_text("torch\n")

        result = runner.invoke(
            main,
            ["init", str(project_dir), "--existing-project", str(project_dir),
             "--no-git", "--no-venv", "--permissions", "yolo"],
        )
        assert result.exit_code == 0

        # Infrastructure created
        assert (project_dir / ".lightcone" / "lightcone.yaml").exists()
        assert (project_dir / ".lightcone" / "dagster.yaml").exists()
        assert (project_dir / ".claude" / "settings.json").exists()
        assert (project_dir / "CLAUDE.md").exists()
        assert (project_dir / "universes").is_dir()
        assert (project_dir / "results").is_dir()
        assert (project_dir / "Containerfile").exists()

        # astra.yaml NOT created — that's /lc-migrate's job
        assert not (project_dir / "astra.yaml").exists()

        # Existing files untouched
        assert (project_dir / "train.py").read_text() == "print('hello')\n"
        assert (project_dir / "requirements.txt").read_text() == "torch\n"

    def test_existing_project_copy_from_source(self, runner: CliRunner, tmp_path: Path):
        """Test --existing-project copies code from source to target."""
        source_dir = tmp_path / "old-code"
        source_dir.mkdir()
        (source_dir / "analysis.py").write_text("x = 1\n")
        (source_dir / "data").mkdir()
        (source_dir / "data" / "input.csv").write_text("a,b\n1,2\n")

        target_dir = tmp_path / "new-astra-project"

        result = runner.invoke(
            main,
            ["init", str(target_dir), "--existing-project", str(source_dir),
             "--no-git", "--no-venv", "--permissions", "yolo"],
        )
        assert result.exit_code == 0

        # Code was copied
        assert (target_dir / "analysis.py").read_text() == "x = 1\n"
        assert (target_dir / "data" / "input.csv").exists()

        # Infrastructure added
        assert (target_dir / ".lightcone" / "lightcone.yaml").exists()
        assert (target_dir / "CLAUDE.md").exists()

        # Source untouched
        assert not (source_dir / ".lightcone").exists()

    def test_existing_project_preserves_gitignore(self, runner: CliRunner, tmp_path: Path):
        """Test that --existing-project appends to existing .gitignore."""
        project_dir = tmp_path / "has-gitignore"
        project_dir.mkdir()
        (project_dir / ".gitignore").write_text("*.log\nnode_modules/\n")

        result = runner.invoke(
            main,
            ["init", str(project_dir), "--existing-project", str(project_dir),
             "--no-git", "--no-venv", "--permissions", "yolo"],
        )
        assert result.exit_code == 0

        gitignore = (project_dir / ".gitignore").read_text()
        assert "*.log" in gitignore
        assert "node_modules/" in gitignore
        assert "results/" in gitignore

    def test_existing_project_skips_existing_claude_md(self, runner: CliRunner, tmp_path: Path):
        """Test that --existing-project doesn't overwrite existing CLAUDE.md."""
        project_dir = tmp_path / "has-claude-md"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text("# My custom docs\n")

        result = runner.invoke(
            main,
            ["init", str(project_dir), "--existing-project", str(project_dir),
             "--no-git", "--no-venv", "--permissions", "yolo"],
        )
        assert result.exit_code == 0
        assert (project_dir / "CLAUDE.md").read_text() == "# My custom docs\n"

    def test_existing_project_fails_if_astra_yaml_exists(
        self, runner: CliRunner, tmp_path: Path,
    ):
        """Test that --existing-project errors if astra.yaml already exists."""
        project_dir = tmp_path / "already-astra"
        project_dir.mkdir()
        (project_dir / "astra.yaml").write_text("version: '1.0'\n")

        result = runner.invoke(
            main,
            ["init", str(project_dir), "--existing-project", str(project_dir),
             "--no-git", "--no-venv", "--permissions", "yolo"],
        )
        assert result.exit_code == 1

    def test_existing_project_shows_next_steps(self, runner: CliRunner, tmp_path: Path):
        """Test that output includes next steps with /lc-migrate."""
        project_dir = tmp_path / "next-steps"
        project_dir.mkdir()

        result = runner.invoke(
            main,
            ["init", str(project_dir), "--existing-project", str(project_dir),
             "--no-git", "--no-venv", "--permissions", "yolo"],
        )
        assert result.exit_code == 0
        assert "/lc-migrate" in result.output


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
        assert "lightcone-cli" in result.output

    def test_init_help(self, runner: CliRunner):
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0

    def test_setup_help(self, runner: CliRunner):
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0


class TestSetupCommand:
    """Tests for the lc setup command."""

    def test_setup_help(self, runner: CliRunner):
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        assert "target" in result.output.lower() or "Setup" in result.output

    def test_setup_list_empty(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["setup", "--list"])
        assert result.exit_code == 0
        assert "no additional targets" in result.output.lower() or "local" in result.output

    def test_setup_list_with_targets(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "perlmutter-gpu.yaml").write_text("site: perlmutter\n")
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: perlmutter-gpu\n")
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: config_path)
        result = runner.invoke(main, ["setup", "--list"])
        assert result.exit_code == 0
        assert "perlmutter-gpu" in result.output

    def test_setup_show(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "perlmutter-gpu.yaml").write_text("site: perlmutter\nbackend: slurm\n")
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        result = runner.invoke(main, ["setup", "--show", "perlmutter-gpu"])
        assert result.exit_code == 0
        assert "slurm" in result.output

    def test_setup_show_nonexistent(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        result = runner.invoke(main, ["setup", "--show", "nonexistent"])
        assert result.exit_code == 1

    def test_setup_wizard_known_site(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test the wizard flow with a known site (perlmutter)."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")

        # hpc=yes, site=1(perlmutter), username, account,
        # target_name=default (accept perlmutter-m1234)
        input_lines = "y\n1\ntestuser\nm1234\n\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0
        assert "Default target: perlmutter-m1234" in result.output

        # Should create the target + local
        assert (targets_dir / "perlmutter-m1234.yaml").exists()
        assert (targets_dir / "local.yaml").exists()
        assert (tmp_path / "config.yaml").exists()

        # Verify target shape — site, backend, connection, account
        import yaml
        target = yaml.safe_load((targets_dir / "perlmutter-m1234.yaml").read_text())
        assert target["site"] == "perlmutter"
        assert target["backend"] == "slurm"
        assert target["account"] == "m1234"
        assert target["container_runtime"] == "podman-hpc"

        # Verify closing message
        assert "lc target --list" in result.output
        assert "lc target add" in result.output
        assert "lc target edit" in result.output

    def test_setup_wizard_local(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test the wizard flow choosing local (no HPC)."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
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
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")

        # hpc=yes, site=1(perlmutter), username, account,
        # target_name=default (accept perlmutter-m1234)
        input_lines = "y\n1\ntestuser\nm1234\n\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0

        import yaml
        config = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert config["default_target"] == "perlmutter-m1234"

    def test_setup_default_local(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test --default local works without a target config file."""
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
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
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
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
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        result = runner.invoke(main, ["setup", "--default", "nonexistent"])
        assert result.exit_code == 1

    def test_setup_menu_shown_when_config_exists(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """When config exists, setup shows the menu. Choosing 5 re-runs wizard."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True)
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: local\n")
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
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
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: local\n")
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
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
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: local\n")
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
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
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: local\n")
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: config_path)

        # Just press Enter — default is 7 (exit)
        input_lines = "\n"
        result = runner.invoke(main, ["setup"], input=input_lines)
        assert result.exit_code == 0
        assert "lightcone-cli Setup" in result.output
        # Should NOT have entered the wizard
        assert "Configure a remote" not in result.output


class TestAutoTrigger:
    """Tests for the auto-trigger setup check."""

    def test_init_without_setup_triggers_wizard(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """Commands should trigger setup when no config exists."""
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["init", str(tmp_path / "proj"), "--no-git", "--no-venv"])
        assert "lightcone-cli Setup" in result.output or "No execution environment" in result.output

    def test_setup_command_skips_auto_trigger(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """lc setup itself should not trigger the auto-trigger."""
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        result = runner.invoke(main, ["setup", "--list"])
        assert "no additional targets" in result.output.lower() or "local" in result.output

    def test_target_command_skips_auto_trigger(
        self, runner: CliRunner, tmp_path: Path, monkeypatch,
    ):
        """lc target itself should not trigger the auto-trigger."""
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: tmp_path / "targets")
        result = runner.invoke(main, ["target", "--list"])
        assert "no additional targets" in result.output.lower() or "local" in result.output

    def test_version_skips_auto_trigger(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """--version should not trigger setup."""
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output

    def test_help_skips_auto_trigger(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """--help should not trigger setup."""
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: tmp_path / "config.yaml")
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0

    def test_commands_work_after_setup(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Commands should work normally when config exists."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: perlmutter-gpu\n")
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
                            lambda: config_path)
        result = runner.invoke(
            main,
            ["init", str(tmp_path / "proj"), "--no-git", "--no-venv",
             "--permissions", "recommended"],
        )
        assert result.exit_code == 0
        assert "Created ASTRA analysis project" in result.output


class TestTargetCommand:
    """Tests for the lc target command."""

    def test_target_no_lightcone_yaml(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(main, ["target"])
        assert result.exit_code == 0
        assert "No lightcone.yaml" in result.output

    def test_target_shows_current(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import yaml
        (tmp_path / ".lightcone").mkdir()
        (tmp_path / ".lightcone" / "lightcone.yaml").write_text(
            yaml.dump({"target": "perlmutter-gpu"})
        )
        with patch("lightcone.engine.targets.load_target", return_value={
            "backend": "slurm", "connection": {"hostname": "perlmutter.nersc.gov"},
        }):
            result = runner.invoke(main, ["target"])
        assert result.exit_code == 0
        assert "perlmutter-gpu" in result.output

    def test_target_set(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import yaml
        (tmp_path / ".lightcone").mkdir()
        (tmp_path / ".lightcone" / "lightcone.yaml").write_text(yaml.dump({"target": "local"}))
        with patch("lightcone.engine.targets.load_target", return_value={"backend": "slurm"}):
            result = runner.invoke(main, ["target", "--set", "perlmutter-gpu"])
        assert result.exit_code == 0
        assert "perlmutter-gpu" in result.output
        config = yaml.safe_load((tmp_path / ".lightcone" / "lightcone.yaml").read_text())
        assert config["target"] == "perlmutter-gpu"

    def test_target_set_nonexistent(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import yaml
        (tmp_path / ".lightcone").mkdir()
        (tmp_path / ".lightcone" / "lightcone.yaml").write_text(yaml.dump({"target": "local"}))
        with patch("lightcone.engine.targets.load_target", return_value=None):
            with patch("lightcone.engine.targets.list_targets", return_value=["local"]):
                result = runner.invoke(main, ["target", "--set", "nonexistent"])
        assert result.exit_code == 1

    def test_target_list(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "perlmutter-gpu.yaml").write_text("site: perlmutter\n")
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
                            lambda: targets_dir)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("default_target: perlmutter-gpu\n")
        monkeypatch.setattr("lightcone.engine.targets.get_config_path",
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
        monkeypatch.setattr("lightcone.engine.targets.get_targets_dir",
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
        """Test that lc run with a local target resolves correctly."""
        monkeypatch.chdir(tmp_path)
        import yaml

        # Create minimal astra.yaml with no outputs (just metadata)
        (tmp_path / "astra.yaml").write_text(yaml.dump({
            "name": "test-project",
            "version": "0.1.0",
            "description": "Test",
            "decisions": [],
        }, sort_keys=False))

        # Create .lightcone/ with lightcone.yaml and dagster.yaml
        (tmp_path / ".lightcone").mkdir()
        (tmp_path / ".lightcone" / "lightcone.yaml").write_text(yaml.dump({
            "target": "local",
        }, sort_keys=False))

        (tmp_path / "results").mkdir()
        (tmp_path / ".lightcone" / "dagster.yaml").write_text(yaml.dump({
            "storage": {"sqlite": {"base_dir": str(tmp_path / "results" / ".dagster")}}
        }, sort_keys=False))

        # Run — should not error on target resolution
        result = runner.invoke(main, ["run"])
        assert "Unknown target" not in (result.output or "")
        assert "ImportError" not in (result.output or "")

    def test_run_with_named_target(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test that lc run --target loads target config directly."""
        monkeypatch.chdir(tmp_path)
        import yaml

        (tmp_path / "astra.yaml").write_text(yaml.dump({
            "name": "test-project",
            "version": "0.1.0",
            "description": "Test",
            "decisions": [],
        }, sort_keys=False))

        (tmp_path / ".lightcone").mkdir()
        (tmp_path / ".lightcone" / "lightcone.yaml").write_text(yaml.dump({
            "target": "local",
        }, sort_keys=False))

        (tmp_path / "results").mkdir()
        (tmp_path / ".lightcone" / "dagster.yaml").write_text(yaml.dump({
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
        with patch("lightcone.engine.targets.load_target", return_value=target_config):
            result = runner.invoke(main, ["run", "--target", "perlmutter-gpu"])
        # Should not fail on target resolution
        assert "Unknown target" not in (result.output or "")


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
        skills = plugin / "skills" / "lc-build"
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
        # Guides
        guides = plugin / "guides"
        guides.mkdir()
        (guides / "astra-reference.md").write_text("# ASTRA Reference\n")
        (guides / "ui-brand.md").write_text("# UI Brand\n")
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
        from lightcone.cli.commands import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        with patch("lightcone.cli.commands.get_plugin_source_dir", return_value=plugin):
            result = _sync_project_plugins(project)

        assert result is True
        assert (project / ".claude" / "skills" / "lc-build" / "SKILL.md").exists()
        assert (project / ".claude" / "scripts" / "session-start.sh").exists()
        assert (project / ".claude" / "hooks" / "langfuse_hook.py").exists()
        assert (project / ".claude" / "guides" / "astra-reference.md").exists()
        assert (project / ".claude" / "guides" / "ui-brand.md").exists()

    def test_sync_scripts_executable(self, tmp_path: Path):
        """Synced scripts should be executable."""
        from lightcone.cli.commands import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        with patch("lightcone.cli.commands.get_plugin_source_dir", return_value=plugin):
            _sync_project_plugins(project)

        sh = project / ".claude" / "scripts" / "session-start.sh"
        assert sh.stat().st_mode & 0o111

    def test_sync_preserves_analysis_context(self, tmp_path: Path):
        """Sync should update managed CLAUDE.md section but preserve Analysis Context."""
        from lightcone.cli.commands import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        with patch("lightcone.cli.commands.get_plugin_source_dir", return_value=plugin):
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
        from lightcone.cli.commands import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        with patch("lightcone.cli.commands.get_plugin_source_dir", return_value=plugin):
            _sync_project_plugins(project)

        content = (project / "CLAUDE.md").read_text()
        assert "my-project" in content
        assert "{{name}}" not in content

    def test_sync_rejects_non_astra_project(self, tmp_path: Path):
        """Sync should fail for directories without astra.yaml."""
        from lightcone.cli.commands import _sync_project_plugins

        not_a_project = tmp_path / "random-dir"
        not_a_project.mkdir()

        result = _sync_project_plugins(not_a_project)
        assert result is False

    def test_sync_replaces_stale_skills(self, tmp_path: Path):
        """Sync should replace existing skills with fresh ones."""
        from lightcone.cli.commands import _sync_project_plugins

        project = self._make_project(tmp_path)
        plugin = self._make_plugin_source(tmp_path)

        # Put stale skill in project
        old_skill = project / ".claude" / "skills" / "lc-build"
        old_skill.mkdir(parents=True)
        (old_skill / "SKILL.md").write_text("# old skill v1\n")

        with patch("lightcone.cli.commands.get_plugin_source_dir", return_value=plugin):
            _sync_project_plugins(project)

        content = (project / ".claude" / "skills" / "lc-build" / "SKILL.md").read_text()
        assert "v2" in content
        assert "v1" not in content


class TestInitSubAnalysis:
    """Tests for lc init --sub-analysis."""

    @staticmethod
    def _setup_project_root(project_dir: Path) -> None:
        """Create a minimal ASTRA project root for sub-analysis tests."""
        import yaml

        project_dir.mkdir(parents=True, exist_ok=True)
        spec = {
            "version": "1.0",
            "name": "Test Project",
            "description": "Test",
            "inputs": [],
            "outputs": [],
            "decisions": {},
        }
        (project_dir / "astra.yaml").write_text(
            yaml.safe_dump(spec, sort_keys=False)
        )
        universes_dir = project_dir / "universes"
        universes_dir.mkdir(exist_ok=True)
        universe = {
            "id": "baseline",
            "description": "Default",
            "decisions": {},
        }
        (universes_dir / "baseline.yaml").write_text(
            yaml.safe_dump(universe, sort_keys=False)
        )

    def test_sub_analysis_creates_structure(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test that --sub-analysis creates the expected directory structure."""
        project_dir = tmp_path / "proj"
        self._setup_project_root(project_dir)
        monkeypatch.chdir(project_dir)

        result = runner.invoke(main, ["init", "analyses/hod_fitting", "--sub-analysis"])
        assert result.exit_code == 0, result.output

        sub = project_dir / "analyses" / "hod_fitting"
        assert (sub / "astra.yaml").exists()
        assert (sub / "scripts" / ".gitkeep").exists()
        assert (sub / "universes" / "baseline.yaml").exists()

    def test_sub_analysis_astra_yaml_content(self, runner: CliRunner, tmp_path: Path, monkeypatch):
        """Test sub-analysis astra.yaml has the right fields."""
        import yaml

        project_dir = tmp_path / "proj"
        self._setup_project_root(project_dir)
        monkeypatch.chdir(project_dir)

        runner.invoke(main, ["init", "analyses/my_stage", "--sub-analysis"])

        sub_spec = yaml.safe_load(
            (project_dir / "analyses" / "my_stage" / "astra.yaml").read_text()
        )
        assert sub_spec["name"] == "My Stage"
        assert sub_spec["inputs"] == []
        assert sub_spec["outputs"] == []
        assert sub_spec["decisions"] == {}

    def test_sub_analysis_wires_root_astra_yaml(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        """Test that root astra.yaml gets the analyses reference."""
        import yaml

        project_dir = tmp_path / "proj"
        self._setup_project_root(project_dir)
        monkeypatch.chdir(project_dir)

        runner.invoke(main, ["init", "analyses/hod_fitting", "--sub-analysis"])

        root_spec = yaml.safe_load((project_dir / "astra.yaml").read_text())
        assert "analyses" in root_spec
        assert root_spec["analyses"]["hod_fitting"] == {"path": "./analyses/hod_fitting"}

    def test_sub_analysis_wires_root_universes(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        """Test that root universe files get the analyses reference."""
        import yaml

        project_dir = tmp_path / "proj"
        self._setup_project_root(project_dir)
        monkeypatch.chdir(project_dir)

        runner.invoke(main, ["init", "analyses/hod_fitting", "--sub-analysis"])

        udata = yaml.safe_load(
            (project_dir / "universes" / "baseline.yaml").read_text()
        )
        assert "analyses" in udata
        assert udata["analyses"]["hod_fitting"] == {"universe": "baseline"}

    def test_sub_analysis_bare_name_defaults_to_analyses_dir(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        """Test that a bare name (no path sep) goes under analyses/."""
        import yaml

        project_dir = tmp_path / "proj"
        self._setup_project_root(project_dir)
        monkeypatch.chdir(project_dir)

        result = runner.invoke(main, ["init", "new_stage", "--sub-analysis"])
        assert result.exit_code == 0, result.output

        sub = project_dir / "analyses" / "new_stage"
        assert (sub / "astra.yaml").exists()

        root_spec = yaml.safe_load((project_dir / "astra.yaml").read_text())
        assert root_spec["analyses"]["new_stage"] == {"path": "./analyses/new_stage"}

    def test_sub_analysis_refuses_without_root_astra_yaml(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        """Test error when no astra.yaml in cwd."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(main, ["init", "analyses/foo", "--sub-analysis"])
        assert result.exit_code == 1
        assert "No astra.yaml found" in result.output

    def test_sub_analysis_refuses_if_already_exists(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        """Test error when sub-analysis already exists."""
        project_dir = tmp_path / "proj"
        self._setup_project_root(project_dir)
        monkeypatch.chdir(project_dir)

        # Create it once
        runner.invoke(main, ["init", "analyses/dup", "--sub-analysis"])
        # Try again
        result = runner.invoke(main, ["init", "analyses/dup", "--sub-analysis"])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_sub_analysis_multiple_universes(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        """Test that all universe files get wired, not just baseline."""
        import yaml

        project_dir = tmp_path / "proj"
        self._setup_project_root(project_dir)
        # Add a second universe
        u2 = {"id": "alternate", "description": "Alt", "decisions": {}}
        (project_dir / "universes" / "alternate.yaml").write_text(
            yaml.safe_dump(u2, sort_keys=False)
        )
        monkeypatch.chdir(project_dir)

        runner.invoke(main, ["init", "analyses/stage_a", "--sub-analysis"])

        for ufile in ["baseline.yaml", "alternate.yaml"]:
            udata = yaml.safe_load(
                (project_dir / "universes" / ufile).read_text()
            )
            assert udata["analyses"]["stage_a"] == {"universe": "baseline"}
