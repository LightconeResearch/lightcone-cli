#!/usr/bin/env python3
"""build_citation_ledger.py — turn paper-extraction's index.json into a
per-citation-use-site ledger ready for verification.

Reads `work/reference/index.json` plus the `.tex` source under
`work/reference/source/`, and writes a `ledger.json` whose rows are
`(citation_key, file, line, claim_sentence, prefix, suffix, doi)` —
one row per `\\cite{}` use-site.

The orchestrator (the citation-audit skill) fans these rows out to
per-paper verifier sub-agents (one per `citation_key`, all rows for
that key passed together) and merges verdicts back into the ledger.

Idempotent: an existing ledger.json is loaded and rows already
present (matched by use_id) preserve their `verdict` field. New
rows are added with `verdict: null`.

Usage:

  python3 build_citation_ledger.py \\
      --reference-dir work/reference \\
      --out work/citation-audit/ledger.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict, fields as dataclass_fields
from pathlib import Path

# All citation commands paper-extraction recognises. Match the command,
# any optional bracketed args, and the brace-delimited key list. The
# trailing `\b` rejects \citetextfoo etc.
_CITE_CMD = re.compile(
    r"\\(?:cite|citep|citet|citeps|citets|citetext|citealp|citealt|citeauthor|citeyear|"
    r"autocite|textcite|parencite|footcite|smartcite)\b"
    r"(?:\s*\[[^\]]*\])?(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}"
)

# Sentence-end markers in LaTeX prose. We treat any of these as a hard
# boundary when walking outward from a cite call:
#   - end-of-line followed by blank line (paragraph break)
#   - \\ at end of line (forced break inside equations or tables)
#   - "."/"!"/"?" followed by whitespace and an uppercase letter, '\\',
#     or end-of-text
#   - section-level commands
#   - \begin{} / \end{} block boundaries
_END_RE = re.compile(
    r"(?:\n\s*\n)"  # paragraph break
    r"|\\\\(?:\s*$|\s)"  # forced linebreak
    r"|[.!?](?=\s+(?:[A-Z\\]|$))"  # sentence punctuation
    r"|\\(?:section|subsection|subsubsection|paragraph|chapter)\*?\s*\{"
    r"|\\(?:begin|end)\s*\{",
    re.MULTILINE,
)

# Useful characters around the claim, for the W3C TextQuoteSelector
# prefix/suffix fields. ~80 chars each side is plenty.
_CONTEXT_CHARS = 80


@dataclass
class LedgerRow:
    """One row of the citation-audit ledger.

    Stable across runs: `use_id` derived from key+line, so re-running
    after edits to the manuscript reliably refreshes only rows whose
    location changed.
    """

    use_id: str
    citation_key: str
    doi: str | None
    file: str
    line: int
    cite_command: str  # e.g. "citep" or "citet"
    claim: str  # the manuscript sentence containing the cite
    prefix: str  # ~80 chars before the claim
    suffix: str  # ~80 chars after the claim
    citation_text: str | None  # from index.json (full bib entry text)
    verdict: str | None  # populated by the verifier in later steps
    verdict_notes: str | None  # rationale for non-`supported` verdicts


def make_use_id(citation_key: str, line: int) -> str:
    """Stable identifier slugged from key + line.

    Re-runs after edits: a citation moved from line 205 → 210 gets a new
    use_id and a fresh verdict; the old row drops out on re-build. This
    is intentional — drifted-location citations get re-verified rather
    than relying on stale verdicts.
    """
    # Strip any non-snake-case characters from the key
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", citation_key).lower()
    return f"{safe}__line_{line}"


def _cite_keys(match: "re.Match[str]") -> list[str]:
    """The comma-separated bibkeys inside a `\\cite{...}` match's brace group."""
    return [k.strip() for k in match.group(1).split(",")]


def _locate_cite(text: str, citation_key: str) -> "re.Match[str] | None":
    """Find the `\\cite` in `text` whose key-list contains `citation_key`.

    A single source line often carries several `\\cite` calls in different
    sentences (e.g. `...B modes \\citep{li, dalal}. A PSF study ... \\citep{zhang}.`).
    Anchoring on the *first* cite would bind every key on the line to the first
    sentence's claim — the mis-binding bug this guards against. So we return the
    cite that actually contains the target key. Falls back to the first cite only
    when the key isn't present at all (degenerate; caller widens the search).
    """
    first: "re.Match[str] | None" = None
    for m in _CITE_CMD.finditer(text):
        if first is None:
            first = m
        if citation_key in _cite_keys(m):
            return m
    return first


def extract_claim(tex: str, line: int, citation_key: str) -> tuple[str, str, str, str]:
    """Return `(claim, prefix, suffix, cite_command)` for `citation_key`'s cite
    at the given 1-indexed line of the `tex` string.

    Anchors on the specific `\\cite` whose key-list contains `citation_key` —
    NOT merely the first cite on the line — then walks outward to the nearest
    sentence boundary in either direction. Multi-line claims are returned with
    internal whitespace collapsed to single spaces; verification needs the
    textual content, not layout fidelity.
    """
    # offsets[i] = byte position of the start of line (i+1).
    offsets = [0]
    pos = 0
    for ch in tex:
        pos += 1
        if ch == "\n":
            offsets.append(pos)

    if line < 1 or line > len(offsets):
        raise IndexError(f"line {line} out of range (1..{len(offsets)})")

    line_start = offsets[line - 1]
    line_end = offsets[line] if line < len(offsets) else len(tex)
    line_text = tex[line_start:line_end]

    cite_match = _locate_cite(line_text, citation_key)
    base = line_start
    if cite_match is None or citation_key not in _cite_keys(cite_match):
        # Key not on this exact line — the cite may span onto the next line, or
        # the recorded line is slightly off. Search a small surrounding window
        # and prefer the key-matching cite there.
        window_start = offsets[max(0, line - 3)]
        window_end = offsets[min(len(offsets) - 1, line + 1)]
        win_match = _locate_cite(tex[window_start:window_end], citation_key)
        if win_match is not None:
            cite_match, base = win_match, window_start
    if cite_match is None:
        return ("", "", "", "")
    cite_abs = base + cite_match.start()

    # The cite command is the first letters after "\".
    cite_cmd = re.match(r"\\(\w+)", cite_match.group(0)).group(1)

    # Walk backward to find the start of the sentence.
    # Scan the substring `tex[:cite_abs]` from the end for sentence ends.
    backward_window = tex[:cite_abs]
    ends_back = list(_END_RE.finditer(backward_window))
    claim_start = ends_back[-1].end() if ends_back else 0

    # Walk forward to find the end of the sentence.
    forward_window = tex[cite_abs:]
    ends_fwd = _END_RE.search(forward_window)
    claim_end = cite_abs + (ends_fwd.start() if ends_fwd else len(forward_window))
    # Include the sentence-terminating punctuation if applicable.
    if ends_fwd and forward_window[ends_fwd.start()] in ".!?":
        claim_end += 1

    claim = tex[claim_start:claim_end].strip()
    claim = re.sub(r"\s+", " ", claim)

    prefix_raw = tex[max(0, claim_start - _CONTEXT_CHARS) : claim_start]
    suffix_raw = tex[claim_end : claim_end + _CONTEXT_CHARS]
    prefix = re.sub(r"\s+", " ", prefix_raw).strip()
    suffix = re.sub(r"\s+", " ", suffix_raw).strip()

    return (claim, prefix, suffix, cite_cmd)


def build_ledger(reference_dir: Path, existing: dict[str, LedgerRow]) -> list[LedgerRow]:
    """Build the ledger from `<reference_dir>/index.json` + tex source.

    `existing` is a dict use_id → LedgerRow for rows already verified;
    matching rows preserve their `verdict` / `verdict_notes` fields.
    """
    index_path = reference_dir / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(
            f"{index_path} not found — run paper-extraction first"
        )
    index = json.loads(index_path.read_text())

    source_dir = reference_dir / (index.get("source_dir") or "source")
    if not source_dir.exists():
        raise FileNotFoundError(
            f"{source_dir} not found — citation context needs Path A source"
        )

    # Cache tex file contents by filename so we don't re-read for every cite.
    tex_cache: dict[str, str] = {}

    def get_tex(fname: str) -> str:
        if fname not in tex_cache:
            path = source_dir / fname
            if not path.exists():
                # Some bib invocations point at .tex files outside source/
                # (e.g. claims_macros.tex at top of bundle). Try sibling.
                alt = reference_dir / fname
                if alt.exists():
                    path = alt
                else:
                    raise FileNotFoundError(f"source file {fname} not found")
            tex_cache[fname] = path.read_text()
        return tex_cache[fname]

    rows: list[LedgerRow] = []
    citations = index.get("citations") or {}
    for key, entry in citations.items():
        doi = entry.get("doi")
        citation_text = entry.get("citation")
        locations = entry.get("locations") or []
        if not locations and doi is None:
            # Path B with no invocation locations and no resolved bib —
            # still include with verdict unverifiable.
            uid = make_use_id(key, 0)
            rows.append(
                LedgerRow(
                    use_id=uid,
                    citation_key=key,
                    doi=None,
                    file="",
                    line=0,
                    cite_command="",
                    claim="",
                    prefix="",
                    suffix="",
                    citation_text=citation_text,
                    verdict="unverifiable_no_doi",
                    verdict_notes="no DOI resolved and no source location captured",
                )
            )
            continue
        for loc in locations:
            fname = loc["file"]
            line = loc["line"]
            uid = make_use_id(key, line)
            try:
                claim, prefix, suffix, cmd = extract_claim(get_tex(fname), line, key)
            except (FileNotFoundError, IndexError) as exc:
                rows.append(
                    LedgerRow(
                        use_id=uid,
                        citation_key=key,
                        doi=doi,
                        file=fname,
                        line=line,
                        cite_command="",
                        claim="",
                        prefix="",
                        suffix="",
                        citation_text=citation_text,
                        verdict="extraction_error",
                        verdict_notes=str(exc),
                    )
                )
                continue
            # Preserve any verdict from an earlier run.
            prior = existing.get(uid)
            verdict = prior.verdict if prior else None
            verdict_notes = prior.verdict_notes if prior else None
            # No DOI → unverifiable_no_doi takes precedence over null
            if doi is None and verdict is None:
                verdict = "unverifiable_no_doi"
                verdict_notes = "bib entry has no DOI; cannot verify against a source"
            rows.append(
                LedgerRow(
                    use_id=uid,
                    citation_key=key,
                    doi=doi,
                    file=fname,
                    line=line,
                    cite_command=cmd,
                    claim=claim,
                    prefix=prefix,
                    suffix=suffix,
                    citation_text=citation_text,
                    verdict=verdict,
                    verdict_notes=verdict_notes,
                )
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=Path("work/reference"),
        help="paper-extraction's work/reference/ directory (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("work/citation-audit/ledger.json"),
        help="ledger output path (default: %(default)s)",
    )
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, LedgerRow] = {}
    if args.out.exists():
        raw = json.loads(args.out.read_text())
        # Downstream steps (build_audit_yaml) enrich ledger rows with extra keys
        # (quote, location, suggested_rewording). Keep only the LedgerRow fields
        # so re-running after an audit doesn't choke on the enriched ledger — we
        # only need `verdict`/`verdict_notes` preserved from prior runs anyway.
        _fields = {f.name for f in dataclass_fields(LedgerRow)}
        for entry in raw.get("rows", []):
            existing[entry["use_id"]] = LedgerRow(
                **{k: v for k, v in entry.items() if k in _fields}
            )

    rows = build_ledger(args.reference_dir, existing)
    summary = {
        "total": len(rows),
        "with_doi": sum(1 for r in rows if r.doi),
        "no_doi": sum(1 for r in rows if r.doi is None),
        "verdicts": {},
    }
    for r in rows:
        v = r.verdict or "pending"
        summary["verdicts"][v] = summary["verdicts"].get(v, 0) + 1

    out_doc = {
        "schema_version": 1,
        "summary": summary,
        "rows": [asdict(r) for r in rows],
    }
    args.out.write_text(json.dumps(out_doc, indent=2, ensure_ascii=False) + "\n")

    print(
        f"wrote {args.out}: {summary['total']} rows "
        f"({summary['with_doi']} with DOI, {summary['no_doi']} without)"
    )
    for v, n in sorted(summary["verdicts"].items()):
        print(f"  {v}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
