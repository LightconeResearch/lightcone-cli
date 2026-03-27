"""Tests for eval harness with mock Daytona."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from prism.eval.harness import (
    DEFAULT_LOOP_PROMPT,
    _get_loop_prompt,
    load_run_config,
    load_task,
    run_trial,
)
from prism.eval.models import (
    EvalRunConfig,
    TaskSpec,
)
from prism.eval.sandbox import (
    BUILD_COMPLETE_MARKER,
    ClaudeResult,
    ExecuteResult,
    _parse_claude_output,
)


@pytest.fixture
def evals_dir(tmp_path: Path) -> Path:
    """Create a minimal evals directory."""
    evals = tmp_path / "evals"
    tasks = evals / "tasks" / "test-task"
    tasks.mkdir(parents=True)

    # Task
    (tasks / "task.yaml").write_text(yaml.dump({
        "id": "test-task",
        "description": "A test task",
        "universe": "baseline",
        "max_turns": 5,
        "trial_timeout": 30,
        "graders": [
            {"name": "check", "type": "command", "command": "true"},
        ],
    }))

    # Seed astra.yaml
    (tasks / "astra.yaml").write_text("version: '1.0'\nname: test\n")

    return evals


@pytest.fixture
def run_config_path(tmp_path: Path) -> Path:
    """Create a minimal run config."""
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.dump({
        "id": "test-run",
        "tasks": ["test-task"],
        "num_trials": 1,
        "max_concurrency": 1,
    }))
    return config_path


class TestLoadTask:
    def test_loads_valid_task(self, evals_dir: Path):
        task = load_task(evals_dir, "test-task")
        assert task.id == "test-task"
        assert task.max_turns == 5
        assert len(task.graders) == 1

    def test_missing_task(self, evals_dir: Path):
        with pytest.raises(FileNotFoundError):
            load_task(evals_dir, "nonexistent")


class TestLoadRunConfig:
    def test_loads_config(self, run_config_path: Path):
        config = load_run_config(run_config_path)
        assert config.id == "test-run"
        assert config.tasks == ["test-task"]
        assert config.num_trials == 1


class TestGetLoopPrompt:
    def test_default_prompt(self, evals_dir: Path):
        prompt = _get_loop_prompt(evals_dir, "test-task")
        assert prompt == DEFAULT_LOOP_PROMPT

    def test_custom_prompt(self, evals_dir: Path):
        custom = evals_dir / "tasks" / "test-task" / "loop-prompt.md"
        custom.write_text("Custom prompt for {{UNIVERSE}}")
        prompt = _get_loop_prompt(evals_dir, "test-task")
        assert prompt == "Custom prompt for {{UNIVERSE}}"


class TestRunTrial:
    @patch("prism.eval.harness.EvalSandbox")
    def test_successful_trial(self, mock_sandbox_cls: MagicMock, evals_dir: Path):
        """Test a trial that completes successfully."""
        sandbox_instance = mock_sandbox_cls.return_value
        sandbox_instance.WORK_DIR = "/home/user/project"

        sandbox_instance.exec_claude.return_value = ClaudeResult(
            cost_usd=0.05,
            num_turns=10,
            duration_ms=5000,
            result_text=f"All done.\n{BUILD_COMPLETE_MARKER}",
            is_error=False,
        )
        # Grader: command exits 0
        sandbox_instance.exec.return_value = ExecuteResult(exit_code=0, output="ok")

        task = TaskSpec(
            id="test-task",
            max_turns=5,
            graders=[{"name": "check", "type": "command", "command": "true"}],
        )
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, 0, evals_dir=evals_dir, config=config, run_id="r1", wheels=[],
        )

        assert trial.build_complete is True
        assert len(trial.iterations) == 1
        assert trial.iterations[0].build_complete is True
        assert trial.total_cost_usd == 0.05
        sandbox_instance.teardown.assert_called_once()

    @patch("prism.eval.harness.EvalSandbox")
    def test_trial_with_error(self, mock_sandbox_cls: MagicMock, evals_dir: Path):
        """Test a trial where sandbox creation fails."""
        sandbox_instance = mock_sandbox_cls.return_value
        sandbox_instance.create.side_effect = RuntimeError("Daytona is down")

        task = TaskSpec(id="test-task")
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, 0, evals_dir=evals_dir, config=config, run_id="r1", wheels=[],
        )

        assert trial.error is not None
        assert "Daytona is down" in trial.error
        sandbox_instance.teardown.assert_called_once()

    @patch("prism.eval.harness.EvalSandbox")
    def test_trial_incomplete(self, mock_sandbox_cls: MagicMock, evals_dir: Path):
        """Test a trial where the build does not complete."""
        sandbox_instance = mock_sandbox_cls.return_value
        sandbox_instance.WORK_DIR = "/home/user/project"

        sandbox_instance.exec_claude.return_value = ClaudeResult(
            cost_usd=0.02,
            num_turns=5,
            duration_ms=3000,
            result_text="Still working...",
            is_error=False,
        )
        sandbox_instance.exec.return_value = ExecuteResult(exit_code=1, output="not done")

        task = TaskSpec(
            id="test-task",
            max_turns=5,
            graders=[{"name": "check", "type": "command", "command": "true"}],
        )
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, 0, evals_dir=evals_dir, config=config, run_id="r1", wheels=[],
        )

        assert trial.build_complete is False
        assert len(trial.iterations) == 1
        assert trial.total_cost_usd == pytest.approx(0.02)


class TestSidecarFiles:
    @patch("prism.eval.harness.EvalSandbox")
    def test_sidecar_written(self, mock_sandbox_cls: MagicMock, evals_dir: Path, tmp_path: Path):
        """Test that JSONL sidecar files are written when sidecar_dir is provided."""
        sandbox_instance = mock_sandbox_cls.return_value
        sandbox_instance.WORK_DIR = "/home/user/project"

        raw_jsonl = '{"type":"assistant","message":"hello"}\n{"type":"result","cost_usd":0.05}\n'
        sandbox_instance.exec_claude.return_value = ClaudeResult(
            cost_usd=0.05, num_turns=3, duration_ms=1000,
            result_text=BUILD_COMPLETE_MARKER, is_error=False,
            raw_jsonl=raw_jsonl,
        )
        sandbox_instance.exec.return_value = ExecuteResult(exit_code=0, output="ok")

        task = TaskSpec(
            id="test-task", max_turns=5,
            graders=[{"name": "check", "type": "command", "command": "true"}],
        )
        config = EvalRunConfig(id="test-run")

        sidecar_dir = tmp_path / "logs"
        trial = run_trial(
            task, 0, evals_dir=evals_dir, config=config,
            run_id="r1", wheels=[], sidecar_dir=sidecar_dir,
        )

        assert trial.iterations[0].transcript_path is not None
        full_path = sidecar_dir.parent / trial.iterations[0].transcript_path
        assert full_path.exists()
        assert full_path.read_text() == raw_jsonl

    @patch("prism.eval.harness.EvalSandbox")
    def test_no_sidecar_without_dir(self, mock_sandbox_cls: MagicMock, evals_dir: Path):
        """transcript_path stays None when no sidecar_dir is given."""
        sandbox_instance = mock_sandbox_cls.return_value
        sandbox_instance.WORK_DIR = "/home/user/project"

        sandbox_instance.exec_claude.return_value = ClaudeResult(
            cost_usd=0.01, num_turns=1, duration_ms=100,
            result_text=BUILD_COMPLETE_MARKER, is_error=False,
            raw_jsonl='{"type":"result"}\n',
        )
        sandbox_instance.exec.return_value = ExecuteResult(exit_code=0, output="ok")

        task = TaskSpec(
            id="test-task", max_turns=5,
            graders=[{"name": "check", "type": "command", "command": "true"}],
        )
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, 0, evals_dir=evals_dir, config=config, run_id="r1", wheels=[],
        )
        assert trial.iterations[0].transcript_path is None


class TestParseClaudeOutput:
    def test_jsonl_with_result_line(self):
        """Parse stream-json JSONL output."""
        jsonl = (
            '{"type":"assistant","message":"working on it"}\n'
            '{"type":"tool","name":"bash","output":"ok"}\n'
            '{"type":"result","cost_usd":0.12,"num_turns":5,"duration_ms":3000,'
            '"result":"All done.\\nBUILD_COMPLETE","is_error":false}\n'
        )
        result = _parse_claude_output(jsonl, exit_code=0, duration_ms=4000)
        assert result.cost_usd == 0.12
        assert result.num_turns == 5
        assert result.duration_ms == 3000
        assert "BUILD_COMPLETE" in result.result_text
        assert result.is_error is False
        assert result.raw_jsonl == jsonl

    def test_total_cost_usd_field(self):
        """Handle total_cost_usd field name (used in actual Claude output)."""
        jsonl = (
            '{"type":"result","total_cost_usd":0.15,'
            '"num_turns":7,"result":"ok","is_error":false}\n'
        )
        result = _parse_claude_output(jsonl, exit_code=0, duration_ms=1000)
        assert result.cost_usd == 0.15

    def test_error_exit_code(self):
        result = _parse_claude_output("some error output", exit_code=1, duration_ms=100)
        assert result.is_error is True
        assert result.result_text == "some error output"
        assert result.raw_jsonl == "some error output"

    def test_unparseable_output(self):
        result = _parse_claude_output("not json at all", exit_code=0, duration_ms=100)
        assert result.is_error is True
        assert result.result_text == "not json at all"
