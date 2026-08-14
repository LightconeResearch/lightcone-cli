"""Digest a `claude -p --output-format stream-json` transcript.

Usage: trace_digest.py <transcript.jsonl> <trace-out.md>

Writes a chronological, human-readable agent trace to <trace-out.md> and
prints shell-sourceable `key=value` run metrics on stdout (model, turns,
tool_calls, cost, duration).
"""

import json
import sys
from collections import Counter
from pathlib import Path

RESULT_SNIPPET = 300
ERROR_SNIPPET = 1500


def _fence(text: str) -> str:
    """Neutralize backtick fences so embedded output can't break ours."""
    return text.replace("```", "`​``")


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: trace_digest.py <transcript.jsonl> <trace-out.md>", file=sys.stderr)
        sys.exit(1)

    transcript = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    model = ""
    metrics: dict = {}
    tool_counts: Counter = Counter()
    body: list[str] = []

    for line in transcript.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = d.get("type")

        if t == "system" and d.get("subtype") == "init":
            model = d.get("model", "")

        elif t == "assistant":
            for c in d.get("message", {}).get("content", []):
                if c.get("type") == "text" and c["text"].strip():
                    body.append(f"**Claude:** {c['text'].strip()}\n")
                elif c.get("type") == "tool_use":
                    name = c.get("name", "?")
                    tool_counts[name] += 1
                    inp = c.get("input", {})
                    detail = (
                        inp.get("command")
                        or inp.get("file_path")
                        or inp.get("skill")
                        or json.dumps(inp)[:200]
                    )
                    body.append(f"🔧 `{name}`\n\n```\n{_fence(str(detail))}\n```\n")

        elif t == "user":
            for c in d.get("message", {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    content = c.get("content", "")
                    if isinstance(content, list):
                        content = "\n".join(
                            x.get("text", "") for x in content if isinstance(x, dict)
                        )
                    content = str(content).strip()
                    if c.get("is_error"):
                        body.append(
                            f"❌ **error result:**\n\n```\n{_fence(content[:ERROR_SNIPPET])}\n```\n"
                        )
                    elif content:
                        # Collapse to one line so the whole snippet stays
                        # inside the blockquote
                        snippet = " ".join(content[:RESULT_SNIPPET].split())
                        ellipsis = " …" if len(content) > RESULT_SNIPPET else ""
                        body.append(f"> {_fence(snippet)}{ellipsis}\n")

        elif t == "result":
            metrics = d

    turns = metrics.get("num_turns", 0)
    cost = metrics.get("total_cost_usd", 0.0) or 0.0
    duration_s = (metrics.get("duration_ms", 0) or 0) // 1000
    duration = f"{duration_s // 60}m{duration_s % 60:02d}s"
    tool_calls = sum(tool_counts.values())
    tools_breakdown = ", ".join(f"{n} ×{c}" for n, c in tool_counts.most_common())

    header = [
        "# Agent trace",
        "",
        f"- **Model:** {model or '?'}",
        f"- **Turns:** {turns}",
        f"- **Tool calls:** {tool_calls} ({tools_breakdown})",
        f"- **Cost:** ${cost:.2f}",
        f"- **Agent wall time:** {duration}",
        f"- **Errored:** {metrics.get('is_error', '?')}",
        "",
        "---",
        "",
    ]
    out_path.write_text("\n".join(header) + "\n".join(body))

    print(f"model={model}")
    print(f"turns={turns}")
    print(f"tool_calls={tool_calls}")
    print(f"cost={cost:.2f}")
    print(f"duration={duration}")


if __name__ == "__main__":
    main()
