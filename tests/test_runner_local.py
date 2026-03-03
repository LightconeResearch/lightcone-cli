"""Tests for local backend execution."""
from pathlib import Path

from prism.dagster.runner import ASTRAContainerRunner


class TestLocalBackend:
    def test_local_backend_runs_command(self, tmp_path: Path):
        (tmp_path / "results").mkdir()
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="local",
        )
        result = runner.execute(
            command="python -c \"print('hello')\"",
            output_id="test_output",
            universe_id="baseline",
        )
        assert result.exit_code == 0
        assert result.metadata.get("backend") == "local"

    def test_local_backend_ignores_container(self, tmp_path: Path):
        (tmp_path / "results").mkdir()
        runner = ASTRAContainerRunner(
            project_root=str(tmp_path),
            backend="local",
        )
        result = runner.execute(
            command="python -c \"print('works')\"",
            output_id="test_output",
            universe_id="baseline",
            container="some-image:latest",
        )
        assert result.exit_code == 0
        assert result.metadata.get("backend") == "local"
