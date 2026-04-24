"""Tests for lc run CLI command."""
import contextlib

import pytest
from click.testing import CliRunner

from lightcone.cli.commands import main


@pytest.fixture(autouse=True)
def _no_parsl_load(monkeypatch):
    """Prevent CLI tests from booting a real DFK."""
    @contextlib.contextmanager
    def fake_load(*a, **k):
        yield
    monkeypatch.setattr("parsl.load", fake_load)


@pytest.fixture
def runner():
    return CliRunner()


class TestRunCommand:
    def test_run_help(self, runner):
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "Materialize" in result.output
