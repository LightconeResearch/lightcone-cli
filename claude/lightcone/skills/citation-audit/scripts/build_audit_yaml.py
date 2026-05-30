#!/usr/bin/env python3
"""build_audit_yaml.py — merge Haiku verdicts into the subject's
astra.yaml as `insights:`.

Reads:
  - `work/citation-audit/ledger.json` — the per-use-site ledger built
    by `build_citation_ledger.py`, with verdicts now populated from
    the Haiku fan-out.
  - `work/reference/astra.yaml` — the stub astra.yaml paper-extraction
    wrote for the subject paper.

Writes:
  - `work/reference/astra.yaml` — same file with `insights:`
    populated. One entry per ledger row, keyed by `use_id`. Existing
    insights are preserved (idempotent — re-running adds new
    use_ids and updates existing ones).

The merge is non-destructive: any prior_insight not produced by this
skill (e.g. authored by hand, or by paper-extraction's Step 5) is left
untouched. Citation-audit's own entries are recognized by the
`tags: [citation_audit, ...]` marker.

Usage:

    python3 build_audit_yaml.py \\
        --ledger work/citation-audit/ledger.json \\
        --astra-yaml work/reference/astra.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# yaml ships with the lightcone-cli editable install; both `astra` and
# `lc` depend on it. If somehow not available, fail loudly so the user
# fixes the env rather than silently writing JSON-as-YAML.
import yaml


_AUDIT_TAG = "citation_audit"

# No-op placeholder; we no longer strip root keys. paper-extraction's
# stub uses the canonical ASTRA shape (id, narrative, findings,
# prior_insights, …) and validates cleanly against astra-tools 0.2.x.


def _now_iso() -> str:
    """Stable ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_insight(
    row: dict[str, Any],
    fetch_state: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Translate a single ledger row into an ASTRA prior_insight entry.

    Returns None for rows whose verdict is missing or `pending`
    (Haikus haven't run, or this row is unverifiable_no_doi and we're
    holding it for the report only — astra.yaml only carries rows the
    Haikus have addressed with a verdict).

    `fetch_state` (optional): the `fetch_state.json` produced by
    `fetch_papers.py`. When present, the row's `doi` (the manuscript
    DOI) is resolved to the **effective DOI** (the form ASTRA cached
    under — arxiv form for papers resolved via the shim). Without this
    resolution, `astra validate --verify-evidence` fails with "Paper
    not in cache" on any non-arxiv DOI that needed the shim.
    """
    verdict = row.get("verdict")
    if not verdict or verdict == "pending":
        return None

    # Only insights with verifiable evidence become astra.yaml entries —
    # the schema requires `evidence:` on every insight. unverifiable_*
    # / unsupported / wrong_paper verdicts stay in the ledger and the
    # report, but don't materialize as ASTRA insights (there is no
    # quote anchor to verify). The audit report is the authoritative
    # surface for those; astra.yaml only carries clean evidence.
    if verdict not in {"supported", "weak"}:
        return None

    use_id = row["use_id"]
    citation_key = row["citation_key"]
    claim = row["claim"]
    tags = [_AUDIT_TAG, f"verdict:{verdict}", f"cite_key:{citation_key}"]

    insight: dict[str, Any] = {
        "id": use_id,
        "claim": claim,
        "created_at": _now_iso(),
        "tags": tags,
    }

    # Notes are required for non-supported verdicts and may carry the
    # verifier's rationale for any verdict.
    if row.get("verdict_notes"):
        insight["notes"] = row["verdict_notes"]

    # Evidence — only populated when the Haiku returned a verified
    # quote. unsupported / wrong_paper / unverifiable_* verdicts have
    # no quote: the cite is still flagged, but there's no Evidence
    # entry to validate.
    if verdict in {"supported", "weak"} and row.get("quote"):
        # Strip the JSON-LD `type:` field — ASTRA's LinkML schema
        # infers TextQuoteSelector / FragmentSelector from position
        # and rejects an explicit `type:` as "extra inputs not
        # permitted." Haikus emit `type:` because the verifier
        # contract uses W3C Annotation conventions; we normalize on
        # the way out.
        quote = {k: v for k, v in (row.get("quote") or {}).items() if k != "type"}
        # Resolve manuscript DOI → effective DOI (the form ASTRA
        # cached under). For papers whose journal-DOI Unpaywall fetch
        # failed, the shim resolved an arXiv preprint and ASTRA
        # cached it under `10.48550/arXiv.<id>`; using row["doi"]
        # (the manuscript form) would miss the cache.
        manuscript_doi = row["doi"]
        effective_doi = manuscript_doi
        if fetch_state:
            entry = fetch_state.get(manuscript_doi) or {}
            effective_doi = entry.get("effective_doi") or manuscript_doi
        ev: dict[str, Any] = {
            "id": "ev1",
            "doi": effective_doi,
            "quote": quote,
        }
        if row.get("location"):
            ev["location"] = {
                k: v for k, v in row["location"].items() if k != "type"
            }
        if row.get("version"):
            ev["version"] = row["version"]
        insight["evidence"] = [ev]
    return insight


def merge_haiku_outputs_into_ledger(
    ledger_path: Path, haiku_dir: Path
) -> int:
    """Read every `haiku-*.yaml` in `haiku_dir`, patch verdicts into the
    ledger at `ledger_path`, write the updated ledger back.

    Returns the number of verdicts applied. Idempotent: re-running over
    the same files overwrites with the latest verdict (Haikus are the
    source of truth for verdict content).
    """
    ledger = json.loads(ledger_path.read_text())
    rows: list[dict[str, Any]] = ledger.get("rows", [])
    by_id = {r["use_id"]: r for r in rows}

    applied = 0
    for haiku_path in sorted(haiku_dir.glob("haiku-*.yaml")):
        try:
            haiku = yaml.safe_load(haiku_path.read_text()) or {}
        except yaml.YAMLError as exc:
            print(f"warn: skipping {haiku_path}: {exc}", file=sys.stderr)
            continue
        verdicts = haiku.get("verdicts") or {}
        for use_id, v in verdicts.items():
            row = by_id.get(use_id)
            if row is None:
                print(
                    f"warn: {haiku_path.name} carries verdict for unknown "
                    f"use_id {use_id} (not in ledger); skipping",
                    file=sys.stderr,
                )
                continue
            verdict = v.get("verdict")
            if not verdict:
                continue
            row["verdict"] = verdict
            row["verdict_notes"] = v.get("notes")
            row["quote"] = v.get("quote")
            row["location"] = v.get("location")
            row["suggested_rewording"] = v.get("suggested_rewording")
            applied += 1

    # Re-compute summary so re-runs reflect the new verdict mix.
    summary = ledger.get("summary") or {}
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("verdict") or "pending"] = (
            counts.get(r.get("verdict") or "pending", 0) + 1
        )
    summary["verdicts"] = counts
    ledger["summary"] = summary

    ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")
    return applied


def merge_ledger_into_astra(
    ledger_path: Path,
    astra_path: Path,
    fetch_state_path: Path | None = None,
) -> tuple[int, int, int]:
    """Merge the ledger's verdicts into astra.yaml's insights.

    Returns `(added, updated, untouched)` — counts for the audit-tagged
    entries this call touched. Non-audit insights are never
    counted (they're left as-is).
    """
    ledger = json.loads(ledger_path.read_text())
    rows: list[dict[str, Any]] = ledger.get("rows", [])

    fetch_state: dict[str, dict[str, Any]] = {}
    if fetch_state_path and fetch_state_path.is_file():
        try:
            fetch_state = json.loads(fetch_state_path.read_text())
        except json.JSONDecodeError:
            print(
                f"warn: {fetch_state_path} is malformed; "
                "evidence DOIs will use manuscript form (cache misses likely)",
                file=sys.stderr,
            )

    astra = yaml.safe_load(astra_path.read_text()) or {}
    prior_insights = astra.setdefault("prior_insights", {}) or {}

    # First, purge audit-tagged insights from any previous run. We
    # rebuild from the current ledger; stale audit insights (e.g.
    # downgraded from `supported` to `unverifiable` on a re-run, which
    # no longer carry evidence and would fail validation) must not
    # linger.
    for uid in list(prior_insights):
        existing = prior_insights[uid] or {}
        if _AUDIT_TAG in (existing.get("tags") or []):
            del prior_insights[uid]

    added = 0
    updated = 0

    for row in rows:
        insight = _row_to_insight(row, fetch_state)
        if insight is None:
            continue
        uid = insight["id"]
        if uid in prior_insights:
            existing = prior_insights[uid] or {}
            tags = existing.get("tags") or []
            if _AUDIT_TAG in tags:
                # Overwrite our own previous version
                prior_insights[uid] = insight
                updated += 1
            # else: not ours — leave it alone (could be hand-authored).
        else:
            prior_insights[uid] = insight
            added += 1

    astra["prior_insights"] = prior_insights

    # Re-emit preserving order where we can. PyYAML's safe_dump with
    # sort_keys=False keeps the order of insertion for dicts. Use
    # block style throughout for readability and `astra validate`
    # compatibility.
    astra_path.write_text(
        yaml.safe_dump(
            astra,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=1_000_000,  # don't wrap quote-anchored strings
        )
    )

    untouched = sum(
        1
        for v in prior_insights.values()
        if (v or {}).get("tags") and _AUDIT_TAG not in ((v or {}).get("tags") or [])
    )
    return added, updated, untouched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("work/citation-audit/ledger.json"),
        help="Ledger path (default: %(default)s)",
    )
    parser.add_argument(
        "--astra-yaml",
        type=Path,
        default=Path("work/reference/astra.yaml"),
        help="Target astra.yaml (default: %(default)s)",
    )
    parser.add_argument(
        "--haiku-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing `haiku-*.yaml` worker outputs to merge "
            "into the ledger before materializing. Default: the ledger's "
            "parent directory."
        ),
    )
    parser.add_argument(
        "--fetch-state",
        type=Path,
        default=Path("work/citation-audit/fetch_state.json"),
        help=(
            "Path to fetch_state.json from fetch_papers.py. Used to "
            "resolve evidence DOIs to the effective form ASTRA cached "
            "under (default: %(default)s)."
        ),
    )
    args = parser.parse_args()

    if not args.ledger.exists():
        print(f"error: ledger not found at {args.ledger}", file=sys.stderr)
        return 2
    if not args.astra_yaml.exists():
        print(
            f"error: astra.yaml not found at {args.astra_yaml} — run paper-extraction first",
            file=sys.stderr,
        )
        return 2

    haiku_dir = args.haiku_dir or args.ledger.parent
    if haiku_dir.exists():
        applied = merge_haiku_outputs_into_ledger(args.ledger, haiku_dir)
        print(f"merged {applied} verdict(s) from {haiku_dir}/haiku-*.yaml into ledger")

    added, updated, untouched = merge_ledger_into_astra(
        args.ledger, args.astra_yaml, fetch_state_path=args.fetch_state
    )
    print(
        f"materialized: {added} new prior_insight(s), {updated} updated; "
        f"{untouched} non-audit insight(s) left untouched"
    )
    print("next: cd work/reference && astra validate astra.yaml --verify-evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
