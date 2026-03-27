"""Aggregation, display, and persistence for eval results."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from prism.eval.models import EvalRun, TrialResult


def compute_summary(eval_run: EvalRun) -> dict[str, Any]:
    """Group trials by (task, variant) and compute aggregate statistics."""
    groups: dict[tuple[str, str], list[TrialResult]] = defaultdict(list)
    for trial in eval_run.trials:
        groups[(trial.task_id, trial.variant_id)].append(trial)

    summary: dict[str, Any] = {"groups": {}, "totals": {}}

    all_costs: list[float] = []
    all_durations: list[float] = []

    for (task_id, variant_id), trials in groups.items():
        # Single pass over trials to collect all stats
        scores: list[float] = []
        costs: list[float] = []
        durations: list[float] = []
        iterations: list[int] = []
        completions = 0
        errors = 0

        for t in trials:
            if t.error is not None:
                errors += 1
                continue
            scores.append(t.composite_score)
            costs.append(t.total_cost_usd)
            durations.append(t.total_duration_seconds)
            iterations.append(len(t.iterations))
            if t.build_complete:
                completions += 1

        n = len(scores)
        mean_score = sum(scores) / n if n > 0 else 0.0
        stderr_score = (
            math.sqrt(sum((s - mean_score) ** 2 for s in scores) / (n - 1)) / math.sqrt(n)
            if n > 1
            else 0.0
        )
        mean_cost = sum(costs) / n if n > 0 else 0.0
        mean_duration = sum(durations) / n if n > 0 else 0.0
        mean_iterations = sum(iterations) / n if n > 0 else 0.0

        key = f"{task_id}/{variant_id}"
        summary["groups"][key] = {
            "task_id": task_id,
            "variant_id": variant_id,
            "num_trials": len(trials),
            "num_errors": errors,
            "mean_score": round(mean_score, 4),
            "stderr_score": round(stderr_score, 4),
            "pass_at_k": completions / len(trials) if trials else 0.0,
            "mean_cost_usd": round(mean_cost, 4),
            "mean_duration_seconds": round(mean_duration, 1),
            "mean_iterations": round(mean_iterations, 1),
        }

        all_costs.extend(costs)
        all_durations.extend(durations)

    summary["totals"] = {
        "total_trials": len(eval_run.trials),
        "total_cost_usd": round(sum(all_costs), 4),
        "total_duration_seconds": round(sum(all_durations), 1),
    }

    return summary


def _build_grid_table(
    title: str,
    task_ids: list[str],
    variant_ids: list[str],
    groups: dict[str, Any],
    cell_fn: Callable[[dict[str, Any]], str],
) -> Table:
    """Build a Rich table with tasks as rows and variants as columns."""
    table = Table(title=title, show_lines=True)
    table.add_column("Task", style="bold")
    for vid in variant_ids:
        table.add_column(vid, justify="center")

    for tid in task_ids:
        row: list[str] = [tid]
        for vid in variant_ids:
            g = groups.get(f"{tid}/{vid}")
            row.append(cell_fn(g) if g is not None else "-")
        table.add_row(*row)

    return table


def _score_cell(g: dict[str, Any]) -> str:
    """Format a score cell with color coding."""
    score = g["mean_score"]
    stderr = g["stderr_score"]
    completion = g["pass_at_k"]

    if score >= 0.8:
        color = "green"
    elif score >= 0.5:
        color = "yellow"
    else:
        color = "red"

    cell = f"[{color}]{score:.2f}[/{color}] +/- {stderr:.2f}\npass@k: {completion:.0%}"
    if g["num_errors"] > 0:
        cell += f"\n[red]{g['num_errors']} errors[/red]"
    return cell


def _cost_cell(g: dict[str, Any]) -> str:
    """Format a cost/duration cell."""
    cost = g["mean_cost_usd"]
    dur = g["mean_duration_seconds"]
    iters = g["mean_iterations"]
    return f"${cost:.2f}\n{dur:.0f}s\n{iters:.1f} iters"


def print_comparison_table(
    eval_run: EvalRun,
    console: Console | None = None,
) -> None:
    """Print a Rich comparison table of eval results."""
    if console is None:
        console = Console()

    summary = eval_run.summary or compute_summary(eval_run)
    groups = summary.get("groups", {})

    if not groups:
        console.print("[yellow]No results to display.[/yellow]")
        return

    # Collect unique tasks and variants (preserving insertion order)
    task_ids: list[str] = []
    variant_ids: list[str] = []
    for g in groups.values():
        if g["task_id"] not in task_ids:
            task_ids.append(g["task_id"])
        if g["variant_id"] not in variant_ids:
            variant_ids.append(g["variant_id"])

    console.print(
        _build_grid_table("Eval Results: Scores", task_ids, variant_ids, groups, _score_cell)
    )
    console.print()
    console.print(_build_grid_table(
        "Eval Results: Cost & Duration", task_ids, variant_ids, groups, _cost_cell,
    ))

    # Totals
    totals = summary.get("totals", {})
    if totals:
        console.print(
            f"\n[bold]Total:[/bold] {totals.get('total_trials', 0)} trials, "
            f"${totals.get('total_cost_usd', 0):.2f}, "
            f"{totals.get('total_duration_seconds', 0):.0f}s"
        )


def print_comparison_between(
    run1: EvalRun,
    run2: EvalRun,
    console: Console | None = None,
) -> None:
    """Print a comparison between two eval runs."""
    if console is None:
        console = Console()

    s1 = run1.summary or compute_summary(run1)
    s2 = run2.summary or compute_summary(run2)

    g1 = s1.get("groups", {})
    g2 = s2.get("groups", {})

    all_keys = sorted(set(g1.keys()) | set(g2.keys()))
    if not all_keys:
        console.print("[yellow]No results to compare.[/yellow]")
        return

    table = Table(title="Eval Comparison", show_lines=True)
    table.add_column("Task/Variant", style="bold")
    table.add_column("Run 1 Score", justify="center")
    table.add_column("Run 2 Score", justify="center")
    table.add_column("Delta", justify="center")

    for key in all_keys:
        r1 = g1.get(key)
        r2 = g2.get(key)

        s1_score = f"{r1['mean_score']:.2f} +/- {r1['stderr_score']:.2f}" if r1 else "-"
        s2_score = f"{r2['mean_score']:.2f} +/- {r2['stderr_score']:.2f}" if r2 else "-"

        if r1 and r2:
            delta = r2["mean_score"] - r1["mean_score"]
            color = "green" if delta > 0 else ("red" if delta < 0 else "white")
            delta_str = f"[{color}]{delta:+.2f}[/{color}]"
        else:
            delta_str = "-"

        table.add_row(key, s1_score, s2_score, delta_str)

    console.print(table)


def save_results(eval_run: EvalRun, output_dir: str | Path) -> Path:
    """Save full EvalRun to JSON."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if eval_run.run_stem:
        filename = f"{eval_run.run_stem}.json"
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_id = eval_run.config.id or "eval"
        filename = f"{run_id}-{timestamp}.json"

    output_path = output_dir / filename
    data = eval_run.model_dump(mode="json")
    output_path.write_text(json.dumps(data, indent=2, default=str))

    return output_path


def load_results(path: Path) -> EvalRun:
    """Load an EvalRun from a JSON file."""
    data = json.loads(path.read_text())
    return EvalRun.model_validate(data)


def load_transcripts(
    results_path: Path,
    eval_run: EvalRun | None = None,
) -> dict[str, dict[int, str]]:
    """Load JSONL transcripts for a saved eval run.

    Returns a dict mapping trial_id -> {iteration_number -> JSONL content}.
    Discovers sidecar files via transcript_path fields on iterations,
    falling back to the convention ``{results_stem}/logs/{trial_id}/``.
    """
    if eval_run is None:
        eval_run = load_results(results_path)
    sidecar_base = results_path.parent / results_path.stem / "logs"

    transcripts: dict[str, dict[int, str]] = {}
    for trial in eval_run.trials:
        trial_transcripts: dict[int, str] = {}
        for iteration in trial.iterations:
            if iteration.transcript_path:
                full_path = results_path.parent / iteration.transcript_path
            else:
                full_path = sidecar_base / trial.trial_id / "transcript.jsonl"

            if full_path.exists():
                trial_transcripts[iteration.iteration] = full_path.read_text()

        if trial_transcripts:
            transcripts[trial.trial_id] = trial_transcripts

    return transcripts
