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
     first two are `source_fetched` (`backend: tex`); a PDF is `pdf`
     (`backend: pdf`, `pdf_source: arxiv`).
  4. No eprint id: fetch the **PDF from the ADS link gateway by bibcode**
     (`resolve_arxiv.resolve_metadata` → bibcode → ADS_PDF route) and
     record `pdf` (`backend: pdf`, `pdf_source: ads`). The verifier
     anchors English-narrative quotes flagged `substrate: pdf`; the gate
     re-checks them with a fuzzy/normalized match. If ADS has no bibcode
     (or the gateway fetch fails), the cite is `unresolvable`. PDF is a
     fetch **backend**, never a verdict — there is no `pre_arxiv` mode.

A user-pre-placed `papers/<slug>/paper.pdf` is honored as a `pdf` backend
(for paywalled papers fetched by hand).

All `.tex` files are normalized to UTF-8 on extraction (the Heymans
1210.0032 trap: ISO-8859-1 with very long single lines — a naive
UTF-8 grep returns zero hits). The verifier then never has to know
about encoding.

fetch_state.json entry shape
----------------------------
    {
      "status": "source_fetched" | "pdf" | "unresolvable",
      "backend": "tex" | "pdf" | "none",  # the axis the verifier reads
      "arxiv_id": "1210.0032" | null,
      "resolved_source": "doi" | "ads" | "crossref" | "arxiv"
                | "preplaced" | null,
      "source_dir": "<rel path to papers/<slug>/source>" | null,
      "main_tex": "<filename of \\documentclass file>" | null,
      "tex_files": ["a.tex", "sec/b.tex", ...],
      "pdf_path": "<path>" | null,         # backend: pdf
      "pdf_source": "arxiv" | "ads" | "preplaced" | null,  # backend: pdf
      "ads_metadata": {...} | null,        # the ADS-gateway / pre-arXiv path
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
# The ADS link gateway resolves a bibcode to the publisher/ADS-hosted PDF. The
# `ADS_PDF` route is the one that works headless; the publisher direct links
# (OUP, journal) 403 behind Cloudflare. Used for papers with no arXiv eprint.
ADS_GATEWAY_PDF_URL = "https://ui.adsabs.harvard.edu/link_gateway/{bibcode}/ADS_PDF"
HTTP_TIMEOUT = 90
PDF_RETRIES = 3  # the ADS gateway 504s transiently; retry a couple of times


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


def _curl(url: str, dest: Path, retries: int = 1) -> bool:
    """Download `url` to `dest` with curl. Return True on success.

    `retries` > 1 re-attempts on failure (the ADS gateway 504s transiently).
    A browser-ish User-Agent keeps Cloudflare-fronted hosts from 403-ing.
    """
    for attempt in range(1, retries + 1):
        proc = subprocess.run(
            [
                "curl", "-sL", "--fail", "--retry", "2",
                "-A", "Mozilla/5.0 (lightcone-citation-audit)",
                "-o", str(dest), url,
            ],
            capture_output=True,
            text=True,
            timeout=HTTP_TIMEOUT,
            check=False,
        )
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True
    return False


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


def _entry(**kw: object) -> dict[str, object]:
    """A fetch_state entry with every key present and a derived `backend`.

    `backend` is the axis the verifier reads: `tex` (arXiv LaTeX source),
    `pdf` (a fetched PDF — arXiv-PDF-only or ADS-gateway), or `none`
    (unresolvable). Pass `status` and the relevant fields; the rest default.
    """
    status = kw.get("status")
    backend = {"source_fetched": "tex", "pdf": "pdf"}.get(str(status), "none")
    base: dict[str, object] = {
        "status": status,
        "backend": backend,
        "arxiv_id": None,
        "resolved_source": None,
        "source_dir": None,
        "main_tex": None,
        "tex_files": [],
        "pdf_path": None,
        "pdf_source": None,   # "arxiv" | "ads" for backend: pdf
        "ads_metadata": None,
        "error": None,
        "fetched_at": _now_iso(),
    }
    base.update(kw)
    return base


def fetch_one(
    doi: str,
    papers_dir: Path,
    eprint_hint: str | None = None,
    bibcode_hint: str | None = None,
) -> dict[str, object]:
    """Resolve and fetch the cited paper's source for one manuscript DOI.

    One read substrate per paper, chosen mechanically: arXiv LaTeX when an
    eprint exists (`backend: tex`), otherwise a PDF (`backend: pdf`) — the
    arXiv PDF when the submission is PDF-only, or the ADS-gateway PDF by
    bibcode when no eprint exists at all. A user-pre-placed
    `papers/<slug>/paper.pdf` is honored as a `pdf` backend.

    `eprint_hint` / `bibcode_hint` come from the manuscript `.bib` (ADS exports
    carry `eprint` + `adsurl`). They are tried FIRST — the skill's contract is to
    trust the `.bib`'s own eprint, and it avoids a fragile DOI->ADS round-trip
    (and the need for an ADS API token) for the common case.
    """
    slug = _doi_slug(doi)
    source_dir = papers_dir / slug / "source"
    preplaced_pdf = papers_dir / slug / "paper.pdf"

    # 1–2. Determine the eprint id: arXiv-DOI, then the .bib's own eprint, then
    # verifiable-metadata resolution (ADS identifier[] / Crossref has-preprint).
    arxiv_id = _arxiv_id_from_doi(doi)
    resolved_source = "doi" if arxiv_id else None
    if not arxiv_id and eprint_hint:
        arxiv_id, resolved_source = eprint_hint, "bib_eprint"
    if not arxiv_id:
        arxiv_id, src = resolve_arxiv.resolve(doi)
        resolved_source = src if arxiv_id else None

    # 4. No eprint → fetch the PDF from the ADS link gateway by bibcode.
    # ADS metadata gives us the bibcode (and confirms the paper's identity);
    # the verifier then anchors English-narrative quotes flagged `substrate: pdf`.
    if not arxiv_id:
        # Prefer the .bib's own bibcode (from adsurl); only hit the ADS API when
        # the bib didn't supply one.
        meta = None
        bibcode = bibcode_hint
        bibcode_source = "bib_bibcode" if bibcode_hint else None
        if not bibcode:
            meta = resolve_arxiv.resolve_metadata(doi)
            bibcode = (meta or {}).get("bibcode")
            bibcode_source = "ads" if bibcode else None
        # Honor a user-pre-placed PDF first.
        if preplaced_pdf.is_file() and preplaced_pdf.stat().st_size > 0:
            return _entry(status="pdf", resolved_source="preplaced",
                          pdf_path=str(preplaced_pdf), pdf_source="preplaced",
                          ads_metadata=meta)
        if bibcode:
            preplaced_pdf.parent.mkdir(parents=True, exist_ok=True)
            if _curl(ADS_GATEWAY_PDF_URL.format(bibcode=bibcode), preplaced_pdf,
                     retries=PDF_RETRIES) and preplaced_pdf.read_bytes()[:5].startswith(b"%PDF"):
                return _entry(status="pdf", resolved_source=bibcode_source,
                              pdf_path=str(preplaced_pdf), pdf_source="ads",
                              ads_metadata=meta)
            return _entry(status="unresolvable", resolved_source=bibcode_source, ads_metadata=meta,
                          error=f"no arXiv eprint; ADS-gateway PDF fetch failed for {bibcode}")
        return _entry(status="unresolvable",
                      error="no arXiv eprint and no ADS metadata for this DOI")

    # 3. Fetch the e-print tarball.
    if source_dir.exists():
        shutil.rmtree(source_dir)
    with tempfile.TemporaryDirectory() as tmp:
        blob = Path(tmp) / "eprint"
        if not _curl(ARXIV_EPRINT_URL.format(id=arxiv_id), blob):
            return _entry(status="unresolvable", arxiv_id=arxiv_id,
                          resolved_source=resolved_source,
                          error=f"arXiv e-print download failed for {arxiv_id}")
        kind = _extract_source(blob, source_dir, arxiv_id)

    if kind == "pdf":
        # arXiv has only a PDF for this submission — read it as a pdf backend.
        shutil.rmtree(source_dir, ignore_errors=True)
        preplaced_pdf.parent.mkdir(parents=True, exist_ok=True)
        if not _curl(ARXIV_PDF_URL.format(id=arxiv_id), preplaced_pdf):
            return _entry(status="unresolvable", arxiv_id=arxiv_id,
                          resolved_source=resolved_source,
                          error=f"arXiv source is PDF-only and PDF download failed ({arxiv_id})")
        return _entry(status="pdf", arxiv_id=arxiv_id, resolved_source=resolved_source,
                      pdf_path=str(preplaced_pdf), pdf_source="arxiv")

    tex_files, main_tex = _index_tex(source_dir)
    if not tex_files:
        return _entry(status="unresolvable", arxiv_id=arxiv_id,
                      resolved_source=resolved_source, source_dir=str(source_dir),
                      error=f"source tarball for {arxiv_id} contained no .tex files")

    return _entry(status="source_fetched", arxiv_id=arxiv_id,
                  resolved_source=resolved_source, source_dir=str(source_dir),
                  main_tex=main_tex, tex_files=tex_files)


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
    # Per-DOI fetch hints straight from the .bib (eprint, bibcode), tried before
    # any DOI->ADS resolution. First row per DOI wins (all rows share the entry).
    hints: dict[str, dict[str, str | None]] = {}
    for r in rows:
        d = r.get("doi")
        if d and d not in hints:
            hints[d] = {"eprint": r.get("eprint"), "bibcode": r.get("bibcode")}

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
        hint = hints.get(doi, {})
        result = fetch_one(doi, args.papers_dir,
                           eprint_hint=hint.get("eprint"), bibcode_hint=hint.get("bibcode"))
        state[doi] = result
        status = str(result["status"])
        counts[status] = counts.get(status, 0) + 1
        if status == "source_fetched":
            print(
                f"✓ source via {result['resolved_source']} "
                f"({result['arxiv_id']}, {len(result['tex_files'])} .tex)"
            )
        elif status == "pdf":
            print(f"→ pdf via {result['pdf_source']} ({result.get('arxiv_id') or (result['ads_metadata'] or {}).get('bibcode')})")
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
