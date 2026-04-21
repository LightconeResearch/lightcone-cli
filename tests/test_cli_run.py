"""Tests for lc run CLI command."""
import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main


@pytest.fixture
def runner():
    return CliRunner()


class TestRunCommand:
    def test_run_help(self, runner):
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "Materialize" in result.output
