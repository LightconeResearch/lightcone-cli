#!/usr/bin/env python3
"""fetch_papers.py — fetch every cited paper in the ledger into ASTRA's cache.

Step 2 of the citation-audit pipeline. Walks unique DOIs in
`ledger.json`, fetches each into the `astra paper` cache, and records
the outcome in a sidecar `fetch_state.json` the orchestrator joins
against when building Haiku partitions.

The fetch chain:

  1. `astra paper get <doi>` — if cached, done.
  2. `astra paper add <doi>` — works natively for arXiv DOIs;
      for journal DOIs, ASTRA calls Unpaywall.
  3. On Unpaywall failure (A&A 403, paywalled journals): run
     `resolve_arxiv.py` to get an arXiv eprint from verifiable metadata
     (ADS `identifier[]` then Crossref `relation.has-preprint`), then
     `astra paper add 10.48550/arXiv.<id>` via the arXiv pipeline.

The shim in step 3 is a stopgap until LightconeResearch/astra-tools#90
lands — once `astra paper add` has its own arXiv fallback, this script
collapses to a single `astra paper add` per DOI.

`fetch_state.json` is keyed by the manuscript's journal DOI (i.e. the
form found in the .bib and used in \\cite{}). Each entry carries:

    {
      "status": "cached_native" | "fetched_native"
                | "fetched_via_arxiv" | "unresolvable",
      "effective_doi": "<DOI ASTRA cached under>",
      "pdf_path": "<path from `astra paper path`>",
      "resolved_source": "ads" | "crossref" | null,
      "error": "<string, only when status=unresolvable>",
      "fetched_at": "<iso8601 utc>"
    }

The orchestrator uses `effective_doi` and `pdf_path` when building each
Haiku partition; the manuscript-facing `doi` stays as-is in the ledger
(matching the .bib).

Idempotent — re-runs skip DOIs already in `fetch_state.json` unless
`--refresh` is given.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SHIM = Path(__file__).resolve().parent / "resolve_arxiv.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run a subprocess; return (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def _astra_get(doi: str) -> str | None:
    """Return the cached PDF path if the DOI is already in the cache, else None."""
    rc, out, _ = _run(["astra", "paper", "path", doi])
    if rc == 0:
        path = out.strip()
        if path and Path(path).is_file():
            return path
    return None


def _astra_add(doi: str) -> tuple[bool, str]:
    """Try `astra paper add <doi>`. Return (success, error_message)."""
    rc, _, err = _run(["astra", "paper", "add", doi], timeout=120)
    if rc == 0:
        return True, ""
    return False, (err or "").strip()


def _resolve_arxiv(doi: str) -> tuple[str | None, str | None]:
    """Run the shim. Return (arxiv_eprint_id, source) or (None, None)."""
    rc, out, _ = _run(["python3", str(SHIM), "--doi", doi, "--json"])
    if rc != 0:
        return None, None
    try:
        payload = json.loads(out)
        return payload.get("arxiv_id"), payload.get("source")
    except json.JSONDecodeError:
        return None, None


def fetch_one(doi: str) -> dict[str, object]:
    """Resolve the full fetch chain for one journal DOI."""
    # 1. Already cached?
    cached = _astra_get(doi)
    if cached:
        return {
            "status": "cached_native",
            "effective_doi": doi,
            "pdf_path": cached,
            "resolved_source": None,
            "error": None,
            "fetched_at": _now_iso(),
        }

    # 2. Try native `astra paper add`.
    ok, err = _astra_add(doi)
    if ok:
        path = _astra_get(doi)
        return {
            "status": "fetched_native",
            "effective_doi": doi,
            "pdf_path": path,
            "resolved_source": None,
            "error": None,
            "fetched_at": _now_iso(),
        }

    # 3. Fall back via the arxiv shim.
    eprint, source = _resolve_arxiv(doi)
    if not eprint:
        return {
            "status": "unresolvable",
            "effective_doi": None,
            "pdf_path": None,
            "resolved_source": None,
            "error": f"native fetch failed ({err}); no arxiv preprint resolvable",
            "fetched_at": _now_iso(),
        }

    arxiv_doi = f"10.48550/arXiv.{eprint}"
    # Maybe already cached under the arxiv DOI from a previous run.
    cached_arxiv = _astra_get(arxiv_doi)
    if cached_arxiv:
        return {
            "status": "fetched_via_arxiv",
            "effective_doi": arxiv_doi,
            "pdf_path": cached_arxiv,
            "resolved_source": source,
            "error": None,
            "fetched_at": _now_iso(),
        }

    ok2, err2 = _astra_add(arxiv_doi)
    if not ok2:
        return {
            "status": "unresolvable",
            "effective_doi": arxiv_doi,
            "pdf_path": None,
            "resolved_source": source,
            "error": (
                f"native fetch failed ({err}); resolved arxiv {eprint} but "
                f"astra paper add {arxiv_doi} also failed: {err2}"
            ),
            "fetched_at": _now_iso(),
        }
    return {
        "status": "fetched_via_arxiv",
        "effective_doi": arxiv_doi,
        "pdf_path": _astra_get(arxiv_doi),
        "resolved_source": source,
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
    args = parser.parse_args()

    if not args.ledger.exists():
        print(f"error: ledger not found at {args.ledger}", file=sys.stderr)
        return 2

    if shutil.which("astra") is None:
        print("error: `astra` CLI not on PATH", file=sys.stderr)
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
        result = fetch_one(doi)
        state[doi] = result
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        if result["status"] == "unresolvable":
            print(f"✗ {result['status']} — {result.get('error')}")
        elif result["status"] == "fetched_via_arxiv":
            print(
                f"→ {result['status']} via {result.get('resolved_source')}: "
                f"{result.get('effective_doi')}"
            )
        else:
            print(f"✓ {result['status']}")
        # Flush state after every fetch so a crash mid-run preserves progress.
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")

    print("\nFetch summary:")
    for status, n in sorted(counts.items()):
        print(f"  {n:>3} {status}")
    print(f"\nfetch_state.json: {args.state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
