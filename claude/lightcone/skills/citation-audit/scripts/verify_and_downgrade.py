#!/usr/bin/env python3
"""verify_and_downgrade.py — the strict per-anchor gate (cited-paper source).

Run **after** the verifier fan-out merges into the ledger and **as the blocking
gate** — it is what makes the skill trustworthy. The verifier's contract demands
every anchor self-validate via `source_match.py` before it returns; this script
is the merge-side re-check that no unverified quote slipped through.

For every `supported`/`weak` ledger row it re-checks **each anchor** against the
cited paper's source (the `source_dir` recorded in `fetch_state.json`, keyed by
DOI), using:
  - `source_match.quote_in_source` for `substrate: tex` anchors (contiguous
    prefix+exact+suffix, whitespace-normalized, substance bar);
  - a whitespace/OCR-tolerant fuzzy match on the PyMuPDF text for
    `substrate: pdf` anchors (no `.tex` exists for these).

An anchor that fails is dropped. A row is **downgraded to `unverifiable`** (with
a diagnostic note) **only when every one of its anchors fails** — partial support
keeps the row (its surviving anchors), since "some facets backed" is the
verifier's `weak`, not a gate failure.

There is no `identity` pass: the verdict vocabulary has no `identity` (a cite that
names a thing anchors the cited paper's self-introducing sentence like any claim),
so the only thing the gate adjudicates is "does each quote actually appear in the
source."

After this gate runs, re-run `build_audit_yaml.py` so `astra.yaml`'s
`prior_insights` reflect the downgrades.

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
    """Whitespace/OCR-tolerant substring check on PyMuPDF text, for `pdf` anchors."""
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore[import-not-found, no-redef]
        except ImportError:
            return False, "pdf anchor but PyMuPDF unavailable to verify"
    try:
        doc = pymupdf.open(pdf_path)
        text = source_match._norm(" ".join(page.get_text() for page in doc))
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"pdf text extraction failed: {exc}"
    # PDF anchors are English-narrative; require a normalized contiguous substring
    # (no substance bar — the narrative sentence is the substance).
    needle = source_match._norm(f"{prefix} {exact} {suffix}".strip())
    if source_match._norm(exact) in text:
        return True, "[pdf, fuzzy] exact present in normalized PDF text"
    if needle and needle in text:
        return True, "[pdf, fuzzy] prefix+exact+suffix present in normalized PDF text"
    return False, "[pdf, fuzzy] quote not found in normalized PDF text"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ledger", type=Path, default=Path("work/citation-audit/ledger.json"))
    parser.add_argument("--state", type=Path, default=Path("work/citation-audit/fetch_state.json"))
    args = parser.parse_args()

    if not args.ledger.exists():
        print(f"error: ledger not found at {args.ledger}", file=sys.stderr)
        return 2

    ledger = json.loads(args.ledger.read_text())
    rows: list[dict[str, Any]] = ledger.get("rows", [])

    fetch_state: dict[str, dict[str, Any]] = {}
    if args.state.is_file():
        fetch_state = json.loads(args.state.read_text())

    source_cache: dict[str, str | None] = {}

    def source_for(doi: str) -> str | None:
        if doi not in source_cache:
            entry = fetch_state.get(doi) or {}
            sd = entry.get("source_dir")
            source_cache[doi] = (
                source_match.load_source(Path(sd)) if sd and Path(sd).is_dir() else None
            )
        return source_cache[doi]

    def check_anchor(doi: str, anchor: dict[str, Any]) -> tuple[bool, str]:
        exact = anchor.get("exact", "")
        prefix = anchor.get("prefix", "")
        suffix = anchor.get("suffix", "")
        if not exact:
            return False, "empty exact"
        entry = fetch_state.get(doi) or {}
        substrate = anchor.get("substrate") or (
            "pdf" if entry.get("status") in {"pdf", "pdf_fallback"} else "tex"
        )
        if substrate == "pdf":
            pdf_path = entry.get("pdf_path")
            if not pdf_path:
                return False, f"pdf anchor but no pdf_path for {doi}"
            return _check_against_pdf(Path(pdf_path), exact, prefix, suffix)
        source = source_for(doi)
        if source is None:
            return False, f"no source available for {doi} (fetch: {entry.get('status')})"
        return source_match.quote_in_source(source, exact, prefix, suffix)

    checked = 0
    dropped_anchors = 0
    downgraded = 0
    for row in rows:
        if row.get("verdict") not in {"supported", "weak"}:
            continue
        doi = row.get("doi")
        anchors = row.get("anchors") or []
        if not anchors or not doi:
            continue
        checked += 1
        survivors: list[dict[str, Any]] = []
        failures: list[str] = []
        for anchor in anchors:
            ok, reason = check_anchor(doi, anchor)
            if ok:
                survivors.append(anchor)
            else:
                failures.append(f"{anchor.get('facet', '?')}: {reason}")
                dropped_anchors += 1

        if survivors:
            row["anchors"] = survivors
            if failures:  # partial — keep verdict, note the dropped facets
                note = "strict gate dropped unverifiable anchor(s): " + "; ".join(failures)
                row["verdict_notes"] = f"{note}\n\n{row.get('verdict_notes') or ''}".strip()
        else:  # every anchor failed → downgrade
            prior = row["verdict"]
            row["verdict"] = "unverifiable"
            row["anchors"] = []
            row["verdict_notes"] = (
                f"strict source gate: original verdict `{prior}`, but every anchor "
                f"failed to verify against source — {'; '.join(failures)}. The verifier "
                f"should have caught this in its self-check; this is the merge-side "
                f"fallback gate.\n\nOriginal verifier notes:\n{row.get('verdict_notes') or ''}"
            ).strip()
            print(f"  ✗ {row['use_id']:<45} {prior:>10} → unverifiable ({'; '.join(failures)[:70]})")
            downgraded += 1

    counts = Counter(r.get("verdict") or "pending" for r in rows)
    ledger.setdefault("summary", {})["verdicts"] = dict(counts)
    args.ledger.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")

    print(
        f"\nchecked {checked} supported/weak rows; dropped {dropped_anchors} failed anchor(s); "
        f"downgraded {downgraded} row(s) whose every anchor failed."
    )
    if downgraded:
        print("Now re-run build_audit_yaml.py to drop downgraded entries from astra.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
