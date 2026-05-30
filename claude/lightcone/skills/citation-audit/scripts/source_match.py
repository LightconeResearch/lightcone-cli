#!/usr/bin/env python3
"""source_match.py — verify a verbatim quote against a paper's arXiv source.

The trust anchor of the arXiv-source pivot. A quote "supports" a claim
only if its exact text — and, when present, its prefix/suffix context —
appears **contiguously** in the cited paper's LaTeX source. Against clean
source (no PDF-extraction noise) this can be a real substring check, not
a fuzzy `partial_ratio ≥ 70` against garbled text.

Two consumers:
  - the fan-out verifier calls this as a CLI for its in-loop self-check
    (`--exact ... [--prefix ...] [--suffix ...] --source-dir ...`);
  - the orchestrator's strict gate (`verify_and_downgrade.py`) imports
    `quote_in_source()` to re-check every supported/weak row after merge.

Matching model
--------------
Both the source (all `.tex` concatenated) and the candidate string are
**whitespace-normalized** (every run of whitespace → one space). LaTeX
line-wrapping, indentation, and the `\\input` split across files all
collapse away. The check is then a plain substring test:

  - if prefix/suffix are given → require `prefix exact suffix`
    contiguous (the strong check; trustworthy against clean source);
  - else → require `exact` alone.

Whitespace normalization is the *only* normalization applied here.
Macro expansion and degenerate-quote rejection (minimum substance,
clause-not-fragment) are deliberately left to the gate-hardening layer —
this module establishes the substrate, not the strictness policy.

Encoding note: `fetch_sources.py` already rewrites every `.tex` to
UTF-8, so this reader assumes UTF-8 (with a replace fallback for safety).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    """Collapse all whitespace to single spaces and strip."""
    return _WS.sub(" ", s).strip()


def load_source(source_dir: Path) -> str:
    """Concatenate every `.tex` under `source_dir`, whitespace-normalized.

    Files are joined with a single space so a quote never spuriously
    matches across a file boundary as if contiguous.
    """
    parts: list[str] = []
    for p in sorted(source_dir.rglob("*.tex")):
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return _norm(" ".join(parts))


def quote_in_source(
    source: str,
    exact: str,
    prefix: str | None = None,
    suffix: str | None = None,
) -> tuple[bool, str]:
    """Check a quote against normalized `source`. Return (ok, reason).

    `source` must already be normalized via `_norm`/`load_source`.
    """
    exact_n = _norm(exact)
    if not exact_n:
        return False, "empty exact quote"

    prefix_n = _norm(prefix) if prefix else ""
    suffix_n = _norm(suffix) if suffix else ""

    if prefix_n or suffix_n:
        contiguous = _norm(f"{prefix_n} {exact_n} {suffix_n}")
        if contiguous in source:
            return True, "verified (prefix+exact+suffix contiguous in source)"
        # Distinguish the failure: is `exact` itself present at all?
        if exact_n in source:
            return False, "exact present but prefix/suffix context does not match source"
        return False, "exact quote not found in source"

    if exact_n in source:
        return True, "verified (exact found in source; no context provided)"
    return False, "exact quote not found in source"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--exact", required=True)
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--suffix", default=None)
    args = parser.parse_args()

    if not args.source_dir.is_dir():
        print(f"error: source dir not found: {args.source_dir}", file=sys.stderr)
        return 2

    source = load_source(args.source_dir)
    ok, reason = quote_in_source(source, args.exact, args.prefix, args.suffix)
    print(("verified: " if ok else "not_found: ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
