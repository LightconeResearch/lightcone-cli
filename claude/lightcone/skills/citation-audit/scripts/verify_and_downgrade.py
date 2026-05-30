#!/usr/bin/env python3
"""verify_and_downgrade.py — close the self-validation gate.

Step 5a of the citation-audit pipeline (runs between merge and the
final `astra validate --verify-evidence` check).

The verifier-Haiku contract demands that every `supported`/`weak`
verdict pass `astra paper verify-quotes` in the Haiku's own self-check.
In practice, Haikus sometimes return quotes that look verbatim but fail
verification — typically paper-title fragments that PDF extractors
don't preserve in body text. The Haiku should have caught these and
downgraded to `unverifiable`; this script catches the ones that
slipped through.

Logic:

1. Iterate through `supported`/`weak` rows in the ledger.
2. For each, run `astra paper verify-quotes <effective_doi>` with the
   row's quote.
3. If verification fails (`not_found` or PDF-extraction error),
   downgrade the row to `unverifiable` with diagnostic `verdict_notes`.
4. Re-emit the ledger and re-run `build_audit_yaml.py` so `astra.yaml`
   reflects the downgrades.

The final `astra validate --verify-evidence` should now succeed end-to-end.

Usage:

    python3 verify_and_downgrade.py \\
        --ledger work/citation-audit/ledger.json \\
        --state work/citation-audit/fetch_state.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _verify_quote(doi: str, row: dict[str, Any]) -> tuple[bool, str]:
    """Run `astra paper verify-quotes` for one quote. Return (ok, reason)."""
    quote = row.get("quote") or {}
    location = row.get("location") or {}
    payload = {
        "quotes": [
            {
                "text": quote.get("exact", ""),
                "prefix": quote.get("prefix", ""),
                "suffix": quote.get("suffix", ""),
                "page": location.get("page"),
            }
        ]
    }
    # Drop None page (verify-quotes treats missing as "any page").
    if payload["quotes"][0]["page"] is None:
        del payload["quotes"][0]["page"]

    proc = subprocess.run(
        ["astra", "paper", "verify-quotes", doi],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        return False, f"verify-quotes failed: {(proc.stderr or '').strip()}"

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, f"unparseable response: {proc.stdout[:200]}"

    summary = data.get("summary") or {}
    if summary.get("errors", 0) > 0:
        # PDF-extraction-level failure.
        msg = (data.get("results") or [{}])[0].get("message") or "extraction error"
        return False, f"pdf extraction error: {msg}"
    results = data.get("results") or []
    if not results:
        return False, "no results returned"
    status = results[0].get("status")
    if status == "verified":
        return True, ""
    return False, f"{status}: {results[0].get('message', '')}".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--ledger", type=Path, default=Path("work/citation-audit/ledger.json")
    )
    parser.add_argument(
        "--state", type=Path, default=Path("work/citation-audit/fetch_state.json")
    )
    args = parser.parse_args()

    if not args.ledger.exists():
        print(f"error: ledger not found at {args.ledger}", file=sys.stderr)
        return 2

    ledger = json.loads(args.ledger.read_text())
    rows: list[dict[str, Any]] = ledger.get("rows", [])

    fetch_state: dict[str, dict[str, Any]] = {}
    if args.state.is_file():
        fetch_state = json.loads(args.state.read_text())

    checked = 0
    downgraded = 0
    for row in rows:
        if row.get("verdict") not in {"supported", "weak"}:
            continue
        if not row.get("quote"):
            continue
        manuscript_doi = row.get("doi")
        if not manuscript_doi:
            continue
        effective_doi = (
            (fetch_state.get(manuscript_doi) or {}).get("effective_doi")
            or manuscript_doi
        )
        checked += 1
        ok, reason = _verify_quote(effective_doi, row)
        if not ok:
            prior = row["verdict"]
            row["verdict"] = "unverifiable"
            existing_notes = row.get("verdict_notes") or ""
            row["verdict_notes"] = (
                f"verifier-self-validation gap: original verdict `{prior}`, "
                f"but `astra paper verify-quotes {effective_doi}` returned: "
                f"{reason}. The Haiku should have caught this and downgraded "
                f"on its own; this is a fallback gate.\n\n"
                f"Original Haiku notes:\n{existing_notes}"
            ).strip()
            row["quote"] = None
            row["location"] = None
            print(
                f"  ✗ {row['use_id']:<45} {prior:>10} → unverifiable "
                f"({reason[:60]})"
            )
            downgraded += 1

    # Re-summarize
    from collections import Counter

    counts = Counter(r.get("verdict") or "pending" for r in rows)
    ledger.setdefault("summary", {})["verdicts"] = dict(counts)

    args.ledger.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")
    print(
        f"\nchecked {checked} supported/weak rows; "
        f"downgraded {downgraded} that failed astra paper verify-quotes."
    )
    print(
        "Now re-run build_audit_yaml.py to drop those entries from astra.yaml's "
        "prior_insights (they no longer have evidence)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
