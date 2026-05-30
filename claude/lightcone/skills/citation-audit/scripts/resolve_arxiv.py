#!/usr/bin/env python3
"""resolve_arxiv.py — given a journal DOI, find a verifiable arXiv eprint.

When `astra paper add <journal_doi>` fails (Unpaywall 403 on A&A papers,
paywalled OUP DOIs, etc.) but a preprint exists on arXiv, this resolver
finds the eprint ID via verifiable metadata — NOT by LLM judgment, and
NOT by guessing from titles. The link must come from one of:

  1. NASA ADS — bibcode lookup by DOI returns the `eprint` field
     directly when ADS has cross-indexed the paper. Most reliable for
     astronomy/astrophysics. Requires ADS_API_TOKEN env var or
     ~/.ads/dev_key.
  2. Crossref — the work record's `relation.has-preprint` field, when
     publishers register the preprint link.

The output is **only** the arXiv eprint ID (e.g. "0809.5035",
"astro-ph/0207454") plus a `source` tag identifying which API surfaced
the link. No "best guess" matches. If neither service has the link,
the resolver returns None and the orchestrator falls back to
`unverifiable_fetch_failed` for that citation.

# Prototype status

This is a prototype attached to a proposed ASTRA enhancement
(see lc-feedback issue: "astra paper add: arxiv fallback + provenance").
The eventual home for this resolver is upstream in `astra paper resolve`
(separate concern from fetching), so any caller can do:

    astra paper resolve <journal_doi>     # returns arxiv eprint or "none"
    astra paper add <journal_doi>          # automatically falls back

Until that lands, the citation-audit orchestrator imports this script
to get the same behavior at the skill level.

Usage:

    resolve_arxiv.py --doi 10.1051/0004-6361/200811054
    # → 0809.5035  ads  (printed on stdout)

    resolve_arxiv.py --doi 10.1086/171151
    # → (no output, exit code 1) — no arxiv preprint in either API
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal

CROSSREF_API = "https://api.crossref.org/works"
ADS_API = "https://api.adsabs.harvard.edu/v1/search/query"
USER_AGENT = "lightcone-citation-audit/0.1 (mailto:noreply@anthropic.com)"
HTTP_TIMEOUT = 20

# arXiv ID forms:
#   - new-style:  2604.03227  (YYMM.NNNNN, optional vN)
#   - old-style:  astro-ph/0207454 (or any 2-3-letter subject class)
_ARXIV_ID_RE = re.compile(
    r"^(?:arXiv:)?(?P<id>(?:\d{4}\.\d{4,5})|(?:[a-z\-]+/\d{7}))(?:v\d+)?$",
    re.IGNORECASE,
)


def _load_ads_key() -> str | None:
    """Mirror paper-extraction's ADS key loading."""
    env = os.environ.get("ADS_API_TOKEN") or os.environ.get("ADS_DEV_KEY")
    if env:
        return env.strip()
    for path in (
        Path.home() / ".ads" / "dev_key",
        Path.home() / ".config" / "ads" / "dev_key",
    ):
        if path.is_file():
            try:
                return path.read_text().strip() or None
            except OSError:
                pass
    return None


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_eprint(raw: str) -> str | None:
    """Strip URL/version/DOI prefixes; return canonical arXiv ID or None.

    Handles:
      - arXiv:0809.5035
      - arXiv:astro-ph/0207454
      - https://arxiv.org/abs/0809.5035
      - 10.48550/arXiv.0809.5035
      - 10.48550/arXiv.astro-ph/0207454
    """
    s = raw.strip()
    # Strip URL prefix variants
    s = re.sub(
        r"^(?:https?://(?:www\.)?arxiv\.org/(?:abs|pdf|e-print)/)",
        "",
        s,
        flags=re.IGNORECASE,
    )
    # Strip arXiv DOI prefix (the 10.48550/arXiv. form ADS sometimes emits)
    s = re.sub(r"^10\.48550/arXiv\.", "", s, flags=re.IGNORECASE)
    # Strip .pdf suffix (e.g. from PDF URLs)
    s = re.sub(r"\.pdf$", "", s, flags=re.IGNORECASE)
    m = _ARXIV_ID_RE.match(s)
    return m.group("id") if m else None


def resolve_via_ads(doi: str, api_key: str) -> str | None:
    """Look up the arXiv eprint via NASA ADS, given a journal DOI.

    Most reliable for astronomy. ADS returns an `identifier` array
    containing both the journal DOI and the arXiv eprint (in `arXiv:...`
    and `10.48550/arXiv....` forms) when the cross-link is known.
    """
    params = {"q": f'doi:"{doi}"', "fl": "identifier,bibcode", "rows": "1"}
    url = f"{ADS_API}?{urllib.parse.urlencode(params)}"
    try:
        data = _http_get_json(url, headers={"Authorization": f"Bearer {api_key}"})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    docs = ((data or {}).get("response", {}) or {}).get("docs", []) or []
    if not docs:
        return None
    identifiers = docs[0].get("identifier") or []
    # Scan for arXiv-flavored entries. ADS surfaces them in two forms:
    #   "arXiv:<id>" and "10.48550/arXiv.<id>". Either is fine — both
    #   normalize to the canonical eprint.
    for ident in identifiers:
        if not isinstance(ident, str):
            continue
        if ident.lower().startswith("arxiv:") or "arxiv." in ident.lower():
            normalized = _normalize_eprint(ident)
            if normalized:
                return normalized
    return None


def resolve_via_crossref(doi: str) -> str | None:
    """Look up the arXiv eprint via Crossref's `relation.has-preprint` field.

    Less reliable than ADS — many publishers don't register preprint
    relations — but no API key needed. Useful fallback when ADS doesn't
    have the cross-index.
    """
    url = f"{CROSSREF_API}/{urllib.parse.quote(doi, safe='')}"
    try:
        data = _http_get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    msg = (data or {}).get("message") or {}
    relations = msg.get("relation") or {}
    # `has-preprint` is a list of {id, id-type, asserted-by}
    candidates = relations.get("has-preprint", []) or []
    for rel in candidates:
        if not isinstance(rel, dict):
            continue
        id_type = (rel.get("id-type") or "").lower()
        rid = rel.get("id") or ""
        if id_type == "arxiv" or "arxiv" in rid.lower():
            normalized = _normalize_eprint(rid)
            if normalized:
                return normalized
    return None


def resolve(doi: str) -> tuple[str | None, Literal["ads", "crossref", "unresolved"]]:
    """Return `(arxiv_id_or_none, source_tag)` for the given journal DOI.

    Tries ADS first (more reliable for astronomy), then Crossref. Both
    are verifiable metadata sources — no LLM judgment, no title-match
    inference.
    """
    api_key = _load_ads_key()
    if api_key:
        eprint = resolve_via_ads(doi, api_key)
        if eprint:
            return eprint, "ads"
    eprint = resolve_via_crossref(doi)
    if eprint:
        return eprint, "crossref"
    return None, "unresolved"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--doi",
        required=True,
        help="The journal DOI to resolve (e.g. 10.1051/0004-6361/200811054).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of plaintext (machine-friendly).",
    )
    args = parser.parse_args()

    eprint, source = resolve(args.doi)

    if args.json:
        print(json.dumps({"doi": args.doi, "arxiv_id": eprint, "source": source}))
    elif eprint:
        print(f"{eprint}  {source}")
    else:
        print(f"no arxiv preprint found for {args.doi}", file=sys.stderr)

    return 0 if eprint else 1


if __name__ == "__main__":
    sys.exit(main())
