"""Tests for eval report generation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from prism.eval.models import (
    EvalRun,
    EvalRunConfig,
    GraderResult,
    GraderType,
    IterationResult,
    TrialResult,
)
from prism.eval.report import (
    compute_summary,
    load_results,
    print_comparison_between,
    print_comparison_table,
    save_results,
)


def _make_trial(
    task_id: str = "t1",
    score: float = 0.8,
    cost: float = 0.1,
    duration: float = 60.0,
    build_complete: bool = True,
    error: str | None = None,
) -> TrialResult:
    return TrialResult(
        trial_id=f"{task_id}-0",
        task_id=task_id,
        iterations=[IterationResult(iteration=0, cost_usd=cost, duration_seconds=duration)],
        grader_results=[
            GraderResult(name="g1", type=GraderType.command, score=score, weight=1.0)
        ],
        composite_score=score,
        build_complete=build_complete,
        total_cost_usd=cost,
        total_duration_seconds=duration,
        error=error,
    )


def _make_eval_run(trials: list[TrialResult] | None = None) -> EvalRun:
    return EvalRun(
        config=EvalRunConfig(id="test", tasks=["t1"]),
        started_at=datetime(2026, 3, 15, tzinfo=UTC),
        trials=trials or [_make_trial()],
    )


class TestComputeSummary:
    def test_single_trial(self):
        run = _make_eval_run()
        summary = compute_summary(run)
        groups = summary["groups"]
        assert "t1" in groups
        g = groups["t1"]
        assert g["mean_score"] == 0.8
        assert g["pass_at_k"] == 1.0
        assert g["mean_cost_usd"] == 0.1

    def test_multiple_trials(self):
        run = _make_eval_run([
            _make_trial(score=1.0, cost=0.1),
            _make_trial(score=0.5, cost=0.2),
        ])
        summary = compute_summary(run)
        g = summary["groups"]["t1"]
        assert g["mean_score"] == 0.75
        assert g["num_trials"] == 2

    def test_with_errors(self):
        run = _make_eval_run([
            _make_trial(score=1.0),
            _make_trial(error="boom"),
        ])
        summary = compute_summary(run)
        g = summary["groups"]["t1"]
        assert g["num_errors"] == 1
        # Error trials excluded from score calculation
        assert g["mean_score"] == 1.0

    def test_totals(self):
        run = _make_eval_run([
            _make_trial(cost=0.1, duration=60.0),
            _make_trial(cost=0.2, duration=120.0),
        ])
        summary = compute_summary(run)
        assert summary["totals"]["total_trials"] == 2
        assert summary["totals"]["total_cost_usd"] == 0.3


class TestPrintComparison:
    def test_prints_without_error(self):
        """Just verify it doesn't crash."""
        run = _make_eval_run()
        run.summary = compute_summary(run)
        console = Console(file=None, force_terminal=False)
        print_comparison_table(run, console=console)

    def test_empty_run(self):
        run = _make_eval_run([])
        run.summary = compute_summary(run)
        console = Console(file=None, force_terminal=False)
        print_comparison_table(run, console=console)


class TestPrintComparisonBetween:
    def test_prints_without_error(self):
        run1 = _make_eval_run([_make_trial(score=0.5)])
        run2 = _make_eval_run([_make_trial(score=0.9)])
        run1.summary = compute_summary(run1)
        run2.summary = compute_summary(run2)
        console = Console(file=None, force_terminal=False)
        print_comparison_between(run1, run2, console=console)


class TestSaveAndLoad:
    def test_roundtrip(self, tmp_path: Path):
        run = _make_eval_run()
        run.run_stem = "test-abc123"
        run.summary = compute_summary(run)
        path = save_results(run, tmp_path)
        assert path.exists()
        assert path.name == "results.json"
        assert "test-abc123" in str(path.parent)

        loaded = load_results(path)
        assert loaded.config.id == "test"
        assert len(loaded.trials) == 1
        assert loaded.trials[0].composite_score == 0.8

    def test_json_is_valid(self, tmp_path: Path):
        run = _make_eval_run()
        run.run_stem = "test-abc123"
        run.summary = compute_summary(run)
        path = save_results(run, tmp_path)
        data = json.loads(path.read_text())
        assert "config" in data
        assert "trials" in data
