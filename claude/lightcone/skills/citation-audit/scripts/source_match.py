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

Before the substring check, `exact` must clear a **substance bar**
(`is_substantive`): a quote either carries a measured-value signal (a
decimal, `\\pm`, an (in)equality against a number, a σ significance) or
it is a clause of real length (≥ 5 words and ≥ 25 chars). A 2-word title
scrap like `Year 3` clears neither and is rejected as degenerate — this
is the gate-hardening layer that closes the reward-hack where a verifier
"supports" a claim with a topical fragment that happens to appear in the
source. Whitespace normalization plus the substance bar are the only
policies applied here; macro expansion is still out of scope.

Encoding note: `fetch_sources.py` already rewrites every `.tex` to
UTF-8, so this reader assumes UTF-8 (with a replace fallback for safety).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_WS = re.compile(r"\s+")

# Substance bar for `exact` (degenerate-quote rejection).
_MIN_WORDS = 5
_MIN_CHARS = 25
_QUANT_FLOOR = 8  # a measured-value quote must still be more than a bare operator

# A measurement-like signal: a decimal value, plus-minus, an (in)equality
# against a number, a σ significance, scientific notation. A *bare integer*
# (the `3` in "Year 3") deliberately does NOT match — that is the reward-hack
# this bar exists to reject.
_QUANT = re.compile(
    r"\d+\.\d+"            # decimal value (0.776)
    r"|\\pm|±"             # plus-minus
    r"|\\sigma|σ"          # sigma significance
    r"|\\times|×"          # times (scientific notation)
    r"|[=<>]\s*[-+]?\d"    # (in)equality against a number (S_8 = 0.7, > 3)
    r"|10\^\{?-?\d"        # powers of ten (10^{-3})
)


def _norm(s: str) -> str:
    """Collapse all whitespace to single spaces and strip."""
    return _WS.sub(" ", s).strip()


def is_substantive(exact: str) -> tuple[bool, str]:
    """Reject degenerate `exact` quotes. Return (ok, reason).

    A quote clears the bar if it carries a measured-value signal (decimal,
    `\\pm`, an (in)equality against a number, σ) — in which case even a terse
    `$S_8 = 0.776\\pm0.017$` is fine — or if it is a clause of real length
    (≥ `_MIN_WORDS` words and ≥ `_MIN_CHARS` chars). A title scrap like
    `Year 3` (2 words, integer only) clears neither and is rejected.
    """
    s = _norm(exact)
    if _QUANT.search(s):
        if len(s) >= _QUANT_FLOOR:
            return True, "substantive (measured-value signal)"
        return False, (
            f"degenerate quote: measured-value signal but only {len(s)} chars "
            f"— quote the value with its uncertainty, not a bare operator"
        )
    words = s.split()
    if len(words) >= _MIN_WORDS and len(s) >= _MIN_CHARS:
        return True, "substantive (clause-length prose)"
    return False, (
        f"degenerate quote: {len(words)} words / {len(s)} chars is below the "
        f"substance bar (need a measured value, or ≥{_MIN_WORDS} words and "
        f"≥{_MIN_CHARS} chars). Quote the clause that establishes the claim — "
        f"never a title fragment, author name, or topical scrap."
    )


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

    # The substance bar is a property of `exact` alone — context padding in
    # prefix/suffix cannot rescue a scrap quote.
    ok_sub, reason_sub = is_substantive(exact_n)
    if not ok_sub:
        return False, reason_sub

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
    if ok:
        print("verified: " + reason)
        return 0
    # A degenerate quote is a different failure than a missing one: the fix is
    # "find a substantive quote", not "re-copy the span". Label it distinctly.
    label = "rejected" if reason.startswith("degenerate") else "not_found"
    print(f"{label}: {reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
