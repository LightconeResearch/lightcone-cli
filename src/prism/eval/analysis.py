"""LLM-based post-processing analysis of eval transcripts."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prism.eval.models import (
    EvalAnalysis,
    EvalRun,
    IterationAnalysis,
    TokenUsage,
    TrialAnalysis,
    TrialResult,
)

logger = logging.getLogger(__name__)

DEFAULT_ANALYSIS_MODEL = "claude-sonnet-4-20250514"

DEFAULT_ANALYSIS_PROMPT = """\
You are analyzing a transcript from an automated coding agent's session.
The transcript is in JSONL format where each line is a JSON event from the
agent's interaction with Claude Code.

Analyze this transcript and identify:

1. **Pain points**: Where did the agent struggle, get confused, or encounter friction?
2. **Failure modes**: What specific errors, misunderstandings, or incorrect approaches occurred?
3. **Wasted loops**: Where did the agent repeat itself, go in circles, or do unnecessary work?
4. **Key decisions**: What were the critical decision points that determined success or failure?

Respond with a JSON object in this exact format (no markdown fences):
{
    "pain_points": ["description1", "description2"],
    "failure_modes": ["description1", "description2"],
    "wasted_loops": ["description1", "description2"],
    "key_decisions": ["description1", "description2"],
    "summary": "A 2-3 sentence overall summary of this session"
}
"""

DEFAULT_AGGREGATION_PROMPT = """\
You are analyzing summaries from multiple automated coding agent trials.
Each trial attempted the same or related tasks. Below are the per-trial
analysis summaries.

Identify:
1. **Common patterns**: What patterns appear across multiple trials?
2. **Common failure modes**: What failures recur?
3. **Recommendations**: What specific improvements would help the agent succeed?

Respond with a JSON object (no markdown fences):
{
    "common_patterns": ["pattern1", "pattern2"],
    "common_failure_modes": ["mode1", "mode2"],
    "recommendations": ["rec1", "rec2"]
}
"""


def load_analysis_prompt(prompt_path: Path | None) -> str:
    """Load a custom analysis prompt or return the default."""
    if prompt_path is not None and prompt_path.exists():
        return prompt_path.read_text()
    return DEFAULT_ANALYSIS_PROMPT


# Per-million-token pricing: (input, output, cache_write, cache_read)
MODEL_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-20250514": (15.0, 75.0, 18.75, 1.50),
    "claude-opus-4-6-20250610": (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-4-20250514": (3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4-6-20250610": (3.0, 15.0, 3.75, 0.30),
    "claude-haiku-3-5-20241022": (0.80, 4.0, 1.0, 0.08),
}
_DEFAULT_PRICING = (3.0, 15.0, 3.75, 0.30)


def _extract_usage(response_usage: Any) -> TokenUsage:
    """Extract raw token counts from an API response usage object."""
    return TokenUsage(
        input_tokens=getattr(response_usage, "input_tokens", 0) or 0,
        output_tokens=getattr(response_usage, "output_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(response_usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(response_usage, "cache_read_input_tokens", 0) or 0,
    )


def estimate_cost(usage: TokenUsage, model: str) -> float:
    """Estimate USD cost from token counts and model pricing.

    Pricing is hardcoded and may go stale — this is a display-time estimate,
    not a billing source. Raw token counts in TokenUsage are the source of truth.
    """
    input_price, output_price, cache_write_price, cache_read_price = MODEL_PRICING.get(
        model, _DEFAULT_PRICING
    )
    return (
        usage.input_tokens * input_price
        + usage.output_tokens * output_price
        + usage.cache_creation_input_tokens * cache_write_price
        + usage.cache_read_input_tokens * cache_read_price
    ) / 1_000_000


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM response text, handling markdown fences."""
    try:
        return json.loads(text.strip())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError):
        pass

    for fence in ("```json", "```"):
        if fence in text:
            parts = text.split(fence, 1)
            if len(parts) > 1:
                json_str = parts[1].split("```", 1)[0]
                try:
                    return json.loads(json_str.strip())  # type: ignore[no-any-return]
                except (json.JSONDecodeError, ValueError):
                    pass

    raise ValueError(f"Could not extract JSON from response: {text[:200]}")


def analyze_transcript(
    client: Any,
    transcript_jsonl: str,
    prompt: str,
    model: str = DEFAULT_ANALYSIS_MODEL,
) -> tuple[IterationAnalysis, TokenUsage]:
    """Analyze a single iteration transcript via the Anthropic API.

    Returns (analysis, usage).
    """
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": f"{prompt}\n\n---\nTRANSCRIPT:\n{transcript_jsonl}",
        }],
    )

    usage = _extract_usage(response.usage)

    text = response.content[0].text
    try:
        data = _extract_json(text)
        return IterationAnalysis(
            iteration=0,
            pain_points=data.get("pain_points", []),
            failure_modes=data.get("failure_modes", []),
            wasted_loops=data.get("wasted_loops", []),
            key_decisions=data.get("key_decisions", []),
            summary=data.get("summary", ""),
        ), usage
    except (ValueError, KeyError) as exc:
        logger.warning("Failed to parse analysis response: %s", exc)
        return IterationAnalysis(iteration=0, summary=text[:500]), usage


def analyze_trial(
    client: Any,
    trial: TrialResult,
    transcripts: dict[int, str],
    prompt: str,
    model: str = DEFAULT_ANALYSIS_MODEL,
) -> TrialAnalysis:
    """Analyze all iteration transcripts for a single trial."""
    analysis = TrialAnalysis(
        trial_id=trial.trial_id,
        task_id=trial.task_id,
        variant_id=trial.variant_id,
    )

    total_usage = TokenUsage()
    for iteration_num in sorted(transcripts.keys()):
        transcript = transcripts[iteration_num]
        if not transcript.strip():
            continue

        iter_analysis, usage = analyze_transcript(client, transcript, prompt, model=model)
        iter_analysis.iteration = iteration_num
        analysis.iterations.append(iter_analysis)
        total_usage = total_usage + usage

    all_failures = [f for ia in analysis.iterations for f in ia.failure_modes]
    if all_failures:
        analysis.primary_failure_mode = all_failures[0]

    summaries = [ia.summary for ia in analysis.iterations if ia.summary]
    analysis.overall_summary = " | ".join(summaries) if summaries else ""
    analysis.usage = total_usage

    return analysis


def aggregate_analyses(
    client: Any,
    trial_analyses: list[TrialAnalysis],
    model: str = DEFAULT_ANALYSIS_MODEL,
) -> tuple[list[str], list[str], list[str], TokenUsage]:
    """Aggregate per-trial analyses into cross-trial patterns.

    Returns (common_patterns, common_failure_modes, recommendations, usage).
    """
    trial_summaries = []
    for ta in trial_analyses:
        trial_summaries.append({
            "trial_id": ta.trial_id,
            "task_id": ta.task_id,
            "variant_id": ta.variant_id,
            "overall_summary": ta.overall_summary,
            "primary_failure_mode": ta.primary_failure_mode,
            "all_pain_points": [p for ia in ta.iterations for p in ia.pain_points],
            "all_failure_modes": [f for ia in ta.iterations for f in ia.failure_modes],
        })

    content = (
        f"{DEFAULT_AGGREGATION_PROMPT}\n\n---\n"
        f"TRIAL ANALYSES:\n{json.dumps(trial_summaries, indent=2)}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
    )

    usage = _extract_usage(response.usage)

    text = response.content[0].text
    try:
        data = _extract_json(text)
        return (
            data.get("common_patterns", []),
            data.get("common_failure_modes", []),
            data.get("recommendations", []),
            usage,
        )
    except (ValueError, KeyError) as exc:
        logger.warning("Failed to parse aggregation response: %s", exc)
        return [], [], [text[:500]], usage


def run_analysis(
    eval_run: EvalRun,
    transcripts: dict[str, dict[int, str]],
    *,
    prompt_path: Path | None = None,
    model: str = DEFAULT_ANALYSIS_MODEL,
) -> EvalAnalysis:
    """Run full analysis: per-trial LLM analysis + cross-trial aggregation."""
    import anthropic

    client = anthropic.Anthropic()
    prompt = load_analysis_prompt(prompt_path)

    analysis = EvalAnalysis(
        run_config_id=eval_run.config.id,
        analyzed_at=datetime.now(UTC),
        model=model,
        prompt_file=str(prompt_path) if prompt_path else None,
    )

    total_usage = TokenUsage()
    for trial in eval_run.trials:
        trial_transcripts = transcripts.get(trial.trial_id, {})
        if not trial_transcripts:
            logger.info("No transcripts for trial %s — skipping", trial.trial_id)
            continue

        trial_analysis = analyze_trial(
            client, trial, trial_transcripts, prompt, model=model
        )
        analysis.trial_analyses.append(trial_analysis)
        total_usage = total_usage + trial_analysis.usage

    if len(analysis.trial_analyses) > 1:
        patterns, failures, recs, agg_usage = aggregate_analyses(
            client, analysis.trial_analyses, model=model
        )
        analysis.common_patterns = patterns
        analysis.common_failure_modes = failures
        analysis.recommendations = recs
        total_usage = total_usage + agg_usage
    elif len(analysis.trial_analyses) == 1:
        ta = analysis.trial_analyses[0]
        analysis.common_patterns = [ia.summary for ia in ta.iterations if ia.summary]
        analysis.common_failure_modes = [
            f for ia in ta.iterations for f in ia.failure_modes
        ]

    analysis.total_usage = total_usage
    return analysis


def save_analysis(analysis: EvalAnalysis, results_path: Path) -> Path:
    """Save analysis as a sidecar JSON alongside the run results.

    Naming: ``{stem}-analysis.json``, or ``{stem}-analysis-{prompt_name}.json``
    when a custom prompt was used.
    """
    stem = results_path.stem
    if analysis.prompt_file:
        prompt_stem = Path(analysis.prompt_file).stem
        filename = f"{stem}-analysis-{prompt_stem}.json"
    else:
        filename = f"{stem}-analysis.json"

    output_path = results_path.parent / filename
    data = analysis.model_dump(mode="json")
    output_path.write_text(json.dumps(data, indent=2, default=str))
    return output_path


def print_analysis_summary(analysis: EvalAnalysis, console: Any) -> None:
    """Display a Rich summary of the analysis."""
    from rich.panel import Panel
    from rich.table import Table

    if analysis.trial_analyses:
        table = Table(title="Trial Analysis Summary", show_lines=True)
        table.add_column("Trial", style="bold")
        table.add_column("Pain Points", justify="center")
        table.add_column("Failure Modes", justify="center")
        table.add_column("Wasted Loops", justify="center")
        table.add_column("Summary")

        for ta in analysis.trial_analyses:
            total_pp = sum(len(ia.pain_points) for ia in ta.iterations)
            total_fm = sum(len(ia.failure_modes) for ia in ta.iterations)
            total_wl = sum(len(ia.wasted_loops) for ia in ta.iterations)
            summary = ta.overall_summary
            if len(summary) > 120:
                summary = summary[:120] + "..."
            table.add_row(
                f"{ta.task_id}/{ta.variant_id}\n{ta.trial_id}",
                str(total_pp),
                str(total_fm),
                str(total_wl),
                summary,
            )

        console.print(table)
        console.print()

    if analysis.common_patterns:
        text = "\n".join(f"  - {p}" for p in analysis.common_patterns)
        console.print(Panel(text, title="Common Patterns"))

    if analysis.common_failure_modes:
        text = "\n".join(f"  - {f}" for f in analysis.common_failure_modes)
        console.print(Panel(text, title="Common Failure Modes", border_style="red"))

    if analysis.recommendations:
        text = "\n".join(f"  - {r}" for r in analysis.recommendations)
        console.print(Panel(text, title="Recommendations", border_style="green"))

    u = analysis.total_usage
    total_tokens = (
        u.input_tokens + u.output_tokens
        + u.cache_creation_input_tokens + u.cache_read_input_tokens
    )
    model = analysis.model or DEFAULT_ANALYSIS_MODEL
    cost = estimate_cost(u, model)
    console.print(
        f"\n[bold]Analysis tokens:[/bold] {total_tokens:,} "
        f"({u.input_tokens:,} in, {u.output_tokens:,} out"
        f"{f', {u.cache_read_input_tokens:,} cache' if u.cache_read_input_tokens else ''})"
        f"  [bold]Est. cost:[/bold] ${cost:.4f} ({model})"
    )
