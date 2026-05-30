#!/usr/bin/env python3
"""render_report.py — render the citation-audit HTML report.

Self-contained HTML (no external CSS / JS / fonts) so it lands cleanly
on a phone via `SendUserFile`. Three sections, action-first:

  1. **Action list.** Every wrong_paper, unsupported, and unverifiable
     verdict at the top — what the manuscript author needs to act on
     before submission.
  2. **Weak claims.** Cited paper supports a narrower version; verifier
     suggested a rewording.
  3. **Clean ledger.** All `supported` rows, collapsed by default
     under `<details>` so the report opens with the bad news.

Usage:

    python3 render_report.py \\
        --reference-dir work/reference \\
        --ledger work/citation-audit/ledger.json \\
        --out work/citation-audit/report.html
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Severity ordering: higher number → more urgent in the action list.
_VERDICT_SEVERITY = {
    "wrong_paper": 5,
    "unsupported": 4,
    "extraction_error": 3,
    "unverifiable_fetch_failed": 3,
    "unverifiable_no_pdf": 2,
    "unverifiable_no_doi": 1,
    "unverifiable": 1,
    "weak": 0,
    "supported": -1,
    "pending": -2,
}

# Inline styling — parchment + cinnabar palette from the shuttle skill's
# report template. EB-Garamond fallback is acceptable; we stay system-font
# to avoid asset deps that break phone preview.
_CSS = """
:root {
  --bg: #f6efe1;
  --ink: #1d1815;
  --rule: #c9ad7a;
  --cinnabar: #b03a2e;
  --weak: #c97a2e;
  --ok: #5a7a3a;
  --muted: #6f5e4a;
  --code-bg: #ece1cd;
}
html, body { background: var(--bg); color: var(--ink);
  font-family: Georgia, "EB Garamond", "Times New Roman", serif;
  line-height: 1.5; margin: 0; padding: 0; }
body { padding: 2rem 1.5rem; max-width: 56rem; margin: 0 auto; }
h1 { border-bottom: 2px solid var(--rule); padding-bottom: .3rem;
  font-size: 1.6rem; margin-top: 0; }
h2 { color: var(--cinnabar); border-bottom: 1px solid var(--rule);
  padding-bottom: .2rem; margin-top: 2rem; font-size: 1.25rem; }
h3 { margin-top: 1.5rem; font-size: 1.1rem; }
.summary { display: grid; grid-template-columns: repeat(auto-fit,
  minmax(8rem, 1fr)); gap: .5rem; margin: 1rem 0; }
.summary div { background: var(--code-bg); padding: .5rem .75rem;
  border-left: 3px solid var(--rule); font-size: .92rem; }
.summary .v-wrong_paper { border-color: var(--cinnabar); color: var(--cinnabar); }
.summary .v-unsupported { border-color: var(--cinnabar); color: var(--cinnabar); }
.summary .v-weak { border-color: var(--weak); color: var(--weak); }
.summary .v-supported { border-color: var(--ok); color: var(--ok); }
.summary .v-unverifiable_no_doi,
.summary .v-unverifiable_no_pdf,
.summary .v-unverifiable_fetch_failed,
.summary .v-unverifiable { border-color: var(--muted); color: var(--muted); }
.row { background: var(--code-bg); border-left: 4px solid var(--rule);
  padding: .8rem 1rem; margin: .8rem 0; border-radius: 3px; }
.row.v-wrong_paper, .row.v-unsupported { border-color: var(--cinnabar); }
.row.v-weak { border-color: var(--weak); }
.row.v-supported { border-color: var(--ok); }
.row.v-unverifiable_no_doi,
.row.v-unverifiable_no_pdf,
.row.v-unverifiable_fetch_failed,
.row.v-unverifiable { border-color: var(--muted); }
.tag { display: inline-block; padding: .05rem .4rem; margin-right: .3rem;
  border-radius: 2px; font-size: .8rem; font-family: monospace;
  background: var(--bg); color: var(--ink); border: 1px solid var(--rule); }
.tag.v-wrong_paper, .tag.v-unsupported { background: var(--cinnabar); color: var(--bg); border-color: var(--cinnabar); }
.tag.v-weak { background: var(--weak); color: var(--bg); border-color: var(--weak); }
.tag.v-supported { background: var(--ok); color: var(--bg); border-color: var(--ok); }
.cite-loc { font-family: monospace; font-size: .85rem; color: var(--muted); }
.claim { margin: .4rem 0; }
.quote { margin: .4rem 0; padding: .4rem .8rem;
  border-left: 2px solid var(--rule); font-style: italic; color: var(--ink); }
.notes { margin: .4rem 0; color: var(--muted); white-space: pre-wrap; }
.rewording { margin: .4rem 0; padding: .4rem .8rem; background: var(--bg);
  border-left: 2px solid var(--weak); font-style: italic; }
.rewording::before { content: "Suggested: "; font-weight: bold;
  font-style: normal; color: var(--weak); }
details > summary { cursor: pointer; padding: .3rem 0;
  font-weight: bold; color: var(--cinnabar); }
.footer { color: var(--muted); font-size: .85rem; margin-top: 3rem;
  border-top: 1px solid var(--rule); padding-top: 1rem; }
"""


def _esc(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s))


def _render_row(row: dict[str, Any]) -> str:
    verdict = row.get("verdict") or "pending"
    parts: list[str] = []
    parts.append(f'<div class="row v-{_esc(verdict)}">')
    parts.append(
        f'<div><span class="tag v-{_esc(verdict)}">{_esc(verdict)}</span>'
        f'<span class="cite-loc">'
        f'{_esc(row.get("citation_key"))} · '
        f'{_esc(row.get("file"))}:{_esc(row.get("line"))} · '
        f'doi={_esc(row.get("doi") or "—")}'
        f"</span></div>"
    )
    if row.get("claim"):
        parts.append(f'<div class="claim">{_esc(row["claim"])}</div>')
    if row.get("citation_text"):
        parts.append(
            f'<div class="cite-loc"><em>cite text:</em> '
            f'{_esc(row["citation_text"])}</div>'
        )
    if row.get("quote") and isinstance(row["quote"], dict):
        q = row["quote"]
        parts.append(
            f'<div class="quote">{_esc(q.get("exact", ""))}'
            f' <span class="cite-loc">'
            f'(p. {_esc((row.get("location") or {}).get("page", "?"))})'
            f"</span></div>"
        )
    if row.get("verdict_notes"):
        parts.append(f'<div class="notes">{_esc(row["verdict_notes"])}</div>')
    if row.get("suggested_rewording"):
        parts.append(f'<div class="rewording">{_esc(row["suggested_rewording"])}</div>')
    parts.append("</div>")
    return "\n".join(parts)


def _section(title: str, rows: list[dict[str, Any]], collapsed: bool = False) -> str:
    if not rows:
        return ""
    body = "\n".join(_render_row(r) for r in rows)
    if collapsed:
        return f"<details><summary>{_esc(title)} ({len(rows)})</summary>\n{body}\n</details>"
    return f"<h2>{_esc(title)} ({len(rows)})</h2>\n{body}"


def _summary_html(rows: list[dict[str, Any]]) -> str:
    counts: Counter[str] = Counter(r.get("verdict") or "pending" for r in rows)
    items = []
    for verdict in sorted(
        counts, key=lambda v: -_VERDICT_SEVERITY.get(v, 0)
    ):
        items.append(
            f'<div class="v-{_esc(verdict)}">'
            f'<b>{counts[verdict]}</b> {_esc(verdict)}'
            f"</div>"
        )
    return '<div class="summary">' + "\n".join(items) + "</div>"


def _load_subject_metadata(reference_dir: Path) -> dict[str, Any]:
    """Best-effort: pull title/abstract from index.json for the header."""
    idx_path = reference_dir / "index.json"
    if not idx_path.exists():
        return {}
    try:
        return json.loads(idx_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def render(ledger_path: Path, reference_dir: Path, out_path: Path) -> None:
    ledger = json.loads(ledger_path.read_text())
    rows: list[dict[str, Any]] = ledger.get("rows", [])
    meta = _load_subject_metadata(reference_dir)

    # Bucket rows by verdict for the three sections.
    action_verdicts = {
        "wrong_paper",
        "unsupported",
        "unverifiable_fetch_failed",
        "unverifiable_no_doi",
        "unverifiable_no_pdf",
        "unverifiable",
        "extraction_error",
    }
    action_rows = sorted(
        (r for r in rows if (r.get("verdict") or "") in action_verdicts),
        key=lambda r: (-_VERDICT_SEVERITY.get(r.get("verdict") or "", 0),
                       r.get("citation_key") or ""),
    )
    weak_rows = [r for r in rows if (r.get("verdict") or "") == "weak"]
    clean_rows = sorted(
        (r for r in rows if (r.get("verdict") or "") == "supported"),
        key=lambda r: (r.get("citation_key") or "", r.get("line") or 0),
    )
    pending_rows = [r for r in rows if (r.get("verdict") or "") in {"pending", "", None}]

    title = meta.get("title") or "Citation audit"

    body = []
    body.append(f"<h1>Citation audit: {_esc(title)}</h1>")
    body.append(_summary_html(rows))
    body.append(_section("Action list", action_rows))
    body.append(_section("Weak claims (consider rewording)", weak_rows))
    body.append(_section("Clean — supported", clean_rows, collapsed=True))
    if pending_rows:
        body.append(_section("Still pending", pending_rows, collapsed=True))

    body.append(
        '<div class="footer">'
        f'Ledger: {_esc(str(ledger_path))} · '
        f'Subject substrate: {_esc(str(reference_dir))}'
        "</div>"
    )

    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Citation audit</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_CSS}</style></head>
<body>
{chr(10).join(body)}
</body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reference-dir", type=Path, default=Path("work/reference"))
    parser.add_argument("--ledger", type=Path, default=Path("work/citation-audit/ledger.json"))
    parser.add_argument(
        "--out", type=Path, default=Path("work/citation-audit/report.html")
    )
    args = parser.parse_args()

    if not args.ledger.exists():
        print(f"error: ledger not found at {args.ledger}", file=sys.stderr)
        return 2

    render(args.ledger, args.reference_dir, args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
