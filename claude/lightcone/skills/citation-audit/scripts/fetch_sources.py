#!/usr/bin/env python3
"""fetch_sources.py — fetch the arXiv LaTeX source of every cited paper.

Step 2 of the citation-audit pipeline, and the heart of the
arXiv-source pivot. Walks the unique DOIs in `ledger.json` and, for
each, fetches the cited paper's **arXiv e-print source tarball** (not
the PDF) into

    work/citation-audit/papers/<doi-slug>/source/

so the fan-out verifiers read the author's actual LaTeX — math,
quantitative values with uncertainties, ligatures, and all — instead
of the lossy mush PDF extraction produces. The outcome per DOI is
recorded in a sidecar `fetch_state.json` the orchestrator joins
against when building verifier partitions.

Why source, not PDF
-------------------
PDF text extraction collapses math (`$S_8 = 0.776\\pm0.017$` becomes
unreadable), drops ligatures, and occasionally yields captcha pages or
ISO-8859 garbage. That pushed verifiers toward quoting topical title
fragments because the real evidence was unreadable. The arXiv source is
the author's words, verbatim. See
`paper-extraction/references/arxiv-source.md` for the same machinery
applied to the *subject* paper; this script extends it to *cited*
papers.

The resolve chain (per DOI)
---------------------------
  1. arXiv DOI (`10.48550/arXiv.<id>`) — the eprint id is in the DOI.
  2. Otherwise resolve the eprint id from **verifiable metadata** via
     `resolve_arxiv.resolve()` (ADS `identifier[]`, then Crossref
     `relation.has-preprint`). No title-guessing, no LLM judgment.
  3. With an eprint id: `curl -L https://arxiv.org/e-print/<id>` and
     extract. arXiv "source" can be a gzipped tar, a single gzipped
     `.tex`, or — when the author only submitted a PDF — a PDF. The
     first two are `source_fetched`; a PDF is `pdf_fallback`.
  4. No eprint id: the paper is **genuinely pre-arXiv**. Confirm its
     identity via ADS metadata (`resolve_arxiv.resolve_metadata`) and
     record `pre_arxiv` — the verifier confirms identity from this
     metadata and never quote-fakes. If ADS has nothing either, the
     cite is `unresolvable`.

All `.tex` files are normalized to UTF-8 on extraction (the Heymans
1210.0032 trap: ISO-8859-1 with very long single lines — a naive
UTF-8 grep returns zero hits). The verifier then never has to know
about encoding.

fetch_state.json entry shape
----------------------------
    {
      "status": "source_fetched" | "pre_arxiv" | "pdf_fallback"
                | "unresolvable",
      "arxiv_id": "1210.0032" | null,
      "resolved_source": "doi" | "ads" | "crossref" | null,
      "source_dir": "<rel path to papers/<slug>/source>" | null,
      "main_tex": "<filename of \\documentclass file>" | null,
      "tex_files": ["a.tex", "sec/b.tex", ...],
      "pdf_path": "<path>" | null,        # only for pdf_fallback
      "ads_metadata": {...} | null,        # only for pre_arxiv
      "error": "<string>" | null,          # only for unresolvable
      "fetched_at": "<iso8601 utc>"
    }

Idempotent — re-runs skip DOIs already in `fetch_state.json` unless
`--refresh` is given. Keyed by the manuscript (.bib) DOI.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import resolve_arxiv  # sibling module

ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{id}"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{id}"
HTTP_TIMEOUT = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _doi_slug(doi: str) -> str:
    """Filesystem-safe slug from a DOI."""
    return doi.replace("/", "_").replace(":", "_").replace(" ", "_")


def _arxiv_id_from_doi(doi: str) -> str | None:
    """Extract the eprint id from a `10.48550/arXiv.<id>` DOI, else None."""
    low = doi.lower()
    marker = "10.48550/arxiv."
    if low.startswith(marker):
        return doi[len(marker):]
    return None


def _curl(url: str, dest: Path) -> bool:
    """Download `url` to `dest` with curl. Return True on success."""
    proc = subprocess.run(
        ["curl", "-sL", "--fail", "-o", str(dest), url],
        capture_output=True,
        text=True,
        timeout=HTTP_TIMEOUT,
        check=False,
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _normalize_tex_to_utf8(path: Path) -> None:
    """Rewrite `path` as UTF-8 if it isn't already valid UTF-8.

    arXiv source is frequently ISO-8859-1 (Latin-1). Latin-1 decodes any
    byte sequence, so it is a safe fallback that never raises. After this
    pass every `.tex` is UTF-8 and a normal grep/Read finds content.
    """
    raw = path.read_bytes()
    try:
        raw.decode("utf-8")
        return  # already UTF-8
    except UnicodeDecodeError:
        pass
    path.write_text(raw.decode("latin-1"), encoding="utf-8")


def _extract_source(blob: Path, source_dir: Path, arxiv_id: str) -> str:
    """Extract an arXiv e-print blob into `source_dir`.

    Returns the kind: 'tar', 'gz-single', 'pdf', or 'raw'.
    """
    head = blob.read_bytes()[:5]
    source_dir.mkdir(parents=True, exist_ok=True)

    if head.startswith(b"%PDF"):
        return "pdf"

    if head[:2] == b"\x1f\x8b":  # gzip magic
        # Could be a gzipped tar or a single gzipped .tex.
        try:
            with tarfile.open(blob, mode="r:gz") as tf:
                tf.extractall(source_dir, filter="data")
            return "tar"
        except (tarfile.ReadError, tarfile.TarError):
            # Single gzipped file — old arXiv single-source submissions.
            with gzip.open(blob, "rb") as fh:
                (source_dir / f"{arxiv_id.replace('/', '_')}.tex").write_bytes(
                    fh.read()
                )
            return "gz-single"

    # Some submissions are an uncompressed tar or a bare .tex.
    try:
        with tarfile.open(blob, mode="r:*") as tf:
            tf.extractall(source_dir, filter="data")
        return "tar"
    except (tarfile.ReadError, tarfile.TarError):
        (source_dir / f"{arxiv_id.replace('/', '_')}.tex").write_bytes(
            blob.read_bytes()
        )
        return "raw"


def _index_tex(source_dir: Path) -> tuple[list[str], str | None]:
    """Return (relative .tex paths, main_tex) and normalize each to UTF-8.

    Main tex = the first file containing `\\documentclass`; falls back to
    the largest `.tex` when none declares a documentclass (common in
    cited-paper fragments).
    """
    tex_paths = sorted(source_dir.rglob("*.tex"))
    rel: list[str] = []
    main: str | None = None
    biggest: tuple[int, str | None] = (-1, None)
    for p in tex_paths:
        _normalize_tex_to_utf8(p)
        r = str(p.relative_to(source_dir))
        rel.append(r)
        text = p.read_text(encoding="utf-8", errors="replace")
        if main is None and "\\documentclass" in text:
            main = r
        if p.stat().st_size > biggest[0]:
            biggest = (p.stat().st_size, r)
    if main is None:
        main = biggest[1]
    return rel, main


def fetch_one(doi: str, papers_dir: Path) -> dict[str, object]:
    """Resolve and fetch the arXiv source for one manuscript DOI."""
    slug = _doi_slug(doi)
    source_dir = papers_dir / slug / "source"

    # 1–2. Determine the eprint id.
    arxiv_id = _arxiv_id_from_doi(doi)
    resolved_source = "doi" if arxiv_id else None
    if not arxiv_id:
        arxiv_id, src = resolve_arxiv.resolve(doi)
        resolved_source = src if arxiv_id else None

    # 4. No eprint → genuinely pre-arXiv. Confirm identity via ADS.
    if not arxiv_id:
        meta = resolve_arxiv.resolve_metadata(doi)
        if meta:
            return {
                "status": "pre_arxiv",
                "arxiv_id": None,
                "resolved_source": "ads",
                "source_dir": None,
                "main_tex": None,
                "tex_files": [],
                "pdf_path": None,
                "ads_metadata": meta,
                "error": None,
                "fetched_at": _now_iso(),
            }
        return {
            "status": "unresolvable",
            "arxiv_id": None,
            "resolved_source": None,
            "source_dir": None,
            "main_tex": None,
            "tex_files": [],
            "pdf_path": None,
            "ads_metadata": None,
            "error": "no arXiv eprint and no ADS metadata for this DOI",
            "fetched_at": _now_iso(),
        }

    # 3. Fetch the e-print tarball.
    if source_dir.exists():
        shutil.rmtree(source_dir)
    with tempfile.TemporaryDirectory() as tmp:
        blob = Path(tmp) / "eprint"
        if not _curl(ARXIV_EPRINT_URL.format(id=arxiv_id), blob):
            return {
                "status": "unresolvable",
                "arxiv_id": arxiv_id,
                "resolved_source": resolved_source,
                "source_dir": None,
                "main_tex": None,
                "tex_files": [],
                "pdf_path": None,
                "ads_metadata": None,
                "error": f"arXiv e-print download failed for {arxiv_id}",
                "fetched_at": _now_iso(),
            }
        kind = _extract_source(blob, source_dir, arxiv_id)

    if kind == "pdf":
        # arXiv has only a PDF for this submission — last-resort fallback.
        shutil.rmtree(source_dir, ignore_errors=True)
        pdf_dir = papers_dir / slug
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / "paper.pdf"
        if not _curl(ARXIV_PDF_URL.format(id=arxiv_id), pdf_path):
            return {
                "status": "unresolvable",
                "arxiv_id": arxiv_id,
                "resolved_source": resolved_source,
                "source_dir": None,
                "main_tex": None,
                "tex_files": [],
                "pdf_path": None,
                "ads_metadata": None,
                "error": f"arXiv source is PDF-only and PDF download failed ({arxiv_id})",
                "fetched_at": _now_iso(),
            }
        return {
            "status": "pdf_fallback",
            "arxiv_id": arxiv_id,
            "resolved_source": resolved_source,
            "source_dir": None,
            "main_tex": None,
            "tex_files": [],
            "pdf_path": str(pdf_path),
            "ads_metadata": None,
            "error": None,
            "fetched_at": _now_iso(),
        }

    tex_files, main_tex = _index_tex(source_dir)
    if not tex_files:
        return {
            "status": "unresolvable",
            "arxiv_id": arxiv_id,
            "resolved_source": resolved_source,
            "source_dir": str(source_dir),
            "main_tex": None,
            "tex_files": [],
            "pdf_path": None,
            "ads_metadata": None,
            "error": f"source tarball for {arxiv_id} contained no .tex files",
            "fetched_at": _now_iso(),
        }

    return {
        "status": "source_fetched",
        "arxiv_id": arxiv_id,
        "resolved_source": resolved_source,
        "source_dir": str(source_dir),
        "main_tex": main_tex,
        "tex_files": tex_files,
        "pdf_path": None,
        "ads_metadata": None,
        "error": None,
        "fetched_at": _now_iso(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("work/citation-audit/ledger.json"),
        help="Ledger path (default: %(default)s)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("work/citation-audit/fetch_state.json"),
        help="Fetch state sidecar (default: %(default)s)",
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=Path("work/citation-audit/papers"),
        help="Where to extract per-paper source (default: %(default)s)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch DOIs that already have an entry in fetch_state.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of DOIs to attempt this run (debugging).",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="Fetch only this DOI (repeatable); implies --refresh for it.",
    )
    args = parser.parse_args()

    if not args.ledger.exists():
        print(f"error: ledger not found at {args.ledger}", file=sys.stderr)
        return 2
    if shutil.which("curl") is None:
        print("error: `curl` not on PATH", file=sys.stderr)
        return 2

    ledger = json.loads(args.ledger.read_text())
    rows = ledger.get("rows", [])
    unique_dois = sorted({r["doi"] for r in rows if r.get("doi")})

    state: dict[str, dict[str, object]] = {}
    if args.state.exists():
        try:
            state = json.loads(args.state.read_text())
        except json.JSONDecodeError:
            print(f"warn: {args.state} is malformed; starting fresh", file=sys.stderr)

    if args.only:
        to_fetch = [d for d in args.only if d in unique_dois or True]
    else:
        to_fetch = [d for d in unique_dois if args.refresh or d not in state]
        if args.limit:
            to_fetch = to_fetch[: args.limit]

    print(
        f"ledger has {len(unique_dois)} unique DOI(s); "
        f"{len(state)} already in fetch_state.json; "
        f"attempting {len(to_fetch)} this run."
    )

    counts: dict[str, int] = {}
    for i, doi in enumerate(to_fetch, 1):
        print(f"[{i}/{len(to_fetch)}] {doi} ...", end=" ", flush=True)
        result = fetch_one(doi, args.papers_dir)
        state[doi] = result
        status = str(result["status"])
        counts[status] = counts.get(status, 0) + 1
        if status == "source_fetched":
            print(
                f"✓ source via {result['resolved_source']} "
                f"({result['arxiv_id']}, {len(result['tex_files'])} .tex)"
            )
        elif status == "pre_arxiv":
            print(f"⊘ pre_arxiv (ADS: {(result['ads_metadata'] or {}).get('bibcode')})")
        elif status == "pdf_fallback":
            print(f"→ pdf_fallback ({result['arxiv_id']}, source PDF-only)")
        else:
            print(f"✗ {status} — {result.get('error')}")
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")

    print("\nFetch summary:")
    for status, n in sorted(counts.items()):
        print(f"  {n:>3} {status}")
    print(f"\nfetch_state.json: {args.state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
