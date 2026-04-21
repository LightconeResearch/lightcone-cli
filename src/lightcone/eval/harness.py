"""Core eval loop — runs trials with ThreadPoolExecutor concurrency."""

from __future__ import annotations

import logging
import signal
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from lightcone.eval.build import build_eval_wheels
from lightcone.eval.graders import compute_composite_score, run_graders
from lightcone.eval.models import (
    EvalRun,
    EvalRunConfig,
    IterationResult,
    TaskSpec,
    TrialResult,
)
from lightcone.eval.sandbox import BUILD_COMPLETE_MARKER, EvalSandbox

logger = logging.getLogger(__name__)

DEFAULT_LOOP_PROMPT = """\
/lc-build this analysis and make sure to cover universe {{UNIVERSE}}.
Do NOT ask for plan approval — skip straight to building. This is an automated eval run.
"""


def load_task(evals_dir: Path, task_id: str) -> TaskSpec:
    """Load a TaskSpec from evals/tasks/<task_id>/task.yaml."""
    task_file = evals_dir / "tasks" / task_id / "task.yaml"
    try:
        data = yaml.safe_load(task_file.read_text())
    except FileNotFoundError:
        raise FileNotFoundError(f"Task not found: {task_file}") from None
    return TaskSpec(**data)


def load_run_config(config_path: Path) -> EvalRunConfig:
    """Load an EvalRunConfig from a YAML file."""
    data = yaml.safe_load(config_path.read_text())
    return EvalRunConfig(**data)


def _get_loop_prompt(evals_dir: Path, task_id: str) -> str:
    """Get loop prompt template: task-specific or default."""
    task_prompt = evals_dir / "tasks" / task_id / "loop-prompt.md"
    if task_prompt.exists():
        return task_prompt.read_text()
    return DEFAULT_LOOP_PROMPT


def run_trial(
    task: TaskSpec,
    trial_number: int,
    *,
    evals_dir: Path,
    config: EvalRunConfig,
    run_id: str,
    wheels: list[Path],
    sidecar_dir: Path | None = None,
) -> TrialResult:
    """Run a single trial: create sandbox -> run /lc-build -> grade -> teardown."""
    trial_id = f"{run_id}-{task.id}-{trial_number}"
    trial = TrialResult(
        trial_id=trial_id,
        task_id=task.id,
        trial_number=trial_number,
        started_at=datetime.now(UTC),
    )

    env_vars = {
        "LIGHTCONE_EVAL_RUN_ID": run_id,
        "CLAUDE_CODE_SESSION_ID": f"eval-{trial_id}",
    }

    sandbox = EvalSandbox(
        task_id=task.id,
        trial_id=trial_id,
        sandbox_image=config.sandbox_image,
        env_vars=env_vars,
    )

    try:
        sandbox.create()

        seed_dir = evals_dir / "tasks" / task.id
        loop_prompt = _get_loop_prompt(evals_dir, task.id)

        sandbox.setup(
            seed_dir=seed_dir,
            universe=task.universe,
            loop_prompt_template=loop_prompt,
            wheels=wheels,
        )

        # Single invocation: /lc-build handles its own loop internally
        start = time.monotonic()
        try:
            claude_result = sandbox.exec_claude(
                max_turns=task.max_turns,
                timeout=task.trial_timeout,
            )
            duration = time.monotonic() - start

            build_complete = BUILD_COMPLETE_MARKER in claude_result.result_text
            iteration = IterationResult(
                iteration=0,
                cost_usd=claude_result.cost_usd,
                num_turns=claude_result.num_turns,
                duration_seconds=duration,
                build_complete=build_complete,
                output_summary=(
                    "" if claude_result.is_error else claude_result.result_text[:500]
                ),
                error=claude_result.result_text[:500] if claude_result.is_error else None,
            )

            # Save transcript sidecar
            if sidecar_dir is not None and claude_result.raw_jsonl:
                trial_log_dir = sidecar_dir / trial_id
                trial_log_dir.mkdir(parents=True, exist_ok=True)
                jsonl_path = trial_log_dir / "transcript.jsonl"
                jsonl_path.write_text(claude_result.raw_jsonl)
                iteration.transcript_path = str(
                    jsonl_path.relative_to(sidecar_dir.parent)
                )
        except Exception as exc:
            duration = time.monotonic() - start
            iteration = IterationResult(
                iteration=0,
                duration_seconds=duration,
                error=str(exc),
            )

        trial.iterations.append(iteration)
        trial.build_complete = iteration.build_complete

        # Run graders
        trial.grader_results = run_graders(sandbox, task.graders, evals_dir, task.id)
        trial.composite_score = compute_composite_score(trial.grader_results)

        # Aggregate metrics
        trial.total_cost_usd = sum(it.cost_usd for it in trial.iterations)
        trial.total_turns = sum(it.num_turns for it in trial.iterations)
        trial.total_duration_seconds = sum(it.duration_seconds for it in trial.iterations)

    except Exception as exc:
        logger.error("Trial %s failed: %s", trial_id, exc, exc_info=True)
        trial.error = str(exc)
    finally:
        sandbox.teardown()

    trial.finished_at = datetime.now(UTC)
    return trial


def run_eval(
    config: EvalRunConfig,
    evals_dir: Path,
    *,
    progress_callback: Callable[[TrialResult], None] | None = None,
    dry_run: bool = False,
) -> EvalRun:
    """Run all trials: tasks x num_trials with ThreadPoolExecutor."""
    run_id = config.id or str(uuid.uuid4())[:8]

    # Load all tasks
    tasks = [load_task(evals_dir, tid) for tid in config.tasks]

    # Build trial schedule
    schedule: list[dict[str, Any]] = []
    for task in tasks:
        for n in range(config.num_trials):
            schedule.append({"task": task, "trial_number": n})

    if dry_run:
        return EvalRun(
            config=config,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            summary={"dry_run": True, "total_trials": len(schedule), "schedule": [
                {"task": s["task"].id, "trial": s["trial_number"]}
                for s in schedule
            ]},
        )

    # Build lightcone-cli wheel from current working tree + collect ASTRA wheel
    version_info, wheels = build_eval_wheels(evals_dir)

    # Compute run stem using git commit for traceability
    commit_short = version_info.lightcone_commit[:8] or "unknown"
    run_stem = f"{run_id}-{commit_short}"
    output_base = Path(config.output_dir)
    sidecar_dir = output_base / run_stem / "logs"

    eval_run = EvalRun(
        config=config,
        version=version_info,
        started_at=datetime.now(UTC),
        run_stem=run_stem,
        transcript_dir=str(sidecar_dir),
    )

    # Handle SIGINT: save partial results
    interrupted = False

    def _signal_handler(signum: int, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        logger.warning("SIGINT received — finishing current trials and saving partial results")

    is_main_thread = threading.current_thread() is threading.main_thread()
    if is_main_thread:
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _signal_handler)

    try:
        with ThreadPoolExecutor(max_workers=config.max_concurrency) as pool:
            futures = {
                pool.submit(
                    run_trial,
                    s["task"],
                    s["trial_number"],
                    evals_dir=evals_dir,
                    config=config,
                    run_id=run_id,
                    wheels=wheels,
                    sidecar_dir=sidecar_dir,
                ): s
                for s in schedule
            }

            for future in as_completed(futures):
                if interrupted:
                    break

                try:
                    trial = future.result()
                except Exception as exc:
                    s = futures[future]
                    trial = TrialResult(
                        trial_id=f"{run_id}-error",
                        task_id=s["task"].id,
                        trial_number=s["trial_number"],
                        error=str(exc),
                    )

                eval_run.trials.append(trial)
                if progress_callback:
                    progress_callback(trial)
    finally:
        if is_main_thread:
            signal.signal(signal.SIGINT, original_handler)

    eval_run.finished_at = datetime.now(UTC)
    return eval_run
