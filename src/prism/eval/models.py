"""Pydantic data models for the eval harness."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class GraderType(StrEnum):
    """Supported grader types."""

    command = "command"
    status = "status"
    files_exist = "files_exist"
    script = "script"


class GraderSpec(BaseModel):
    """Specification for a single grader."""

    name: str
    type: GraderType
    command: str | None = None
    paths: list[str] | None = None
    script: str | None = None
    weight: float = 1.0
    timeout: int = 120


class TaskSpec(BaseModel):
    """Specification for an eval task (loaded from task.yaml)."""

    id: str
    description: str = ""
    seed_project: str = ""
    universe: str = "baseline"
    max_iterations: int = 15
    max_turns: int = 25
    iteration_timeout: int = 600
    trial_timeout: int = 7200
    graders: list[GraderSpec] = Field(default_factory=list)


class Variant(BaseModel):
    """A prompt/config variant to evaluate."""

    id: str
    description: str = ""
    file_overrides: dict[str, str] = Field(default_factory=dict)
    inline_overrides: dict[str, str] = Field(default_factory=dict)
    env_vars: dict[str, str] = Field(default_factory=dict)
    model: str | None = None


class IterationResult(BaseModel):
    """Result from a single build-loop iteration."""

    iteration: int
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_seconds: float = 0.0
    build_complete: bool = False
    output_summary: str = ""
    error: str | None = None


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
    """Result of a single trial (one task x variant x repetition)."""

    trial_id: str
    task_id: str
    variant_id: str
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
    """Configuration for an eval run (loaded from run config YAML)."""

    id: str = ""
    tasks: list[str] = Field(default_factory=list)
    variants: list[str] = Field(default_factory=list)
    num_trials: int = 3
    max_concurrency: int = 4
    sandbox_image: str = "ghcr.io/lightconeresearch/prism-eval:latest"
    output_dir: str = "eval-results"


class EvalRun(BaseModel):
    """Complete results of an eval run."""

    config: EvalRunConfig
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trials: list[TrialResult] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
