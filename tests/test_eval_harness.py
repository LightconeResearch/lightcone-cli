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
    load_variant,
    run_eval,
    run_trial,
)
from prism.eval.models import EvalRunConfig, TaskSpec, Variant
from prism.eval.sandbox import BUILD_COMPLETE_MARKER, ClaudeResult, ExecuteResult


@pytest.fixture
def evals_dir(tmp_path: Path) -> Path:
    """Create a minimal evals directory."""
    evals = tmp_path / "evals"
    tasks = evals / "tasks" / "test-task"
    tasks.mkdir(parents=True)
    variants = evals / "variants"
    variants.mkdir(parents=True)

    # Task
    (tasks / "task.yaml").write_text(yaml.dump({
        "id": "test-task",
        "description": "A test task",
        "universe": "baseline",
        "max_iterations": 2,
        "max_turns": 5,
        "iteration_timeout": 10,
        "trial_timeout": 30,
        "graders": [
            {"name": "check", "type": "command", "command": "true"},
        ],
    }))

    # Seed astra.yaml
    (tasks / "astra.yaml").write_text("version: '1.0'\nname: test\n")

    # Variant
    (variants / "baseline.yaml").write_text(yaml.dump({
        "id": "baseline",
        "description": "no changes",
    }))

    return evals


@pytest.fixture
def run_config_path(tmp_path: Path) -> Path:
    """Create a minimal run config."""
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.dump({
        "id": "test-run",
        "tasks": ["test-task"],
        "variants": ["baseline"],
        "num_trials": 1,
        "max_concurrency": 1,
    }))
    return config_path


class TestLoadTask:
    def test_loads_valid_task(self, evals_dir: Path):
        task = load_task(evals_dir, "test-task")
        assert task.id == "test-task"
        assert task.max_iterations == 2
        assert len(task.graders) == 1

    def test_missing_task(self, evals_dir: Path):
        with pytest.raises(FileNotFoundError):
            load_task(evals_dir, "nonexistent")


class TestLoadVariant:
    def test_loads_valid_variant(self, evals_dir: Path):
        variant = load_variant(evals_dir, "baseline")
        assert variant.id == "baseline"

    def test_missing_variant(self, evals_dir: Path):
        with pytest.raises(FileNotFoundError):
            load_variant(evals_dir, "nonexistent")


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
        """Test a trial that completes successfully on first iteration."""
        sandbox_instance = mock_sandbox_cls.return_value
        sandbox_instance.WORK_DIR = "/home/user/project"

        sandbox_instance.exec_claude.return_value = ClaudeResult(
            cost_usd=0.05,
            num_turns=10,
            duration_ms=5000,
            result_text=f"All done. {BUILD_COMPLETE_MARKER}",
            is_error=False,
        )
        # Grader: command exits 0
        sandbox_instance.exec.return_value = ExecuteResult(exit_code=0, output="ok")

        task = TaskSpec(
            id="test-task",
            max_iterations=5,
            max_turns=5,
            graders=[{"name": "check", "type": "command", "command": "true"}],
        )
        variant = Variant(id="baseline")
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, variant, 0, evals_dir=evals_dir, config=config, run_id="r1"
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

        task = TaskSpec(id="test-task", max_iterations=2)
        variant = Variant(id="baseline")
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, variant, 0, evals_dir=evals_dir, config=config, run_id="r1"
        )

        assert trial.error is not None
        assert "Daytona is down" in trial.error
        sandbox_instance.teardown.assert_called_once()

    @patch("prism.eval.harness.EvalSandbox")
    def test_trial_max_iterations(self, mock_sandbox_cls: MagicMock, evals_dir: Path):
        """Test a trial that uses all iterations without completing."""
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
            max_iterations=3,
            max_turns=5,
            graders=[{"name": "check", "type": "command", "command": "true"}],
        )
        variant = Variant(id="baseline")
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, variant, 0, evals_dir=evals_dir, config=config, run_id="r1"
        )

        assert trial.build_complete is False
        assert len(trial.iterations) == 3
        assert trial.total_cost_usd == pytest.approx(0.06)


class TestRunEval:
    def test_dry_run(self, evals_dir: Path):
        config = EvalRunConfig(
            id="dry", tasks=["test-task"], variants=["baseline"], num_trials=2
        )
        result = run_eval(config, evals_dir, dry_run=True)
        assert result.summary.get("dry_run") is True
        assert result.summary.get("total_trials") == 2
        assert len(result.summary.get("schedule", [])) == 2

    @patch("prism.eval.harness.EvalSandbox")
    def test_full_run(self, mock_sandbox_cls: MagicMock, evals_dir: Path):
        sandbox_instance = mock_sandbox_cls.return_value
        sandbox_instance.WORK_DIR = "/home/user/project"
        sandbox_instance.exec_claude.return_value = ClaudeResult(
            cost_usd=0.01,
            num_turns=3,
            duration_ms=1000,
            result_text=BUILD_COMPLETE_MARKER,
            is_error=False,
        )
        sandbox_instance.exec.return_value = ExecuteResult(exit_code=0, output="ok")

        config = EvalRunConfig(
            id="test", tasks=["test-task"], variants=["baseline"], num_trials=1,
            max_concurrency=1,
        )
        result = run_eval(config, evals_dir)
        assert len(result.trials) == 1
        assert result.trials[0].build_complete is True
