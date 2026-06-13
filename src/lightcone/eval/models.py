"""Pydantic data models for the eval harness."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class HarnessSpec(BaseModel):
    """One agent harness in the run matrix, with an optional model override.

    Accepts a bare string in YAML (``- claude``) or a mapping
    (``{name: codex, model: gpt-5.3-codex-spark}``).
    """

    name: str
    model: str | None = None


class GraderType(StrEnum):
    """Supported grader types."""

    command = "command"
    status = "status"
    script = "script"


class GraderSpec(BaseModel):
    """Specification for a single grader."""

    name: str
    type: GraderType
    command: str | None = None
    script: str | None = None
    weight: float = 1.0
    timeout: int = 120


class TaskSpec(BaseModel):
    """Specification for an eval task (loaded from task.yaml)."""

    id: str
    description: str = ""
    seed_project: str = ""
    universe: str = "baseline"
    max_turns: int = 200
    trial_timeout: int = 7200
    graders: list[GraderSpec] = Field(default_factory=list)


class IterationResult(BaseModel):
    """Result from a single build-loop iteration."""

    iteration: int
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_seconds: float = 0.0
    build_complete: bool = False
    output_summary: str = ""
    error: str | None = None
    transcript_path: str | None = None


class GraderResult(BaseModel):
    """Result from a single grader."""

    name: str
    type: GraderType
    passed: bool = False
    score: float = 0.0
    weight: float = 1.0
    details: str = ""
    error: str | None = None


class TrialResult(BaseModel):
    """Result of a single trial (one task x harness x skill-variant x rep)."""

    trial_id: str
    task_id: str
    harness: str = "claude"
    with_skills: bool = True
    trial_number: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    iterations: list[IterationResult] = Field(default_factory=list)
    grader_results: list[GraderResult] = Field(default_factory=list)
    composite_score: float = 0.0
    build_complete: bool = False
    total_cost_usd: float = 0.0
    total_turns: int = 0
    total_duration_seconds: float = 0.0
    error: str | None = None


class EvalRunConfig(BaseModel):
    """Configuration for an eval run (loaded from run config YAML).

    The run is a matrix: ``tasks`` x ``harnesses`` x ``skill_variants`` x
    ``num_trials``. ``skill_variants`` is the with/without-Lightcone A/B —
    ``[true]`` for a single arm, ``[true, false]`` to measure the lift.
    """

    id: str = ""
    tasks: list[str] = Field(default_factory=list)
    backend: str = "daytona"  # "daytona" | "local_docker"
    harnesses: list[HarnessSpec] = Field(
        default_factory=lambda: [HarnessSpec(name="claude")]
    )
    skill_variants: list[bool] = Field(default_factory=lambda: [True])
    num_trials: int = 3
    max_concurrency: int = 4
    sandbox_image: str | None = None
    output_dir: str = "eval-results"
    # Optional run-level overrides of the task's budget — handy for cheap
    # plumbing smokes (cap turns so a trial exits fast without a full build).
    max_turns: int | None = None
    trial_timeout: int | None = None

    @field_validator("harnesses", mode="before")
    @classmethod
    def _coerce_harnesses(cls, v: object) -> object:
        """Allow ``harnesses: [claude, {name: codex, model: ...}]`` — bare
        strings become ``HarnessSpec(name=...)``."""
        if isinstance(v, list):
            return [{"name": h} if isinstance(h, str) else h for h in v]
        return v


class VersionInfo(BaseModel):
    """Git and wheel version metadata for reproducibility."""

    lightcone_commit: str = ""
    lightcone_branch: str = ""
    lightcone_dirty: bool = False
    lightcone_version: str = ""
    astra_version: str = ""


class EvalRun(BaseModel):
    """Complete results of an eval run."""

    config: EvalRunConfig
    version: VersionInfo = Field(default_factory=VersionInfo)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trials: list[TrialResult] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    transcript_dir: str | None = None
    run_stem: str | None = None
