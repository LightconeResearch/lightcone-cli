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

# A pdf anchor is confirmed if its fuzzy partial-ratio against the (OCR'd) PDF
# text clears this bar. Calibrated on the bench: true quotes against text PDFs
# score ~0.9; topically-related fabrications ~0.5.
PDF_FUZZY_THRESHOLD = 0.80
# Image-scan rescue: when the contiguous partial-ratio fails, fall back to the
# insertion-tolerant `ordered_recall` (which absorbs OCR reading-order splices —
# a table or column gutter spliced mid-sentence on a multi-column scan). Calibrated
# on the fresh-paper image scans (Bertin96, Landy-Szalay93): true quotes recall 1.0,
# scrambled/cross-paper controls top out ~0.66. 0.85 sits in the gap with margin.
PDF_RECALL_THRESHOLD = 0.85
# DPI for rasterizing an image page before OCR. PyMuPDF's get_textpage_ocr default
# is 72 (screen res) — far too low for a scanned journal page; at 72 dpi true quotes
# scored ~0.5 and the scans read as "unverifiable". 300 dpi is the OCR standard and
# is what makes pre-arXiv image scans verifiable at all.
OCR_DPI = 300
# A page whose embedded text layer is thinner than this is treated as an image
# scan and OCR'd (pre-arXiv papers are very often scans).
MIN_PAGE_CHARS = 200

# Tools the gate wanted but could not import/run — surfaced at the end so the
# orchestrator can ask the user to install them (don't silently degrade).
MISSING_TOOLS: set[str] = set()
_PDF_TEXT_CACHE: dict[str, tuple[str | None, str]] = {}


def _pdf_text(pdf_path: Path) -> tuple[str | None, str]:
    """Extract normalized PDF text, OCR-ing image pages. Returns (norm_text, method).

    Prefers PyMuPDF (page text, with a per-page OCR fallback via Tesseract for
    image scans); falls back to pypdf (no OCR) when PyMuPDF is absent. Cached per
    path — OCR is slow. `norm_text` is `None` when no extractor is available."""
    key = str(pdf_path)
    if key in _PDF_TEXT_CACHE:
        return _PDF_TEXT_CACHE[key]
    text: str | None = None
    method = "none"
    pymupdf = None
    for modname in ("pymupdf", "fitz"):
        try:
            pymupdf = __import__(modname)
            break
        except ImportError:
            continue
    if pymupdf is None:
        MISSING_TOOLS.add("pymupdf")
    else:
        try:
            doc = pymupdf.open(str(pdf_path))
            parts: list[str] = []
            ocr_pages = 0
            for page in doc:
                t = page.get_text()
                if len(t.strip()) < MIN_PAGE_CHARS:  # image page → OCR
                    try:
                        tp = page.get_textpage_ocr(flags=0, full=True, dpi=OCR_DPI)
                        t_ocr = page.get_text(textpage=tp)
                        if len(t_ocr.strip()) > len(t.strip()):
                            t, _ = t_ocr, ocr_pages
                            ocr_pages += 1
                    except Exception:  # noqa: BLE001 — OCR needs the tesseract binary
                        MISSING_TOOLS.add("tesseract (OCR; brew install tesseract)")
                parts.append(t)
            doc.close()
            text = " ".join(parts)
            method = f"pymupdf+ocr:{ocr_pages}p" if ocr_pages else "pymupdf"
        except Exception as exc:  # noqa: BLE001
            method = f"pymupdf-failed:{exc}"
            text = None
    if text is None and pymupdf is None:
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]

            text = " ".join((p.extract_text() or "") for p in PdfReader(str(pdf_path)).pages)
            method = "pypdf (no OCR)"
        except Exception:  # noqa: BLE001
            text = None
    norm = source_match.norm_pdf(text) if text else None
    _PDF_TEXT_CACHE[key] = (norm, method)
    return _PDF_TEXT_CACHE[key]


def _check_against_pdf(pdf_path: Path, exact: str, prefix: str, suffix: str) -> tuple[bool, str]:
    """Fuzzy, OCR-tolerant check of a `pdf` anchor against the cited PDF.

    PDF anchors are English narrative (the verifier contract forbids quoting math
    from a PDF). Three escalating checks:
      1. normalized substring (clean text PDFs);
      2. order-sensitive `partial_ratio` (tolerates ligature/OCR symbol noise like
         a Greek µ extracted as ``fi`` — a contiguous match with a few bad chars);
      3. insertion-tolerant `ordered_recall` (rescues image scans whose OCR splices
         a table/column-gutter mid-sentence, breaking contiguity but not order).
    Only when all three fail is the anchor reported unverifiable — and on a scan
    that means a genuine extraction gap, no longer the default fate of a scan."""
    text, method = _pdf_text(pdf_path)
    if not text:
        return False, f"pdf anchor unverifiable: no PDF text extractor available ({method})"
    needle = source_match.norm_pdf(exact)
    if not needle:
        return False, "empty exact"
    if needle in text:
        return True, f"[pdf:{method}] exact present in normalized PDF text"
    ctx = source_match.norm_pdf(f"{prefix} {exact} {suffix}")
    if ctx and ctx in text:
        return True, f"[pdf:{method}] prefix+exact+suffix present"
    ratio = source_match.partial_ratio(needle, text)
    if ratio >= PDF_FUZZY_THRESHOLD:
        return True, f"[pdf:{method}] fuzzy partial_ratio={ratio:.2f} ≥ {PDF_FUZZY_THRESHOLD}"
    recall = source_match.ordered_recall(needle, text)
    if recall >= PDF_RECALL_THRESHOLD:
        return True, (
            f"[pdf:{method}] insertion-tolerant ordered_recall={recall:.2f} ≥ "
            f"{PDF_RECALL_THRESHOLD} (OCR spliced foreign text mid-quote; words present in order)"
        )
    scanned = "ocr" in method
    detail = (
        "; source is an image scan and the OCR text genuinely does not contain "
        "this quote in order — not a resolution artifact (OCR runs at "
        f"{OCR_DPI} dpi)" if scanned else ""
    )
    return False, (
        f"[pdf:{method}] not confirmable (partial_ratio={ratio:.2f} < {PDF_FUZZY_THRESHOLD}, "
        f"ordered_recall={recall:.2f} < {PDF_RECALL_THRESHOLD}{detail})"
    )


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
    if MISSING_TOOLS:
        print(
            "\n⚠ PDF tools missing — some pdf-backed anchors could not be verified: "
            + ", ".join(sorted(MISSING_TOOLS))
            + ".\n  Install to make pdf cites verifiable, then re-run the gate:\n"
            "    python3 -m pip install pymupdf      # PDF text + OCR driver\n"
            "    brew install tesseract              # OCR engine for image-scan PDFs\n"
            "  (The skill should ASK the user to install these rather than silently "
            "leaving pdf cites unverifiable.)"
        )
    if downgraded:
        print("Now re-run build_audit_yaml.py to drop downgraded entries from astra.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
