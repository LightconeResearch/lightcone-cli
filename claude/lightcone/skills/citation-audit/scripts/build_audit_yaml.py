#!/usr/bin/env python3
"""build_audit_yaml.py — merge verifier verdicts into the subject's
astra.yaml as `insights:`.

Reads:
  - `work/citation-audit/ledger.json` — the per-use-site ledger built
    by `build_citation_ledger.py`, with verdicts now populated from
    the verifier fan-out.
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


def _anchors_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the row's anchor list, normalizing a legacy single `quote`.

    The current schema stores `row["anchors"]` — a list of one quote per
    facet (W3C TextQuoteSelector). Older ledgers (and the originally-
    `supported` rows from a pre-migration run) carry a single `row["quote"]`
    plus `row["location"]`; normalize those to a 1-element anchors list so
    the materializer has one code path.
    """
    anchors = row.get("anchors")
    if anchors:
        return anchors
    quote = row.get("quote")
    if quote and isinstance(quote, dict) and quote.get("exact"):
        loc = row.get("location") or {}
        return [
            {
                "exact": quote.get("exact"),
                "prefix": quote.get("prefix"),
                "suffix": quote.get("suffix"),
                "section": loc.get("section") or loc.get("value"),
                "substrate": "tex",
            }
        ]
    return []


def _row_to_insight(
    row: dict[str, Any],
    fetch_state: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Translate a single ledger row into an ASTRA prior_insight entry.

    One verification path: a `supported`/`weak` row carries 1..N anchors
    (one verbatim quote per facet of the claim) and materializes as an
    insight whose `evidence:` is the list `[ev1 .. evN]`. Every other
    verdict — `unsupported`, `wrong_paper`, `unverifiable`, the pre-verify
    ledger states (`pending`, `unverifiable_no_doi`, `extraction_error`) —
    stays in the ledger and the report but does NOT materialize as an
    ASTRA insight: the schema requires verified `evidence:` on every
    insight, and these rows have no anchorable quote. The report is the
    authoritative surface for them; astra.yaml carries only clean evidence.

    `fetch_state` (optional): the `fetch_state.json` produced by
    `fetch_sources.py`. Used to resolve the manuscript (.bib) DOI to the
    `effective_doi` ASTRA cached the source under, when they differ.
    """
    verdict = row.get("verdict")
    if verdict not in {"supported", "weak"}:
        return None

    anchors = _anchors_of(row)
    if not anchors:
        # A supported/weak verdict with no anchor cannot be materialized —
        # there is nothing to verify. The gate downgrades such rows to
        # `unverifiable`; if one slips through, skip it rather than emit an
        # evidence-less insight that fails structural validation.
        print(
            f"warn: {row.get('use_id')} is `{verdict}` but has no anchor; "
            f"skipping materialization (the gate should have downgraded it)",
            file=sys.stderr,
        )
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
    if row.get("verdict_notes"):
        insight["notes"] = row["verdict_notes"]

    # Resolve manuscript DOI → effective DOI (the form ASTRA cached the
    # source under). For a paper whose journal-DOI fetch failed, the shim
    # resolved an arXiv preprint and ASTRA cached it under
    # `10.48550/arXiv.<id>`; the manuscript form would miss the cache.
    manuscript_doi = row.get("doi")
    effective_doi = manuscript_doi
    if fetch_state and manuscript_doi:
        entry = fetch_state.get(manuscript_doi) or {}
        effective_doi = entry.get("effective_doi") or manuscript_doi

    # Multi-anchor → evidence list, one entry per facet. ASTRA's LinkML
    # schema infers TextQuoteSelector / FragmentSelector from position and
    # rejects an explicit `type:`, so we emit only exact/prefix/suffix and a
    # free-string `location.value` (the source-based verifier locates by
    # `section`/`\label`, not a PDF page).
    evidence: list[dict[str, Any]] = []
    for i, anchor in enumerate(anchors, 1):
        exact = anchor.get("exact")
        if not exact:
            continue
        quote = {
            k: anchor[k]
            for k in ("exact", "prefix", "suffix")
            if anchor.get(k)
        }
        ev: dict[str, Any] = {"id": f"ev{i}", "doi": effective_doi, "quote": quote}
        section = anchor.get("section")
        if section:
            ev["location"] = {"value": section}
        evidence.append(ev)
    if not evidence:
        print(
            f"warn: {use_id} is `{verdict}` but no anchor had an `exact`; skipping",
            file=sys.stderr,
        )
        return None
    insight["evidence"] = evidence
    return insight


def _apply_verdict(row: dict[str, Any], v: dict[str, Any]) -> bool:
    """Patch one verifier verdict object onto its ledger row.

    Stores the multi-anchor `anchors:` list (one quote per facet) and clears
    any legacy single `quote`/`location` so the row has a single shape. Returns
    True if a verdict was applied.
    """
    verdict = v.get("verdict")
    if not verdict:
        return False
    row["verdict"] = verdict
    row["verdict_notes"] = v.get("notes")
    row["anchors"] = v.get("anchors") or []
    row["suggested_rewording"] = v.get("suggested_rewording")
    if v.get("doi_flag"):
        row["doi_flag"] = v["doi_flag"]
    # Drop the pre-migration single-quote fields so downstream reads one shape.
    row.pop("quote", None)
    row.pop("location", None)
    return True


def _recompute_summary(ledger: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    summary = ledger.get("summary") or {}
    counts: dict[str, int] = {}
    for r in rows:
        key = r.get("verdict") or "pending"
        counts[key] = counts.get(key, 0) + 1
    summary["verdicts"] = counts
    ledger["summary"] = summary


def merge_verdicts_into_ledger(ledger_path: Path, verdicts_path: Path) -> int:
    """Merge the workflow's `verdicts.json` (one object per use-site) into the
    ledger at `ledger_path`, joining by `use_id`. Writes the ledger back.

    This is the deep-research-sibling merge surface: the verifier fan-out
    collects multi-anchor verdicts in memory and the workflow writes them as a
    single flat JSON list (or `{"verdicts": [...]}`). Idempotent — re-running
    overwrites each row with the latest verdict.
    """
    ledger = json.loads(ledger_path.read_text())
    rows: list[dict[str, Any]] = ledger.get("rows", [])
    by_id = {r["use_id"]: r for r in rows}

    doc = json.loads(verdicts_path.read_text())
    verdicts = doc.get("verdicts", []) if isinstance(doc, dict) else doc

    applied = 0
    for v in verdicts:
        use_id = v.get("use_id")
        row = by_id.get(use_id)
        if row is None:
            print(
                f"warn: verdict for unknown use_id {use_id} (not in ledger); skipping",
                file=sys.stderr,
            )
            continue
        if _apply_verdict(row, v):
            applied += 1

    _recompute_summary(ledger, rows)
    ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")
    return applied


def merge_worker_outputs_into_ledger(
    ledger_path: Path, worker_dir: Path
) -> int:
    """Read every `verifier-*.yaml` in `worker_dir`, patch verdicts into
    the ledger at `ledger_path`, write the updated ledger back.

    The per-worker-YAML path (one file per partition, each a
    `verdicts: {use_id: {...}}` map). Prefer `merge_verdicts_into_ledger`
    (the flat `verdicts.json` the workflow emits); this stays for orchestrators
    that fan out into sidecar YAML files. Idempotent.
    """
    ledger = json.loads(ledger_path.read_text())
    rows: list[dict[str, Any]] = ledger.get("rows", [])
    by_id = {r["use_id"]: r for r in rows}

    applied = 0
    # Accept the legacy `haiku-*.yaml` filename alongside `verifier-*.yaml`
    # so old work dirs still merge.
    worker_files = sorted(
        {*worker_dir.glob("verifier-*.yaml"), *worker_dir.glob("haiku-*.yaml")}
    )
    for worker_path in worker_files:
        try:
            worker_out = yaml.safe_load(worker_path.read_text()) or {}
        except yaml.YAMLError as exc:
            print(f"warn: skipping {worker_path}: {exc}", file=sys.stderr)
            continue
        verdicts = worker_out.get("verdicts") or {}
        for use_id, v in verdicts.items():
            row = by_id.get(use_id)
            if row is None:
                print(
                    f"warn: {worker_path.name} carries verdict for unknown "
                    f"use_id {use_id} (not in ledger); skipping",
                    file=sys.stderr,
                )
                continue
            if _apply_verdict(row, v):
                applied += 1

    _recompute_summary(ledger, rows)
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
        "--verdicts",
        type=Path,
        default=None,
        help=(
            "Flat `verdicts.json` (one object per use-site, the form the "
            "citation-audit workflow emits) to merge into the ledger before "
            "materializing. Preferred over --worker-dir."
        ),
    )
    parser.add_argument(
        "--worker-dir",
        "--haiku-dir",  # legacy alias
        dest="worker_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing `verifier-*.yaml` worker outputs (legacy "
            "`haiku-*.yaml` still accepted) to merge into the ledger before "
            "materializing. Used only when --verdicts is absent. Default: the "
            "ledger's parent directory."
        ),
    )
    parser.add_argument(
        "--materialize-only",
        action="store_true",
        help=(
            "Skip re-merging worker YAMLs; materialize astra.yaml from the "
            "ledger as-is. Use this for the post-gate re-materialize so the "
            "strict-source-gate downgrades in the ledger are NOT clobbered by "
            "re-reading the (pre-downgrade) worker verdicts."
        ),
    )
    parser.add_argument(
        "--fetch-state",
        type=Path,
        default=Path("work/citation-audit/fetch_state.json"),
        help=(
            "Path to fetch_state.json from fetch_sources.py "
            "(default: %(default)s)."
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

    if args.materialize_only:
        print("materialize-only: skipping verdict merge (ledger is authoritative)")
    elif args.verdicts is not None:
        if not args.verdicts.exists():
            print(f"error: verdicts not found at {args.verdicts}", file=sys.stderr)
            return 2
        applied = merge_verdicts_into_ledger(args.ledger, args.verdicts)
        print(f"merged {applied} verdict(s) from {args.verdicts} into ledger")
    else:
        worker_dir = args.worker_dir or args.ledger.parent
        if worker_dir.exists():
            applied = merge_worker_outputs_into_ledger(args.ledger, worker_dir)
            print(
                f"merged {applied} verdict(s) from "
                f"{worker_dir}/verifier-*.yaml into ledger"
            )

    added, updated, untouched = merge_ledger_into_astra(
        args.ledger, args.astra_yaml, fetch_state_path=args.fetch_state
    )
    print(
        f"materialized: {added} new prior_insight(s), {updated} updated; "
        f"{untouched} non-audit insight(s) left untouched"
    )
    print("next: cd work/reference && astra validate astra.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
