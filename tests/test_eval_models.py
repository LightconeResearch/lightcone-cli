"""Tests for eval data models."""

from __future__ import annotations

from datetime import UTC, datetime

from lightcone.eval.models import (
    EvalRun,
    EvalRunConfig,
    GraderResult,
    GraderSpec,
    GraderType,
    IterationResult,
    TaskSpec,
    TrialResult,
    VersionInfo,
)


class TestGraderSpec:
    def test_command_grader(self):
        g = GraderSpec(name="check", type=GraderType.command, command="echo ok")
        assert g.name == "check"
        assert g.type == GraderType.command
        assert g.weight == 1.0
        assert g.timeout == 120

    def test_status_grader(self):
        g = GraderSpec(name="status", type=GraderType.status, weight=3.0)
        assert g.weight == 3.0

    def test_script_grader(self):
        g = GraderSpec(name="custom", type=GraderType.script, script="check.py")
        assert g.script == "check.py"


class TestTaskSpec:
    def test_defaults(self):
        t = TaskSpec(id="test")
        assert t.max_turns == 200
        assert t.trial_timeout == 7200
        assert t.graders == []

    def test_full_spec(self):
        t = TaskSpec(
            id="stellar-mass",
            description="Test task",
            universe="baseline",
            graders=[GraderSpec(name="g1", type=GraderType.command, command="true")],
        )
        assert len(t.graders) == 1


class TestIterationResult:
    def test_defaults(self):
        ir = IterationResult(iteration=0)
        assert ir.cost_usd == 0.0
        assert ir.build_complete is False

    def test_with_data(self):
        ir = IterationResult(
            iteration=1, cost_usd=0.05, num_turns=10, build_complete=True
        )
        assert ir.build_complete is True


class TestTrialResult:
    def test_roundtrip_json(self):
        trial = TrialResult(
            trial_id="t-1",
            task_id="task1",
            started_at=datetime(2026, 3, 15, tzinfo=UTC),
            iterations=[IterationResult(iteration=0, cost_usd=0.1)],
            grader_results=[
                GraderResult(name="g1", type=GraderType.command, passed=True, score=1.0)
            ],
            composite_score=1.0,
            build_complete=True,
            total_cost_usd=0.1,
        )
        data = trial.model_dump(mode="json")
        restored = TrialResult(**data)
        assert restored.trial_id == "t-1"
        assert restored.composite_score == 1.0
        assert len(restored.iterations) == 1


class TestEvalRunConfig:
    def test_defaults(self):
        config = EvalRunConfig()
        assert config.num_trials == 3
        assert config.max_concurrency == 4

    def test_from_dict(self):
        config = EvalRunConfig(
            id="test-run",
            tasks=["t1"],
            num_trials=5,
        )
        assert config.id == "test-run"
        assert config.tasks == ["t1"]


class TestVersionInfo:
    def test_defaults(self):
        v = VersionInfo()
        assert v.lightcone_commit == ""
        assert v.lightcone_dirty is False

    def test_roundtrip(self):
        v = VersionInfo(
            lightcone_commit="abc123",
            lightcone_branch="main",
            lightcone_dirty=True,
            lightcone_version="0.0.2",
            astra_version="0.0.8",
        )
        data = v.model_dump(mode="json")
        restored = VersionInfo(**data)
        assert restored.lightcone_commit == "abc123"
        assert restored.lightcone_dirty is True


class TestEvalRun:
    def test_empty_run(self):
        run = EvalRun(config=EvalRunConfig())
        assert run.trials == []
        assert run.summary == {}

    def test_roundtrip(self):
        run = EvalRun(
            config=EvalRunConfig(id="r1", tasks=["t1"]),
            version=VersionInfo(lightcone_commit="abc123"),
            started_at=datetime(2026, 3, 15, tzinfo=UTC),
            trials=[
                TrialResult(
                    trial_id="t-1",
                    task_id="t1",
                    composite_score=0.75,
                )
            ],
        )
        data = run.model_dump(mode="json")
        restored = EvalRun(**data)
        assert len(restored.trials) == 1
        assert restored.trials[0].composite_score == 0.75
        assert restored.version.lightcone_commit == "abc123"
