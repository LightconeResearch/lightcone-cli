"""Core eval loop — runs trials with ThreadPoolExecutor concurrency."""

from __future__ import annotations

import logging
import signal
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from prism.eval.graders import compute_composite_score, run_graders
from prism.eval.models import (
    EvalRun,
    EvalRunConfig,
    IterationResult,
    TaskSpec,
    TrialResult,
    Variant,
)
from prism.eval.sandbox import BUILD_COMPLETE_MARKER, EvalSandbox

logger = logging.getLogger(__name__)

DEFAULT_LOOP_PROMPT = """\
Continue building this ASTRA analysis for universe {{UNIVERSE}}.

Look at the current state via `prism status` and `astra validate astra.yaml`.
Write or fix scripts for any outputs that are not yet materialized.
Run `prism run --universe {{UNIVERSE}}` to execute.

When ALL outputs are materialized and validation passes, respond with: BUILD_COMPLETE
"""


def load_task(evals_dir: Path, task_id: str) -> TaskSpec:
    """Load a TaskSpec from evals/tasks/<task_id>/task.yaml."""
    task_file = evals_dir / "tasks" / task_id / "task.yaml"
    try:
        data = yaml.safe_load(task_file.read_text())
    except FileNotFoundError:
        raise FileNotFoundError(f"Task not found: {task_file}") from None
    return TaskSpec(**data)


def load_variant(evals_dir: Path, variant_id: str) -> Variant:
    """Load a Variant from evals/variants/<variant_id>.yaml."""
    variant_file = evals_dir / "variants" / f"{variant_id}.yaml"
    try:
        data = yaml.safe_load(variant_file.read_text())
    except FileNotFoundError:
        raise FileNotFoundError(f"Variant not found: {variant_file}") from None
    return Variant(**data)


def load_run_config(config_path: Path) -> EvalRunConfig:
    """Load an EvalRunConfig from a YAML file."""
    data = yaml.safe_load(config_path.read_text())
    return EvalRunConfig(**data)


def _get_loop_prompt(evals_dir: Path, task_id: str) -> str:
    """Get loop prompt template: task-specific or default."""
    # Check for task-specific loop prompt
    task_prompt = evals_dir / "tasks" / task_id / "loop-prompt.md"
    if task_prompt.exists():
        return task_prompt.read_text()
    return DEFAULT_LOOP_PROMPT


def run_trial(
    task: TaskSpec,
    variant: Variant,
    trial_number: int,
    *,
    evals_dir: Path,
    config: EvalRunConfig,
    run_id: str,
) -> TrialResult:
    """Run a single trial: create sandbox -> build loop -> grade -> teardown."""
    trial_id = f"{run_id}-{task.id}-{variant.id}-{trial_number}"
    trial = TrialResult(
        trial_id=trial_id,
        task_id=task.id,
        variant_id=variant.id,
        trial_number=trial_number,
        started_at=datetime.now(UTC),
    )

    # Merge variant env vars with eval metadata
    env_vars = {
        **variant.env_vars,
        "PRISM_EVAL_RUN_ID": run_id,
        "CLAUDE_CODE_SESSION_ID": f"eval-{trial_id}",
    }

    sandbox = EvalSandbox(
        task_id=task.id,
        variant_id=variant.id,
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
            variant=variant,
            evals_dir=evals_dir,
            universe=task.universe,
            loop_prompt_template=loop_prompt,
        )

        # Build loop
        trial_start = time.monotonic()
        for i in range(task.max_iterations):
            elapsed = time.monotonic() - trial_start
            if elapsed > task.trial_timeout:
                logger.warning("Trial %s exceeded timeout after %d iterations", trial_id, i)
                break

            iter_start = time.monotonic()
            try:
                claude_result = sandbox.exec_claude(
                    max_turns=task.max_turns,
                    timeout=task.iteration_timeout,
                    model=variant.model,
                )
                iter_duration = time.monotonic() - iter_start

                iteration = IterationResult(
                    iteration=i,
                    cost_usd=claude_result.cost_usd,
                    num_turns=claude_result.num_turns,
                    duration_seconds=iter_duration,
                    build_complete=BUILD_COMPLETE_MARKER in claude_result.result_text,
                    output_summary=claude_result.result_text[:500],
                    error=claude_result.result_text[:500] if claude_result.is_error else None,
                )
            except Exception as exc:
                iter_duration = time.monotonic() - iter_start
                iteration = IterationResult(
                    iteration=i,
                    duration_seconds=iter_duration,
                    error=str(exc),
                )

            trial.iterations.append(iteration)

            if iteration.build_complete:
                trial.build_complete = True
                break

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
    """Run all trials: tasks x variants x num_trials with ThreadPoolExecutor."""
    run_id = config.id or str(uuid.uuid4())[:8]

    # Load all tasks and variants
    tasks = [load_task(evals_dir, tid) for tid in config.tasks]
    variants = [load_variant(evals_dir, vid) for vid in config.variants]

    # Build trial schedule
    schedule: list[dict[str, Any]] = []
    for task in tasks:
        for variant in variants:
            for n in range(config.num_trials):
                schedule.append({"task": task, "variant": variant, "trial_number": n})

    if dry_run:
        return EvalRun(
            config=config,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            summary={"dry_run": True, "total_trials": len(schedule), "schedule": [
                {"task": s["task"].id, "variant": s["variant"].id, "trial": s["trial_number"]}
                for s in schedule
            ]},
        )

    eval_run = EvalRun(
        config=config,
        started_at=datetime.now(UTC),
    )

    # Handle SIGINT: save partial results
    interrupted = False

    def _signal_handler(signum: int, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        logger.warning("SIGINT received — finishing current trials and saving partial results")

    original_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        with ThreadPoolExecutor(max_workers=config.max_concurrency) as pool:
            futures = {
                pool.submit(
                    run_trial,
                    s["task"],
                    s["variant"],
                    s["trial_number"],
                    evals_dir=evals_dir,
                    config=config,
                    run_id=run_id,
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
                        variant_id=s["variant"].id,
                        trial_number=s["trial_number"],
                        error=str(exc),
                    )

                eval_run.trials.append(trial)
                if progress_callback:
                    progress_callback(trial)
    finally:
        signal.signal(signal.SIGINT, original_handler)

    eval_run.finished_at = datetime.now(UTC)
    return eval_run
