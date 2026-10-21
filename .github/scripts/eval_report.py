"""Report on a Harbor eval run: metrics, evidence, the step gate, the PR comment.

    eval_report.py summarize --jobs collected/ --out report/ [--baseline base/]
                             [--refs refs.json] [--changes changes.md]
                             [--mode main|pr] [--tolerance 0.25]
    eval_report.py comment --out report/

``summarize`` reads every leg under ``--jobs`` (one directory per matrix leg,
holding ``leg.json`` and, unless the leg was skipped, Harbor's job output) and
writes into ``--out``:

- ``results.json``    per-leg rewards, steps, tool calls, phases, cost; the gate
- ``history.json``    the baseline's history plus this run, on ``--mode main``
- ``digest-<leg>.md`` the chronological trace of each trial (ATIF trajectory)
- ``evidence-<leg>.md`` failures, repeats and the phase table, deterministic
- ``analysis-input-<leg>.md`` everything the trace analyst is handed

``comment`` assembles ``comment.md`` from those plus each ``analysis-<leg>.md``
the analyst wrote. The gate's verdict is in ``results.json``; the workflow
fails the job on it once the comment is posted.

Inspired from LightconeResearch/lightcone-bench (``summarize.py``,
``trajectory_metrics.py``, ``compare.py``, ``analyze_traces.py``), which reads
the same Harbor artifacts. Agent steps are counted as the bench counts them:
trajectory steps whose source is the agent.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HISTORY_LENGTH = 10
SNIPPET = 300
ERROR_SNIPPET = 1500
DIGEST_CAP = 120_000
EVIDENCE_CAP = 40

# Harness tool name (lowercased) -> how the call is read. Anything unlisted is
# read by its arguments alone. Vocabularies differ per harness: claude-code
# `Bash`/`Read`/`Skill`, codex `exec_command`/`apply_patch`, opencode `bash`/`read`.
SHELL = {
    "bash",
    "shell",
    "exec",
    "exec_command",
    "local_shell",
    "run_command",
    "execute_command",
    "terminal",
    "container.exec",
}
READ = {
    "read",
    "read_file",
    "view",
    "glob",
    "grep",
    "ls",
    "list_dir",
    "list_files",
    "search_files",
    "open_file",
}

# Where a tool call's effort went, first match wins. Keyed on the call's
# command or path, so it reads the same under every harness.
PHASES = [
    ("skills", re.compile(r"SKILL\.md|/skills/")),
    ("help", re.compile(r"--help\b|\s-h\b|\bastra info\b|\blc --version\b")),
    ("compute", re.compile(r"\blc compute\b")),
    ("build", re.compile(r"\blc (materialize|run|status)\b")),
    ("spec", re.compile(r"astra\.yaml|universes/|\bastra\b")),
    ("publication", re.compile(r"license|ro-crate", re.I)),
    ("deps", re.compile(r"\buv (add|remove|lock|sync|pip)\b|pyproject\.toml|uv\.lock")),
    ("scripts", re.compile(r"scripts/|\.py\b")),
    ("git", re.compile(r"\bgit\b")),
]

FAILURE = re.compile(
    r"Traceback \(most recent call last\)|^Error:|^error:"
    r"|\bexit(?:ed with)? (?:code|status):? ?[1-9]|command not found|Permission denied"
    r"|EACCES|No such file or directory|sandbox"
    r"|is not (?:up to date|current)|refus",
    re.I | re.M,
)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _seconds(block: Any) -> float | None:
    if not isinstance(block, dict):
        return None
    try:
        start, end = (
            datetime.fromisoformat(str(block[k]).replace("Z", "+00:00"))
            for k in ("started_at", "finished_at")
        )
    except (KeyError, TypeError, ValueError):
        return None
    return (end - start).total_seconds()


def _text(value: Any) -> str:
    """Flatten an ATIF content value (string, parts list, or dict) to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(part) for part in value)
    if isinstance(value, dict):
        for key in ("text", "content", "output", "stdout"):
            if key in value:
                return _text(value[key])
    return json.dumps(value)


def _observations(step: dict[str, Any]) -> dict[str, str]:
    """Map each tool call id in a step to the text of its result."""
    obs = step.get("observation")
    if isinstance(obs, str):
        try:
            obs = json.loads(obs)
        except ValueError:
            return {"": obs}
    if not isinstance(obs, dict):
        return {}
    return {
        str(r.get("source_call_id", "")): _text(r.get("content"))
        for r in obs.get("results") or []
        if isinstance(r, dict)
    }


def _call_text(call: dict[str, Any]) -> str:
    """The command or path a tool call acted on, as one line."""
    args = call.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return args
    if not isinstance(args, dict):
        return ""
    parts = []
    for key in (
        "command",
        "cmd",
        "file_path",
        "filePath",
        "path",
        "pattern",
        "skill",
        "input",
        "patch",
    ):
        value = args.get(key)
        if isinstance(value, list):
            value = " ".join(map(str, value))
        if value:
            parts.append(str(value))
    return " ".join(parts) or json.dumps(args)


def _phase(name: str, text: str) -> str:
    if name == "skill":
        return "skills"
    for phase, pattern in PHASES:
        if pattern.search(text):
            return phase
    return "explore" if name in READ else "other"


def _snip(text: str, limit: int) -> str:
    text = text.strip().replace("```", "`​``")
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def read_trial(trial_dir: Path) -> dict[str, Any]:
    """Metrics, evidence and a digest for one Harbor trial directory."""
    result = _load(trial_dir / "result.json") or {}
    verifier = result.get("verifier_result") or {}
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    exc = result.get("exception_info")
    trial: dict[str, Any] = {
        "trial": result.get("trial_name", trial_dir.name),
        "rewards": rewards or {},
        "reward": (rewards or {}).get("reward"),
        "exception": (
            f"{exc.get('exception_type', '?')}: {_snip(str(exc.get('exception_message', '')), 500)}"
            if isinstance(exc, dict)
            else None
        ),
        "agent_min": (lambda s: s / 60 if s is not None else None)(
            _seconds(result.get("agent_execution"))
        ),
        "cost_usd": (result.get("agent_result") or {}).get("cost_usd"),
    }
    traj = _load(trial_dir / "agent" / "trajectory.json")
    steps = [s for s in (traj or {}).get("steps") or [] if isinstance(s, dict)]
    phases: Counter[str] = Counter()
    commands: Counter[str] = Counter()
    failures: list[str] = []
    digest = [f"## Trial `{trial['trial']}`\n"]
    for step in steps:
        if step.get("source") == "user":
            continue
        results = _observations(step)
        head = _text(step.get("message")) or _text(step.get("reasoning_content"))
        line = f"**step {step.get('step_id', '?')}**"
        digest.append(f"{line} {_snip(head, SNIPPET)}" if head else line)
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("function_name", "?"))
            text = _call_text(call)
            phases[_phase(name.lower(), text)] += 1
            if name.lower() in SHELL:
                commands[text.strip()] += 1
            # A step-level observation with no call ids belongs to its first call
            out = results.pop(str(call.get("tool_call_id", "")), None) or results.pop("", "")
            failed = bool(FAILURE.search(out))
            digest.append(f"- `{name}` {_snip(text, SNIPPET)}")
            if out:
                digest.append(
                    "  ```\n  "
                    + _snip(out, ERROR_SNIPPET if failed else SNIPPET).replace("\n", "\n  ")
                    + "\n  ```"
                )
            if failed:
                failures.append(
                    f"- `{trial['trial']}` step {step.get('step_id', '?')} `{name}` "
                    f"`{_snip(text, 200)}`\n  ```\n  "
                    + _snip(out, 600).replace("\n", "\n  ")
                    + "\n  ```"
                )
    totals = (traj or {}).get("final_metrics") or {}
    trial.update(
        {
            "has_trajectory": bool(steps),
            "steps": sum(1 for s in steps if s.get("source") == "agent") if steps else None,
            "tool_calls": sum(phases.values()) if steps else None,
            "phases": dict(phases),
            "skills_used": phases.get("skills", 0) > 0,
            "failures": len(failures),
            "repeats": {c: n for c, n in commands.items() if n > 1},
        }
    )
    if trial["cost_usd"] is None:
        trial["cost_usd"] = totals.get("total_cost_usd")
    trial["_failures"] = failures
    trial["_digest"] = "\n".join(digest)
    test_out = trial_dir / "verifier" / "test-stdout.txt"
    if test_out.is_file():
        trial["_verifier"] = _snip(test_out.read_text(errors="replace"), 3000)
    return trial


def _mean(values: list[Any]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return statistics.mean(nums) if nums else None


def read_leg(leg_dir: Path) -> dict[str, Any]:
    """Every trial of one matrix leg, plus the leg's own description."""
    meta = _load(leg_dir / "leg.json") or {}
    trials = [
        read_trial(p.parent)
        for p in sorted(leg_dir.rglob("result.json"))
        if (_load(p) or {}).get("trial_name")
    ]
    phases: Counter[str] = Counter()
    for t in trials:
        phases.update(t["phases"])
    return {
        "leg": meta.get("leg", leg_dir.name),
        "agent": meta.get("agent", "?"),
        "model": meta.get("model", "?"),
        "skipped": bool(meta.get("skipped")),
        "skip_reason": meta.get("skip_reason", ""),
        "trials": trials,
        "n": len(trials),
        "errored": sum(1 for t in trials if t["exception"]),
        "mean_reward": _mean([t["reward"] for t in trials]),
        "mean_steps": _mean([t["steps"] for t in trials]),
        "mean_tool_calls": _mean([t["tool_calls"] for t in trials]),
        "mean_cost_usd": _mean([t["cost_usd"] for t in trials]),
        "mean_agent_min": _mean([t["agent_min"] for t in trials]),
        "phases": {k: v / max(len(trials), 1) for k, v in phases.items()},
        "skills_used": any(t["skills_used"] for t in trials),
    }


def gate(
    legs: list[dict[str, Any]], history: dict[str, Any], mode: str, tolerance: float
) -> dict[str, Any]:
    """The verdict: reward and breakage always; steps against main's median off main."""
    reasons: list[str] = []
    notes: list[str] = []
    ran = [leg for leg in legs if not leg["skipped"]]
    if not ran:
        reasons.append("no agent leg ran (every leg was skipped)")
    for leg in ran:
        name = leg["leg"]
        if not leg["n"]:
            reasons.append(f"{name}: no trial results")
            continue
        if leg["errored"]:
            reasons.append(f"{name}: {leg['errored']} trial(s) errored")
        if leg["mean_reward"] is None or leg["mean_reward"] < 1.0:
            reasons.append(f"{name}: mean reward {leg['mean_reward']} < 1.0")
        past = [h["steps"] for h in history.get(name, []) if h.get("steps") is not None]
        leg["baseline_steps"] = statistics.median(past) if past else None
        leg["history"] = history.get(name, [])
        if leg["baseline_steps"] is None:
            notes.append(f"{name}: NEW — no main history to compare steps against")
        elif leg["mean_steps"] is None:
            reasons.append(f"{name}: no trajectory, steps cannot be compared")
        else:
            limit = leg["baseline_steps"] * (1 + tolerance)
            leg["steps_limit"] = limit
            if leg["mean_steps"] > limit and mode != "main":
                reasons.append(
                    f"{name}: {leg['mean_steps']:.0f} steps exceeds main's median "
                    f"{leg['baseline_steps']:.0f} by more than {tolerance:.0%}"
                )
    return {"ok": not reasons, "reasons": reasons, "notes": notes}


def _fmt(value: Any, spec: str = "{:.0f}") -> str:
    return "—" if value is None else spec.format(value)


def summarize(args: argparse.Namespace) -> int:
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    refs = _load(args.refs) if args.refs else {}
    legs = [read_leg(d) for d in sorted(p for p in args.jobs.iterdir() if p.is_dir())]
    base = args.baseline
    base_results = (_load(base / "results.json") if base else None) or {}
    history = (_load(base / "history.json") if base else None) or {}
    verdict = gate(legs, history, args.mode, args.tolerance)
    changes = args.changes.read_text() if args.changes and args.changes.is_file() else ""
    base_legs = {leg["leg"]: leg for leg in base_results.get("legs", [])}

    for leg in legs:
        name = leg["leg"]
        if leg["skipped"]:
            continue
        digest = "\n\n".join(t.pop("_digest") for t in leg["trials"])
        (out / f"digest-{name}.md").write_text(digest[:DIGEST_CAP])
        failures = [f for t in leg["trials"] for f in t.pop("_failures")]
        verifier = [t.pop("_verifier") for t in leg["trials"] if "_verifier" in t]
        before = base_legs.get(name, {}).get("phases", {})
        rows = ["| phase | this run | main | Δ |", "|---|---|---|---|"]
        for phase in sorted(set(leg["phases"]) | set(before)):
            now, then = leg["phases"].get(phase, 0), before.get(phase)
            delta = "—" if then is None else f"{now - then:+.1f}"
            rows.append(f"| {phase} | {now:.1f} | {_fmt(then, '{:.1f}')} | {delta} |")
        repeats: Counter[str] = Counter()
        for t in leg["trials"]:
            repeats.update(t["repeats"])
        evidence = [
            f"### Evidence — {name}",
            "",
            "Tool calls by phase (mean per trial):",
            "",
            *rows,
            "",
            f"Skills loaded: {'yes' if leg['skills_used'] else '**no**'}",
            "",
            f"Failed or erroring tool calls ({len(failures)}):",
            "",
            *(failures[:EVIDENCE_CAP] or ["- none"]),
            *(
                [f"- … {len(failures) - EVIDENCE_CAP} more in the digest"]
                if len(failures) > EVIDENCE_CAP
                else []
            ),
            "",
            "Commands run more than once:",
            "",
            *([f"- {n}× `{_snip(c, 200)}`" for c, n in repeats.most_common(15)] or ["- none"]),
        ]
        if any(t["exception"] for t in leg["trials"]):
            evidence += [
                "",
                "Harness exceptions:",
                "",
                *(f"- `{t['trial']}`: {t['exception']}" for t in leg["trials"] if t["exception"]),
            ]
        if verifier:
            evidence += ["", "Verifier output:", "", "```", *verifier, "```"]
        (out / f"evidence-{name}.md").write_text("\n".join(evidence) + "\n")

        base_digest = base / f"digest-{name}.md" if base else None
        summary = (
            f"leg {name} ({leg['agent']} / {leg['model']}): mean reward "
            f"{_fmt(leg['mean_reward'], '{:.2f}')}, mean agent steps "
            f"{_fmt(leg['mean_steps'])}, main's median "
            f"{_fmt(leg.get('baseline_steps'))}, n={leg['n']}"
        )
        (out / f"analysis-input-{name}.md").write_text(
            "\n\n".join(
                [
                    "# Run summary",
                    summary,
                    "\n".join(evidence),
                    "# Change under test",
                    changes or "_not provided_",
                    "# This run's trace",
                    digest[:DIGEST_CAP],
                    "# Baseline trace (latest successful main run)",
                    base_digest.read_text()[:DIGEST_CAP]
                    if base_digest and base_digest.is_file()
                    else "_no baseline trace available_",
                ]
            )
            + "\n"
        )

    results = {
        "refs": refs,
        "mode": args.mode,
        "tolerance": args.tolerance,
        "legs": legs,
        "gate": verdict,
    }
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str) + "\n")
    if args.mode == "main":
        now = datetime.now(UTC).isoformat(timespec="seconds")
        for leg in legs:
            if leg["skipped"] or leg["mean_steps"] is None:
                continue
            entry = {
                "at": now,
                "sha": refs.get("sha", ""),
                "steps": leg["mean_steps"],
                "reward": leg["mean_reward"],
            }
            history[leg["leg"]] = (history.get(leg["leg"], []) + [entry])[-HISTORY_LENGTH:]
        (out / "history.json").write_text(json.dumps(history, indent=1) + "\n")
    elif base and (base / "history.json").is_file():
        # Carry main's history along so a downloaded PR artifact is self-explanatory.
        (out / "history.json").write_text(json.dumps(history, indent=1) + "\n")
    print("gate:", "ok" if verdict["ok"] else "; ".join(verdict["reasons"]))
    return 0


def comment(args: argparse.Namespace) -> int:
    out: Path = args.out
    results = _load(out / "results.json") or {}
    verdict = results.get("gate", {})
    refs = results.get("refs", {})
    lines = [
        f"## {'✅' if verdict.get('ok') else '❌'} Eval",
        "",
        "<!-- lightcone-eval-report -->",
        "",
    ]
    if refs:
        lines += [" · ".join(f"{k}: `{v}`" for k, v in refs.items() if v), ""]
    lines += [
        "| Leg | Reward | Steps | main median | Tool calls | Cost | Agent min | Skills |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for leg in results.get("legs", []):
        if leg["skipped"]:
            lines.append(f"| {leg['leg']} | skipped: {leg['skip_reason']} | | | | | | |")
            continue
        steps = _fmt(leg["mean_steps"])
        if leg.get("baseline_steps") and leg["mean_steps"] is not None:
            steps += f" ({(leg['mean_steps'] / leg['baseline_steps'] - 1):+.0%})"
        lines.append(
            f"| {leg['leg']} | {_fmt(leg['mean_reward'], '{:.2f}')} | {steps} "
            f"| {_fmt(leg.get('baseline_steps'))} | {_fmt(leg['mean_tool_calls'])} "
            f"| {_fmt(leg['mean_cost_usd'], '${:.2f}')} "
            f"| {_fmt(leg['mean_agent_min'], '{:.1f}')} "
            f"| {'yes' if leg['skills_used'] else 'no'} |"
        )
    lines.append("")
    for reason in verdict.get("reasons", []):
        lines.append(f"- ❌ {reason}")
    for note in verdict.get("notes", []):
        lines.append(f"- ℹ️ {note}")
    lines.append("")
    for leg in results.get("legs", []):
        if leg["skipped"]:
            continue
        name = leg["leg"]
        analysis = out / f"analysis-{name}.md"
        lines += [f"### {name}", ""]
        if analysis.is_file() and analysis.read_text().strip():
            lines += [analysis.read_text().strip(), ""]
        else:
            lines += [
                "❌ _Trace analysis missing — see the evidence below and the "
                "`eval-results` artifact._",
                "",
            ]
        history = leg.get("history") or []
        if history:
            trend = " → ".join(_fmt(h.get("steps")) for h in history)
            lines += [f"Steps on main, oldest first: {trend}", ""]
        evidence = out / f"evidence-{name}.md"
        if evidence.is_file():
            lines += [
                "<details><summary>Evidence (deterministic)</summary>",
                "",
                evidence.read_text(),
                "</details>",
                "",
            ]
    (out / "comment.md").write_text("\n".join(lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("summarize")
    s.add_argument("--jobs", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--baseline", type=Path)
    s.add_argument("--refs", type=Path)
    s.add_argument("--changes", type=Path)
    s.add_argument("--mode", choices=["main", "pr"], default="pr")
    s.add_argument("--tolerance", type=float, default=0.25)
    c = sub.add_parser("comment")
    c.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "comment":
        return comment(args)
    if args.baseline and not args.baseline.is_dir():
        args.baseline = None
    return summarize(args)


if __name__ == "__main__":
    sys.exit(main())
