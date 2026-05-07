"""Grader dispatch for eval trials."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from lightcone.eval.models import GraderResult, GraderSpec, GraderType

if TYPE_CHECKING:
    from lightcone.eval.sandbox import EvalSandbox

logger = logging.getLogger(__name__)


def _error_result(grader: GraderSpec, error: str, **kwargs: object) -> GraderResult:
    """Create a failed GraderResult with score 0."""
    return GraderResult(
        name=grader.name,
        type=grader.type,
        passed=False,
        score=0.0,
        weight=grader.weight,
        error=error,
        **kwargs,  # type: ignore[arg-type]
    )


def run_graders(
    sandbox: EvalSandbox,
    graders: list[GraderSpec],
    evals_dir: Path,
    task_id: str,
) -> list[GraderResult]:
    """Run all graders for a trial and return results."""
    results: list[GraderResult] = []
    for grader in graders:
        try:
            result = _run_single_grader(sandbox, grader, evals_dir, task_id)
        except Exception as exc:
            result = _error_result(grader, str(exc))
        results.append(result)
    return results


def _run_single_grader(
    sandbox: EvalSandbox,
    grader: GraderSpec,
    evals_dir: Path,
    task_id: str,
) -> GraderResult:
    """Run a single grader and return its result."""
    if grader.type == GraderType.command:
        return _grade_command(sandbox, grader)
    elif grader.type == GraderType.status:
        return _grade_status(sandbox, grader)
    elif grader.type == GraderType.script:
        return _grade_script(sandbox, grader, evals_dir, task_id)
    else:
        return _error_result(grader, f"Unknown grader type: {grader.type}")


def _grade_command(sandbox: EvalSandbox, grader: GraderSpec) -> GraderResult:
    """Run a shell command; exit 0 = pass."""
    if grader.command is None:
        raise ValueError("command grader requires 'command' field")

    cmd = f"cd {sandbox.WORK_DIR} && {grader.command}"
    result = sandbox.exec(cmd, timeout=grader.timeout)
    passed = result.exit_code == 0
    return GraderResult(
        name=grader.name,
        type=grader.type,
        passed=passed,
        score=1.0 if passed else 0.0,
        weight=grader.weight,
        details=result.output[:2000] if result.output else "",
    )


def _grade_status(sandbox: EvalSandbox, grader: GraderSpec) -> GraderResult:
    """Score the fraction of declared outputs that are materialized.

    Uses ``lc status --json`` so the grader is decoupled from any styling
    or human-readable layout in the CLI's text output. Aliases (outputs
    without their own recipe) are excluded from the denominator — they
    are not independently materializable.
    """
    import json as _json

    cmd = f"cd {sandbox.WORK_DIR} && lc status --json"
    result = sandbox.exec(cmd, timeout=grader.timeout)

    if result.exit_code != 0:
        return _error_result(
            grader,
            f"lc status --json exited with code {result.exit_code}",
            details=result.output[:2000],
        )

    try:
        payload = _json.loads(result.output)
    except _json.JSONDecodeError as exc:
        return _error_result(
            grader,
            f"lc status --json produced invalid JSON: {exc}",
            details=result.output[:2000],
        )

    materialized = 0
    total = 0
    for u in payload.get("universes", []):
        for o in u.get("outputs", []):
            status = o.get("status")
            if status == "alias":
                continue
            total += 1
            if status == "ok":
                materialized += 1

    if total == 0:
        return GraderResult(
            name=grader.name,
            type=grader.type,
            passed=False,
            score=0.0,
            weight=grader.weight,
            details="No materializable outputs declared",
        )

    score = materialized / total
    return GraderResult(
        name=grader.name,
        type=grader.type,
        passed=score == 1.0,
        score=score,
        weight=grader.weight,
        details=f"{materialized}/{total} outputs materialized",
    )



def _grade_script(
    sandbox: EvalSandbox,
    grader: GraderSpec,
    evals_dir: Path,
    task_id: str,
) -> GraderResult:
    """Upload and run a custom grading script; exit 0 = pass, last stdout line as score."""
    if grader.script is None:
        raise ValueError("script grader requires 'script' field")

    # Look for script in task graders dir
    script_path = evals_dir / "tasks" / task_id / "graders" / grader.script
    if not script_path.exists():
        return _error_result(grader, f"Grader script not found: {script_path}")

    remote_script = f"/tmp/grader_{grader.name}.py"
    sandbox.upload_file(remote_script, script_path.read_bytes())

    result = sandbox.exec(
        f"cd {sandbox.WORK_DIR} && python {remote_script}",
        timeout=grader.timeout,
    )

    passed = result.exit_code == 0
    score = 1.0 if passed else 0.0

    # Try to parse last stdout line as a float score
    if result.output:
        lines = result.output.strip().splitlines()
        if lines:
            try:
                parsed = float(lines[-1].strip())
                if 0.0 <= parsed <= 1.0:
                    score = parsed
                    passed = score > 0.0
            except ValueError:
                pass

    return GraderResult(
        name=grader.name,
        type=grader.type,
        passed=passed,
        score=score,
        weight=grader.weight,
        details=result.output[:2000] if result.output else "",
    )


def compute_composite_score(results: list[GraderResult]) -> float:
    """Compute weighted mean of grader scores."""
    total_weight = sum(r.weight for r in results)
    if total_weight == 0:
        return 0.0
    return sum(r.score * r.weight for r in results) / total_weight
