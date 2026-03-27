"""Tests for eval harness with mock Daytona."""

from __future__ import annotations

import json
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
from prism.eval.models import (
    EvalAnalysis,
    EvalRunConfig,
    IterationAnalysis,
    IterationResult,
    TaskSpec,
    TokenUsage,
    TrialAnalysis,
    Variant,
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
    variants = evals / "variants"
    variants.mkdir(parents=True)

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
        assert task.max_turns == 5
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

        task = TaskSpec(id="test-task")
        variant = Variant(id="baseline")
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, variant, 0, evals_dir=evals_dir, config=config, run_id="r1"
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
        variant = Variant(id="baseline")
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, variant, 0, evals_dir=evals_dir, config=config, run_id="r1"
        )

        assert trial.build_complete is False
        assert len(trial.iterations) == 1
        assert trial.total_cost_usd == pytest.approx(0.02)


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

    @patch("prism.eval.harness.EvalSandbox")
    def test_run_eval_sets_run_stem(self, mock_sandbox_cls: MagicMock, evals_dir: Path):
        sandbox_instance = mock_sandbox_cls.return_value
        sandbox_instance.WORK_DIR = "/home/user/project"
        sandbox_instance.exec_claude.return_value = ClaudeResult(
            cost_usd=0.01, num_turns=1, duration_ms=100,
            result_text=BUILD_COMPLETE_MARKER, is_error=False,
        )
        sandbox_instance.exec.return_value = ExecuteResult(exit_code=0, output="ok")

        config = EvalRunConfig(
            id="test", tasks=["test-task"], variants=["baseline"], num_trials=1,
            max_concurrency=1,
        )
        result = run_eval(config, evals_dir)
        assert result.run_stem is not None
        assert result.run_stem.startswith("test-")
        assert result.transcript_dir is not None


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
        variant = Variant(id="baseline")
        config = EvalRunConfig(id="test-run")

        sidecar_dir = tmp_path / "logs"
        trial = run_trial(
            task, variant, 0, evals_dir=evals_dir, config=config,
            run_id="r1", sidecar_dir=sidecar_dir,
        )

        assert trial.iterations[0].transcript_path is not None
        # The actual JSONL file should exist
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
        variant = Variant(id="baseline")
        config = EvalRunConfig(id="test-run")

        trial = run_trial(
            task, variant, 0, evals_dir=evals_dir, config=config, run_id="r1"
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


class TestLoadTranscripts:
    def test_loads_from_sidecar_dir(self, tmp_path: Path):
        """Test loading transcripts by convention path."""
        from prism.eval.models import EvalRun, EvalRunConfig, TrialResult
        from prism.eval.report import load_transcripts, save_results

        eval_run = EvalRun(
            config=EvalRunConfig(id="test", tasks=["t1"], variants=["v1"]),
            run_stem="test-20260327-120000",
            trials=[
                TrialResult(
                    trial_id="r1-t1-v1-0", task_id="t1", variant_id="v1",
                    iterations=[
                        IterationResult(
                            iteration=0,
                            transcript_path="test-20260327-120000/logs/r1-t1-v1-0/transcript.jsonl",
                        ),
                    ],
                ),
            ],
        )

        # Save the results JSON
        results_path = save_results(eval_run, tmp_path)

        # Create the sidecar files
        log_dir = tmp_path / "test-20260327-120000" / "logs" / "r1-t1-v1-0"
        log_dir.mkdir(parents=True)
        (log_dir / "transcript.jsonl").write_text('{"type":"result"}\n')

        transcripts = load_transcripts(results_path)
        assert "r1-t1-v1-0" in transcripts
        assert 0 in transcripts["r1-t1-v1-0"]
        assert '{"type":"result"}' in transcripts["r1-t1-v1-0"][0]

    def test_empty_when_no_sidecars(self, tmp_path: Path):
        from prism.eval.models import EvalRun, EvalRunConfig, TrialResult
        from prism.eval.report import load_transcripts, save_results

        eval_run = EvalRun(
            config=EvalRunConfig(id="test"),
            trials=[
                TrialResult(trial_id="r1-t1-v1-0", task_id="t1", variant_id="v1"),
            ],
        )
        results_path = save_results(eval_run, tmp_path)
        transcripts = load_transcripts(results_path)
        assert transcripts == {}


class TestAnalysisModels:
    def test_iteration_analysis_roundtrip(self):
        ia = IterationAnalysis(
            iteration=0,
            pain_points=["got stuck on imports"],
            failure_modes=["wrong package version"],
            summary="Agent struggled with dependencies",
        )
        data = ia.model_dump(mode="json")
        restored = IterationAnalysis.model_validate(data)
        assert restored.pain_points == ["got stuck on imports"]

    def test_trial_analysis_roundtrip(self):
        ta = TrialAnalysis(
            trial_id="r1-t1-v1-0",
            task_id="t1",
            variant_id="v1",
            overall_summary="Failed due to missing deps",
            primary_failure_mode="dependency resolution",
            usage=TokenUsage(input_tokens=5000, output_tokens=500),
        )
        data = ta.model_dump(mode="json")
        restored = TrialAnalysis.model_validate(data)
        assert restored.primary_failure_mode == "dependency resolution"
        assert restored.usage.input_tokens == 5000

    def test_eval_analysis_roundtrip(self):
        analysis = EvalAnalysis(
            run_config_id="test",
            model="claude-sonnet-4-20250514",
            common_patterns=["pattern1"],
            common_failure_modes=["mode1"],
            recommendations=["rec1"],
            total_usage=TokenUsage(input_tokens=10000, output_tokens=1000),
        )
        data = analysis.model_dump(mode="json")
        restored = EvalAnalysis.model_validate(data)
        assert restored.common_patterns == ["pattern1"]
        assert restored.total_usage.input_tokens == 10000


class TestAnalysisFunctions:
    def test_extract_json(self):
        from prism.eval.analysis import _extract_json

        # Plain JSON
        assert _extract_json('{"key": "value"}') == {"key": "value"}

        # JSON in markdown fence
        text = 'Here is the result:\n```json\n{"key": "value"}\n```\n'
        assert _extract_json(text) == {"key": "value"}

        # Invalid
        with pytest.raises(ValueError):
            _extract_json("not json at all")

    def test_estimate_cost_sonnet(self):
        from prism.eval.analysis import estimate_cost

        usage = TokenUsage(input_tokens=1_000_000, output_tokens=100_000)
        # Sonnet: $3/M input + $15/M output → $3 + $1.5 = $4.5
        assert estimate_cost(usage, "claude-sonnet-4-20250514") == pytest.approx(4.5)

    def test_estimate_cost_opus(self):
        from prism.eval.analysis import estimate_cost

        usage = TokenUsage(input_tokens=1_000_000, output_tokens=100_000)
        # Opus: $15/M input + $75/M output → $15 + $7.5 = $22.5
        assert estimate_cost(usage, "claude-opus-4-20250514") == pytest.approx(22.5)

    def test_estimate_cost_with_cache(self):
        from prism.eval.analysis import estimate_cost

        usage = TokenUsage(
            input_tokens=500_000,
            output_tokens=100_000,
            cache_creation_input_tokens=200_000,
            cache_read_input_tokens=300_000,
        )
        # Sonnet: 0.5M*$3 + 0.1M*$15 + 0.2M*$3.75 + 0.3M*$0.30
        # = $1.50 + $1.50 + $0.75 + $0.09 = $3.84
        assert estimate_cost(usage, "claude-sonnet-4-20250514") == pytest.approx(3.84)

    def test_analyze_transcript_with_mock(self):
        from prism.eval.analysis import analyze_transcript

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "pain_points": ["stuck on docker"],
            "failure_modes": ["wrong base image"],
            "wasted_loops": [],
            "key_decisions": ["chose alpine"],
            "summary": "Agent struggled with container setup",
        }))]
        mock_response.usage.input_tokens = 1000
        mock_response.usage.output_tokens = 200
        mock_response.usage.cache_creation_input_tokens = 0
        mock_response.usage.cache_read_input_tokens = 0
        mock_client.messages.create.return_value = mock_response

        analysis, usage = analyze_transcript(mock_client, '{"type":"result"}\n', "analyze this")
        assert analysis.pain_points == ["stuck on docker"]
        assert analysis.summary == "Agent struggled with container setup"
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 200

    def test_save_analysis(self, tmp_path: Path):
        from prism.eval.analysis import save_analysis

        analysis = EvalAnalysis(
            run_config_id="test",
            common_patterns=["p1"],
            total_analysis_cost_usd=0.005,
        )
        results_path = tmp_path / "test-20260327.json"
        results_path.write_text("{}")

        output = save_analysis(analysis, results_path)
        assert output.name == "test-20260327-analysis.json"
        assert output.exists()
        data = json.loads(output.read_text())
        assert data["common_patterns"] == ["p1"]

    def test_save_analysis_custom_prompt(self, tmp_path: Path):
        from prism.eval.analysis import save_analysis

        analysis = EvalAnalysis(
            run_config_id="test",
            prompt_file="my-custom-prompt.md",
        )
        results_path = tmp_path / "test-20260327.json"
        results_path.write_text("{}")

        output = save_analysis(analysis, results_path)
        assert output.name == "test-20260327-analysis-my-custom-prompt.json"
