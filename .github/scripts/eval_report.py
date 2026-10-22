"""Report on a Harbor eval run: the gate, the trace digests, the PR comment.

    eval_report.py summarize --jobs collected/ --out report/ --task bundle/task
                             [--baseline base/] [--refs refs.json]
                             [--changes changes.md] [--oracle success]
                             [--mode main|pr]
    eval_report.py comment --out report/

``summarize`` reads one directory per matrix leg (``leg.json``, plus
Harbor's job output unless the leg was skipped) and writes ``results.json``,
``history.json`` (main runs only), and per leg a trace digest and the input
the trace analyst is handed. ``comment`` assembles ``comment.md`` from those
and each ``analysis-<leg>.md`` the analyst wrote.

Steps are counted as lightcone-bench counts them (inspired from its
``trajectory_metrics.py``): trajectory steps whose source is the agent.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The step gate: off main, a leg fails when its mean steps exceed the median of
# its last main runs by more than TOLERANCE *and* by at least MIN_SLACK steps,
# once at least MIN_HISTORY main runs exist. One trial per leg is noisy.
TOLERANCE = 0.25
MIN_SLACK = 3
MIN_HISTORY = 3
HISTORY_LENGTH = 10
SNIPPET = 800
DIGEST_CAP = 120_000


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _text(value: Any) -> str:
    """Flatten an ATIF content value (string, parts list, or dict) to text."""
    if value is None or isinstance(value, str):
        return value or ""
    if isinstance(value, list):
        return "\n".join(_text(part) for part in value)
    if isinstance(value, dict):
        for key in ("text", "content", "output"):
            if key in value:
                return _text(value[key])
    return json.dumps(value)


def _snip(text: str, limit: int = SNIPPET) -> str:
    text = text.strip().replace("```", "`​``")
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def read_trial(trial_dir: Path) -> dict[str, Any]:
    """Reward, exception, steps and a chronological digest for one trial."""
    result = _load(trial_dir / "result.json") or {}
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    exc = result.get("exception_info")
    timing = result.get("agent_execution") or {}
    try:
        start, end = (datetime.fromisoformat(timing[k]) for k in ("started_at", "finished_at"))
        minutes: float | None = (end - start).total_seconds() / 60
    except (KeyError, TypeError, ValueError):
        minutes = None

    steps = [s for s in (_load(trial_dir / "agent" / "trajectory.json") or {}).get("steps", [])]
    digest = [f"## Trial `{result.get('trial_name', trial_dir.name)}`"]
    skills_used = False
    for step in steps:
        if step.get("source") == "user":
            continue
        obs = step.get("observation")
        if isinstance(obs, str):
            try:
                obs = json.loads(obs)
            except ValueError:
                obs = {"results": [{"content": obs}]}
        results = {
            r.get("source_call_id", ""): _text(r.get("content"))
            for r in (obs or {}).get("results", [])
        }
        head = _text(step.get("message")) or _text(step.get("reasoning_content"))
        digest.append(f"**step {step.get('step_id', '?')}** {_snip(head, 300)}".rstrip())
        for call in step.get("tool_calls") or []:
            name = str(call.get("function_name", "?"))
            args = call.get("arguments")
            text = json.dumps(args) if not isinstance(args, str) else args
            skills_used |= name.lower() == "skill" or "SKILL.md" in text
            # An observation with no call ids belongs to the step's first call
            out = results.pop(call.get("tool_call_id", ""), None) or results.pop("", "")
            digest.append(f"- `{name}` {_snip(text, 300)}")
            if out:
                digest.append("  ```\n  " + _snip(out).replace("\n", "\n  ") + "\n  ```")

    verifier = trial_dir / "verifier" / "test-stdout.txt"
    return {
        "reward": rewards.get("reward"),
        "exception": f"{exc.get('exception_type')}: {_snip(str(exc.get('exception_message')), 500)}"
        if isinstance(exc, dict)
        else None,
        "steps": sum(1 for s in steps if s.get("source") == "agent") if steps else None,
        "minutes": minutes,
        "skills_used": skills_used,
        "digest": "\n".join(digest),
        "verifier": _snip(verifier.read_text(errors="replace"), 3000) if verifier.is_file() else "",
    }


def _mean(values: list[Any]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return statistics.mean(nums) if nums else None


def read_leg(leg_dir: Path) -> dict[str, Any]:
    """Every trial of one matrix leg, summarized."""
    meta = _load(leg_dir / "leg.json") or {}
    trials = [
        read_trial(p.parent)
        for p in sorted(leg_dir.rglob("result.json"))
        if (_load(p) or {}).get("trial_name")
    ]
    return {
        **meta,
        "leg": meta.get("leg", leg_dir.name),
        "trials": trials,
        "n": len(trials),
        "errored": sum(1 for t in trials if t["exception"]),
        "reward": _mean([t["reward"] for t in trials]),
        "steps": _mean([t["steps"] for t in trials]),
        "minutes": _mean([t["minutes"] for t in trials]),
        "skills_used": any(t["skills_used"] for t in trials),
    }


def gate(
    legs: list[dict[str, Any]], history: dict[str, Any], oracle: str, mode: str
) -> dict[str, Any]:
    """Reward and breakage always; steps against main's median off main."""
    reasons, notes = [], []
    if oracle != "success":
        reasons.append(f"reference solution: {oracle} — the task or the stack is broken")
    ran = [leg for leg in legs if not leg.get("skipped")]
    if oracle == "success" and not ran:
        reasons.append("no agent leg ran (every leg was skipped)")
    for leg in ran:
        name = leg["leg"]
        if not leg["n"]:
            reasons.append(f"{name}: no trial results")
            continue
        if leg["errored"]:
            reasons.append(f"{name}: {leg['errored']} trial(s) errored")
        if leg["reward"] is None or leg["reward"] < 1.0:
            reasons.append(f"{name}: mean reward {leg['reward']} < 1.0")
        past = [h["steps"] for h in history.get(name, [])]
        leg["history"] = past
        leg["median"] = statistics.median(past) if past else None
        if len(past) < MIN_HISTORY:
            notes.append(f"{name}: {len(past)} main run(s) on record, steps not gated yet")
        elif leg["steps"] is None:
            reasons.append(f"{name}: no trajectory, steps cannot be compared")
        elif (
            mode != "main"
            and leg["steps"] > leg["median"] * (1 + TOLERANCE)
            and leg["steps"] - leg["median"] >= MIN_SLACK
        ):
            reasons.append(
                f"{name}: {leg['steps']:.0f} steps against main's median {leg['median']:.0f}"
            )
    return {"ok": not reasons, "reasons": reasons, "notes": notes}


def _fmt(value: Any, spec: str = "{:.0f}") -> str:
    return "—" if value is None else spec.format(value)


def summarize(args: argparse.Namespace) -> int:
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    base: Path | None = args.baseline if args.baseline and args.baseline.is_dir() else None
    history = (_load(base / "history.json") if base else None) or {}
    legs = [read_leg(d) for d in sorted(p for p in args.jobs.iterdir() if p.is_dir())]
    verdict = gate(legs, history, args.oracle, args.mode)
    instruction = (args.task / "instruction.md").read_text()
    changes = args.changes.read_text() if args.changes and args.changes.is_file() else ""

    for leg in legs:
        name = leg["leg"]
        if leg.get("skipped") or not leg["n"]:
            continue
        digest = "\n\n".join(t.pop("digest") for t in leg["trials"])[:DIGEST_CAP]
        (out / f"digest-{name}.md").write_text(digest)
        base_digest = base / f"digest-{name}.md" if base else None
        facts = [
            f"Reward {_fmt(leg['reward'], '{:.2f}')}, {_fmt(leg['steps'])} agent steps "
            f"(main's median: {_fmt(leg.get('median'))}), {leg['n']} trial(s).",
            *(f"Harness exception: {t['exception']}" for t in leg["trials"] if t["exception"]),
            *(
                f"Verifier output:\n```\n{t['verifier']}\n```"
                for t in leg["trials"]
                if t["verifier"]
            ),
        ]
        (out / f"analysis-input-{name}.md").write_text(
            "\n\n".join(
                [
                    "# Task instruction",
                    instruction,
                    "# Run summary",
                    *facts,
                    "# Change under test",
                    changes or "_not provided_",
                    "# This run's trace",
                    digest,
                    "# Baseline trace (latest successful main run)",
                    base_digest.read_text() if base_digest and base_digest.is_file() else "_none_",
                ]
            )
            + "\n"
        )
        for t in leg["trials"]:
            t.pop("verifier")

    refs = (_load(args.refs) if args.refs else None) or {}
    (out / "results.json").write_text(
        json.dumps({"refs": refs, "legs": legs, "gate": verdict}, indent=1) + "\n"
    )
    if args.mode == "main":
        for leg in legs:
            if leg["steps"] is not None:
                entry = {
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "sha": refs.get("sha", ""),
                    "steps": leg["steps"],
                }
                history[leg["leg"]] = (history.get(leg["leg"], []) + [entry])[-HISTORY_LENGTH:]
        (out / "history.json").write_text(json.dumps(history, indent=1) + "\n")
    print("gate:", "ok" if verdict["ok"] else "; ".join(verdict["reasons"]))
    return 0


def comment(args: argparse.Namespace) -> int:
    out: Path = args.out
    results = _load(out / "results.json") or {}
    verdict = results.get("gate", {})
    lines = [
        f"## {'✅' if verdict.get('ok') else '❌'} Eval",
        "",
        "<!-- lightcone-eval-report -->",
        "",
        " · ".join(f"{k}: `{v}`" for k, v in results.get("refs", {}).items() if v),
        "",
        "| Leg | Reward | Agent steps | main median | Agent min | Skills loaded |",
        "|---|---|---|---|---|---|",
    ]
    for leg in results.get("legs", []):
        if leg.get("skipped"):
            lines.append(f"| {leg['leg']} | skipped: {leg.get('skip_reason', '')} | | | | |")
            continue
        lines.append(
            f"| {leg['leg']} | {_fmt(leg['reward'], '{:.2f}')} | {_fmt(leg['steps'])} "
            f"| {_fmt(leg.get('median'))} | {_fmt(leg['minutes'], '{:.1f}')} "
            f"| {'yes' if leg['skills_used'] else '**no**'} |"
        )
    lines += ["", *(f"- ❌ {r}" for r in verdict.get("reasons", []))]
    lines += [*(f"- ℹ️ {n}" for n in verdict.get("notes", [])), ""]
    for leg in results.get("legs", []):
        if leg.get("skipped") or not leg["n"]:
            continue
        analysis = out / f"analysis-{leg['leg']}.md"
        text = analysis.read_text().strip() if analysis.is_file() else ""
        lines += [f"### {leg['leg']}", "", text or "❌ _Trace analysis missing._", ""]
        if leg.get("history"):
            lines += ["Steps on main, oldest first: " + " → ".join(map(_fmt, leg["history"])), ""]
    (out / "comment.md").write_text("\n".join(lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("summarize")
    s.add_argument("--jobs", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--task", type=Path, required=True)
    s.add_argument("--baseline", type=Path)
    s.add_argument("--refs", type=Path)
    s.add_argument("--changes", type=Path)
    s.add_argument("--oracle", default="success")
    s.add_argument("--mode", choices=["main", "pr"], default="pr")
    sub.add_parser("comment").add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return summarize(args) if args.command == "summarize" else comment(args)


if __name__ == "__main__":
    sys.exit(main())
