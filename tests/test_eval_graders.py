"""Tests for eval graders."""

from __future__ import annotations

from unittest.mock import MagicMock

from prism.eval.graders import (
    _grade_command,
    _grade_status,
    compute_composite_score,
    run_graders,
)
from prism.eval.models import GraderResult, GraderSpec, GraderType
from prism.eval.sandbox import ExecuteResult


def _mock_sandbox(exec_results: dict[str, ExecuteResult] | None = None) -> MagicMock:
    """Create a mock sandbox with configurable exec results."""
    sandbox = MagicMock()
    sandbox.WORK_DIR = "/home/user/project"

    if exec_results:
        def exec_side_effect(cmd: str, timeout: int = 300) -> ExecuteResult:
            for pattern, result in exec_results.items():
                if pattern in cmd:
                    return result
            return ExecuteResult(exit_code=1, output="no match")

        sandbox.exec.side_effect = exec_side_effect
    return sandbox


class TestGradeCommand:
    def test_passing_command(self):
        sandbox = _mock_sandbox({"echo": ExecuteResult(exit_code=0, output="ok\n")})
        grader = GraderSpec(name="check", type=GraderType.command, command="echo ok")
        result = _grade_command(sandbox, grader)
        assert result.passed is True
        assert result.score == 1.0

    def test_failing_command(self):
        sandbox = _mock_sandbox({"false": ExecuteResult(exit_code=1, output="")})
        grader = GraderSpec(name="check", type=GraderType.command, command="false")
        result = _grade_command(sandbox, grader)
        assert result.passed is False
        assert result.score == 0.0


class TestGradeStatus:
    def test_all_materialized(self):
        status_output = (
            "┃ Output ┃ baseline ┃\n"
            "│ chain  │ ok       │\n"
            "│ plot   │ ok       │\n"
        )
        sandbox = _mock_sandbox({"prism status": ExecuteResult(exit_code=0, output=status_output)})
        grader = GraderSpec(name="status", type=GraderType.status, weight=3.0)
        result = _grade_status(sandbox, grader)
        assert result.passed is True
        assert result.score == 1.0
        assert result.weight == 3.0

    def test_partial_materialization(self):
        status_output = (
            "┃ Output ┃ baseline ┃\n"
            "│ chain  │ ok       │\n"
            "│ plot   │ pending  │\n"
        )
        sandbox = _mock_sandbox({"prism status": ExecuteResult(exit_code=0, output=status_output)})
        grader = GraderSpec(name="status", type=GraderType.status)
        result = _grade_status(sandbox, grader)
        assert result.passed is False
        assert result.score == 0.5

    def test_status_command_failure(self):
        sandbox = _mock_sandbox({"prism status": ExecuteResult(exit_code=1, output="error")})
        grader = GraderSpec(name="status", type=GraderType.status)
        result = _grade_status(sandbox, grader)
        assert result.error is not None


class TestCompositeScore:
    def test_uniform_weights(self):
        results = [
            GraderResult(name="a", type=GraderType.command, score=1.0, weight=1.0),
            GraderResult(name="b", type=GraderType.command, score=0.0, weight=1.0),
        ]
        assert compute_composite_score(results) == 0.5

    def test_weighted(self):
        results = [
            GraderResult(name="a", type=GraderType.command, score=1.0, weight=3.0),
            GraderResult(name="b", type=GraderType.command, score=0.0, weight=1.0),
        ]
        assert compute_composite_score(results) == 0.75

    def test_empty(self):
        assert compute_composite_score([]) == 0.0


class TestRunGraders:
    def test_catches_exceptions(self):
        sandbox = MagicMock()
        sandbox.WORK_DIR = "/home/user/project"
        sandbox.exec.side_effect = RuntimeError("sandbox down")

        graders = [GraderSpec(name="check", type=GraderType.command, command="echo")]
        results = run_graders(sandbox, graders, evals_dir=MagicMock(), task_id="t1")
        assert len(results) == 1
        assert results[0].error is not None
        assert results[0].score == 0.0
