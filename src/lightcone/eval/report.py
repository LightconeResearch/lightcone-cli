"""Aggregation, display, and persistence for eval results."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from lightcone.eval.models import EvalRun, TrialResult


def compute_summary(eval_run: EvalRun) -> dict[str, Any]:
    """Group trials by task and compute aggregate statistics."""
    groups: dict[str, list[TrialResult]] = defaultdict(list)
    for trial in eval_run.trials:
        groups[trial.task_id].append(trial)

    summary: dict[str, Any] = {"groups": {}, "totals": {}}

    all_costs: list[float] = []
    all_durations: list[float] = []

    for task_id, trials in groups.items():
        scores: list[float] = []
        costs: list[float] = []
        durations: list[float] = []
        completions = 0
        errors = 0

        for t in trials:
            if t.error is not None:
                errors += 1
                continue
            scores.append(t.composite_score)
            costs.append(t.total_cost_usd)
            durations.append(t.total_duration_seconds)
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

        summary["groups"][task_id] = {
            "task_id": task_id,
            "num_trials": len(trials),
            "num_errors": errors,
            "mean_score": round(mean_score, 4),
            "stderr_score": round(stderr_score, 4),
            "pass_at_k": completions / len(trials) if trials else 0.0,
            "mean_cost_usd": round(mean_cost, 4),
            "mean_duration_seconds": round(mean_duration, 1),
        }

        all_costs.extend(costs)
        all_durations.extend(durations)

    summary["totals"] = {
        "total_trials": len(eval_run.trials),
        "total_cost_usd": round(sum(all_costs), 4),
        "total_duration_seconds": round(sum(all_durations), 1),
    }

    return summary


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
    return f"${cost:.2f}\n{dur:.0f}s"


def print_comparison_table(
    eval_run: EvalRun,
    console: Console | None = None,
) -> None:
    """Print a Rich summary table of eval results."""
    if console is None:
        console = Console()

    summary = eval_run.summary or compute_summary(eval_run)
    groups = summary.get("groups", {})

    if not groups:
        console.print("[yellow]No results to display.[/yellow]")
        return

    # Scores table
    score_table = Table(title="Eval Results: Scores", show_lines=True)
    score_table.add_column("Task", style="bold")
    score_table.add_column("Score", justify="center")
    for task_id, g in groups.items():
        score_table.add_row(task_id, _score_cell(g))
    console.print(score_table)

    # Cost table
    console.print()
    cost_table = Table(title="Eval Results: Cost & Duration", show_lines=True)
    cost_table.add_column("Task", style="bold")
    cost_table.add_column("Cost / Duration", justify="center")
    for task_id, g in groups.items():
        cost_table.add_row(task_id, _cost_cell(g))
    console.print(cost_table)

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
    table.add_column("Task", style="bold")
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
    """Save full EvalRun to JSON inside the run's sidecar directory."""
    output_dir = Path(output_dir)

    if eval_run.run_stem:
        run_dir = output_dir / eval_run.run_stem
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_id = eval_run.config.id or "eval"
        run_dir = output_dir / f"{run_id}-{timestamp}"

    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "results.json"
    data = eval_run.model_dump(mode="json")
    output_path.write_text(json.dumps(data, indent=2, default=str))

    return output_path


def load_results(path: Path) -> EvalRun:
    """Load an EvalRun from a JSON file."""
    data = json.loads(path.read_text())
    return EvalRun.model_validate(data)
