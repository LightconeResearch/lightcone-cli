"""CLI commands for the eval harness: prism eval {run, report, compare}."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

console = Console()


@click.group()
def eval_group() -> None:
    """Evaluate the Prism build loop against seed tasks."""
    import logging

    from dotenv import load_dotenv

    load_dotenv()

    # Configure logging so sandbox build logs and harness progress are visible
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


@eval_group.command("run")
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
@click.option("--concurrency", "-c", type=int, default=None, help="Max parallel sandboxes")
@click.option("--num-trials", "-n", type=int, default=None, help="Override number of trials")
@click.option("--dry-run", is_flag=True, help="Print trial schedule without running")
@click.option(
    "--evals-dir",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to evals/ directory (default: evals/ in current dir)",
)
def run_cmd(
    config_path: Path,
    concurrency: int | None,
    num_trials: int | None,
    dry_run: bool,
    evals_dir: Path | None,
) -> None:
    """Run an eval suite from a config file.

    Examples:
        prism eval run evals/example-run.yaml
        prism eval run evals/example-run.yaml --dry-run
        prism eval run evals/example-run.yaml --num-trials 1 --concurrency 2
    """
    from prism.eval.harness import load_run_config, run_eval
    from prism.eval.report import compute_summary, print_comparison_table, save_results

    config = load_run_config(config_path)

    # Apply CLI overrides
    if concurrency is not None:
        config.max_concurrency = concurrency
    if num_trials is not None:
        config.num_trials = num_trials

    if evals_dir is None:
        evals_dir = Path.cwd() / "evals"

    if not evals_dir.exists():
        console.print(f"[red]Error:[/red] Evals directory not found: {evals_dir}")
        raise SystemExit(1)

    if dry_run:
        console.print("[bold]Dry run — trial schedule:[/bold]\n")

    def _on_trial_complete(trial: object) -> None:
        from prism.eval.models import TrialResult

        assert isinstance(trial, TrialResult)
        if trial.build_complete:
            status = "[green]complete[/green]"
        else:
            status = "[yellow]incomplete[/yellow]"
        if trial.error:
            status = f"[red]error: {trial.error[:60]}[/red]"
        console.print(
            f"  {trial.task_id} "
            f"trial {trial.trial_number}: "
            f"score={trial.composite_score:.2f} {status}"
        )

    eval_run = run_eval(
        config,
        evals_dir,
        progress_callback=_on_trial_complete,
        dry_run=dry_run,
    )

    if dry_run:
        schedule = eval_run.summary.get("schedule", [])
        for s in schedule:
            console.print(f"  {s['task']} trial {s['trial']}")
        console.print(f"\n[bold]Total: {eval_run.summary.get('total_trials', 0)} trials[/bold]")
        return

    # Display version info
    v = eval_run.version
    dirty = " [yellow](dirty)[/yellow]" if v.prism_dirty else ""
    console.print(
        f"\n[bold]Prism:[/bold] {v.prism_version} "
        f"({v.prism_branch} {v.prism_commit[:8]}){dirty}"
    )
    if v.astra_version:
        console.print(f"[bold]ASTRA:[/bold] {v.astra_version}")

    # Compute summary and display
    eval_run.summary = compute_summary(eval_run)
    console.print()
    print_comparison_table(eval_run, console=console)

    # Save results
    output_path = save_results(eval_run, config.output_dir)
    console.print(f"\n[bold]Results saved to:[/bold] {output_path}")


@eval_group.command("report")
@click.argument("results_path", type=click.Path(exists=True, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON summary")
def report_cmd(results_path: Path, as_json: bool) -> None:
    """Display results from a previous eval run.

    Examples:
        prism eval report eval-results/my-run-20260315.json
        prism eval report eval-results/my-run-20260315.json --json
    """
    import json

    from prism.eval.report import compute_summary, load_results, print_comparison_table

    eval_run = load_results(results_path)

    if not eval_run.summary:
        eval_run.summary = compute_summary(eval_run)

    if as_json:
        console.print(json.dumps(eval_run.summary, indent=2, default=str))
    else:
        print_comparison_table(eval_run, console=console)


@eval_group.command("compare")
@click.argument("results1", type=click.Path(exists=True, path_type=Path))
@click.argument("results2", type=click.Path(exists=True, path_type=Path))
def compare_cmd(results1: Path, results2: Path) -> None:
    """Compare two eval runs side by side.

    Examples:
        prism eval compare eval-results/run1.json eval-results/run2.json
    """
    from prism.eval.report import (
        compute_summary,
        load_results,
        print_comparison_between,
    )

    run1 = load_results(results1)
    run2 = load_results(results2)

    if not run1.summary:
        run1.summary = compute_summary(run1)
    if not run2.summary:
        run2.summary = compute_summary(run2)

    print_comparison_between(run1, run2, console=console)


