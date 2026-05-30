#!/usr/bin/env python3
"""verify_and_downgrade.py — the strict per-quote gate (arXiv source).

Step 5 of the citation-audit pipeline, run **after** the merge
(`build_audit_yaml.py`) and **as the blocking gate** — it is what makes
the skill trustworthy now that verification lives on arXiv source rather
than PDF.

The fan-out verifier's contract demands every `supported`/`weak` verdict
self-validate via `source_match.py` before it returns. This script is the
orchestrator-side re-check that no unverified quote slipped through: for
every `supported`/`weak` ledger row, it re-checks the quote against the
cited paper's arXiv source (the `source_dir` recorded in
`fetch_state.json`). A quote that fails — `exact` not in source, or the
`prefix`/`suffix` context not contiguous — is **downgraded to
`unverifiable`** with a diagnostic note, and its quote/location are
dropped.

Why source, not `astra paper verify-quotes`
-------------------------------------------
The pivot moved quote-finding and verification to arXiv LaTeX source.
A source-derived quote carries the author's markup (`$S_8=0.776\\pm
0.017$`, `\\citep{...}`) which the PDF text layer mangles — so
`astra paper verify-quotes` (PDF-based) and `astra validate
--verify-evidence` (PDF-based) would *reject* a correct source quote.
This source check supersedes them as the gate. The astra-side PDF path
is retired from the pipeline; an upstream enhancement (source-aware
`verify-evidence`) is noted for `astra-tools`. `pdf_fallback` papers
(arXiv has a PDF but no source — rare) are the one exception: their
quotes are re-checked against the local PDF text via PyMuPDF, with the
lossiness flagged in the note.

After this gate runs, re-run `build_audit_yaml.py` so `astra.yaml`'s
`prior_insights` reflect the downgrades (downgraded rows no longer carry
evidence and would otherwise fail structural validation).

Usage:

    python3 verify_and_downgrade.py \\
        --ledger work/citation-audit/ledger.json \\
        --state work/citation-audit/fetch_state.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import source_match


def _check_against_pdf(pdf_path: Path, exact: str, prefix: str, suffix: str) -> tuple[bool, str]:
    """Last-resort: whitespace-normalized substring check on PyMuPDF text.

    Only reached for `pdf_fallback` papers (arXiv source PDF-only). The
    lossiness is flagged so the report can mark these specially.
    """
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore[import-not-found, no-redef]
        except ImportError:
            return False, "pdf_fallback paper but PyMuPDF unavailable to verify"
    try:
        doc = pymupdf.open(pdf_path)
        text = source_match._norm(" ".join(page.get_text() for page in doc))
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"pdf_fallback text extraction failed: {exc}"
    ok, reason = source_match.quote_in_source(text, exact, prefix, suffix)
    return ok, f"[pdf_fallback, lossy] {reason}"


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

    # Cache normalized source per DOI so a heavily-cited paper is read once.
    source_cache: dict[str, str | None] = {}

    def source_for(doi: str) -> str | None:
        if doi not in source_cache:
            entry = fetch_state.get(doi) or {}
            sd = entry.get("source_dir")
            source_cache[doi] = (
                source_match.load_source(Path(sd)) if sd and Path(sd).is_dir() else None
            )
        return source_cache[doi]

    checked = 0
    downgraded = 0
    for row in rows:
        if row.get("verdict") not in {"supported", "weak"}:
            continue
        quote = row.get("quote") or {}
        exact = quote.get("exact")
        if not exact:
            continue
        doi = row.get("doi")
        if not doi:
            continue
        checked += 1
        prefix = quote.get("prefix", "")
        suffix = quote.get("suffix", "")

        entry = fetch_state.get(doi) or {}
        status = entry.get("status")
        if status == "pdf_fallback" and entry.get("pdf_path"):
            ok, reason = _check_against_pdf(
                Path(entry["pdf_path"]), exact, prefix, suffix
            )
        else:
            source = source_for(doi)
            if source is None:
                ok, reason = False, (
                    f"no arXiv source available for {doi} "
                    f"(fetch status: {status}); cannot verify quote"
                )
            else:
                ok, reason = source_match.quote_in_source(source, exact, prefix, suffix)

        if not ok:
            prior = row["verdict"]
            row["verdict"] = "unverifiable"
            existing_notes = row.get("verdict_notes") or ""
            row["verdict_notes"] = (
                f"strict source gate: original verdict `{prior}`, but the quote "
                f"did not verify against arXiv source — {reason}. The verifier "
                f"should have caught this in its self-check; this is the "
                f"orchestrator-side fallback gate.\n\n"
                f"Original verifier notes:\n{existing_notes}"
            ).strip()
            row["quote"] = None
            row["location"] = None
            print(f"  ✗ {row['use_id']:<45} {prior:>10} → unverifiable ({reason[:70]})")
            downgraded += 1

    counts = Counter(r.get("verdict") or "pending" for r in rows)
    ledger.setdefault("summary", {})["verdicts"] = dict(counts)
    args.ledger.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")

    print(
        f"\nchecked {checked} supported/weak rows against arXiv source; "
        f"downgraded {downgraded} that failed."
    )
    if downgraded:
        print(
            "Now re-run build_audit_yaml.py to drop the downgraded entries from "
            "astra.yaml's prior_insights (they no longer carry evidence)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
