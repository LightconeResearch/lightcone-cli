#!/usr/bin/env python3
"""extract_text.py — extract per-page text from cached PDFs for Haiku consumption.

Optional Step 2.5 of the citation-audit pipeline. Reads `fetch_state.json`,
finds every successfully cached PDF, and writes a sidecar
`papers/<doi-slug>/text.txt` per paper with `=== Page N ===` markers
between pages.

Haiku workers read the text file (cheap, no inline-image overhead) rather
than the raw PDF — this matters because PDFs over a few hundred KB blow
either Anthropic's 32MB request-size limit or the worker's context window.
The text file is *only* for the Haiku to scan for relevance and pick
quote candidates; the verifier gate (`astra paper verify-quotes`) still
runs against the actual cached PDF.

Output:

    work/citation-audit/papers/<doi-slug>/text.txt

The `<doi-slug>` is the DOI with `/` and `:` replaced by `_`, e.g.
`10_48550_arXiv_2007_15633`.

Idempotent — skips DOIs whose text file already exists, unless
`--refresh` is given.

Usage:

    python3 extract_text.py \\
        --state work/citation-audit/fetch_state.json \\
        --out-dir work/citation-audit/papers
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import pymupdf  # type: ignore[import-not-found]
except ImportError:
    try:
        import fitz as pymupdf  # type: ignore[import-not-found, no-redef]
    except ImportError:
        print("error: pymupdf not installed", file=sys.stderr)
        sys.exit(2)


def _doi_slug(doi: str) -> str:
    """Filesystem-safe slug from a DOI."""
    return doi.replace("/", "_").replace(":", "_").replace(" ", "_")


def extract(pdf_path: Path, out_path: Path) -> tuple[int, int]:
    """Extract page-marked text. Return (pages_extracted, bytes_written)."""
    doc = pymupdf.open(pdf_path)
    chunks: list[str] = []
    for i, page in enumerate(doc, 1):
        chunks.append(f"=== Page {i} ===")
        chunks.append(page.get_text())
    doc.close()
    body = "\n".join(chunks).rstrip() + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body)
    return len(chunks) // 2, len(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("work/citation-audit/fetch_state.json"),
        help="Fetch state from fetch_papers.py (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("work/citation-audit/papers"),
        help="Output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-extract papers whose text.txt already exists.",
    )
    args = parser.parse_args()

    if not args.state.exists():
        print(f"error: fetch state not found at {args.state}", file=sys.stderr)
        return 2

    state = json.loads(args.state.read_text())

    extractable = [
        (doi, s)
        for doi, s in state.items()
        if s.get("status") in {"cached_native", "fetched_native", "fetched_via_arxiv"}
        and s.get("pdf_path")
    ]
    print(f"papers eligible for extraction: {len(extractable)}")

    extracted = 0
    skipped = 0
    failed = 0
    for doi, entry in extractable:
        pdf_path = Path(entry["pdf_path"])
        out_path = args.out_dir / _doi_slug(doi) / "text.txt"
        if out_path.exists() and not args.refresh:
            skipped += 1
            continue
        if not pdf_path.is_file():
            print(f"  ✗ {doi} — PDF missing at {pdf_path}", file=sys.stderr)
            failed += 1
            continue
        try:
            pages, n_bytes = extract(pdf_path, out_path)
            print(
                f"  ✓ {doi:<40}  {pages:>3} pages, "
                f"{n_bytes / 1024:>6.1f} KiB → {out_path.relative_to(args.out_dir.parent)}"
            )
            extracted += 1
        except Exception as exc:  # noqa: BLE001 — pymupdf raises various
            print(f"  ✗ {doi} — extraction failed: {exc}", file=sys.stderr)
            failed += 1

    print(
        f"\nextracted: {extracted}, skipped (already present): {skipped}, "
        f"failed: {failed}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
