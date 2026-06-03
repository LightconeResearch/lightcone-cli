"""Regression tests for the citation-audit one-path / multi-anchor migration.

Pins the contract the rework established:
  - a `supported`/`weak` row carries **1..N anchors** (one quote per facet) and
    materializes as `evidence: [ev1 .. evN]` on the astra.yaml insight;
  - the `identity` verdict is **gone** — it is not in any enum, and an
    identity-labelled row never materializes;
  - a legacy single `quote` normalizes to a 1-element anchors list (back-compat);
  - the deterministic gate drops a failed anchor, keeps the survivors, and
    downgrades a row to `unverifiable` only when **every** anchor fails;
  - the report renders per-facet evidence and never the word "identity".

The skill scripts live in the plugin bundle (outside `src/`), so they load by
path. `source_match` is a sibling import for the gate, so the scripts dir goes
on `sys.path`.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "claude/lightcone/skills/citation-audit/scripts"
)
_SKILL = _SCRIPTS.parent
sys.path.insert(0, str(_SCRIPTS))  # make `import source_match` resolve


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_audit = _load("ca_build_audit", "build_audit_yaml.py")
render_report = _load("ca_render_report", "render_report.py")


# --------------------------------------------------------------------------
# Multi-anchor materialization
# --------------------------------------------------------------------------

def test_multi_anchor_materializes_one_evidence_per_facet() -> None:
    row = {
        "use_id": "x__line_1",
        "citation_key": "x.etal20",
        "claim": "Composite claim.",
        "doi": "10.0/x",
        "verdict": "supported",
        "anchors": [
            {"facet": "value", "exact": r"$S_8 = 0.7$", "prefix": "p", "suffix": "s",
             "section": "abstract", "substrate": "tex"},
            {"facet": "null B", "exact": "the B-mode signal is consistent with zero",
             "section": "results", "substrate": "tex"},
        ],
    }
    ins = build_audit._row_to_insight(row)
    assert ins is not None
    ev = ins["evidence"]
    assert [e["id"] for e in ev] == ["ev1", "ev2"]
    assert ev[0]["quote"]["exact"] == r"$S_8 = 0.7$"
    assert ev[0]["location"] == {"value": "abstract"}
    assert ev[1]["quote"]["exact"] == "the B-mode signal is consistent with zero"
    # No JSON-LD `type:` leaks into the quote selector.
    assert all("type" not in e["quote"] for e in ev)


def test_supported_tags_carry_verdict_and_cite_key() -> None:
    row = {
        "use_id": "x__line_1", "citation_key": "x.etal20", "claim": "c", "doi": "d",
        "verdict": "weak", "anchors": [{"facet": "f", "exact": "a long enough clause to pass"}],
    }
    ins = build_audit._row_to_insight(row)
    assert ins["tags"] == ["citation_audit", "verdict:weak", "cite_key:x.etal20"]


# --------------------------------------------------------------------------
# No `identity` verdict anywhere
# --------------------------------------------------------------------------

def test_identity_verdict_does_not_materialize() -> None:
    # `identity` is not in the verdict vocabulary; a row carrying it (e.g. a
    # stale ledger) must not become an ASTRA insight.
    row = {
        "use_id": "x__line_1", "citation_key": "x", "claim": "c", "doi": "d",
        "verdict": "identity", "anchors": [{"facet": "f", "exact": "anything at all here"}],
    }
    assert build_audit._row_to_insight(row) is None


def test_workflow_schema_enum_is_the_five_verdicts() -> None:
    wf = (_SKILL / "citation_audit_workflow.js").read_text()
    assert (
        "enum: ['supported', 'weak', 'unsupported', 'wrong_paper', 'unverifiable']"
        in wf
    )


def test_verifier_agent_enum_has_no_identity() -> None:
    agent = (_SKILL.parents[1] / "agents/citation-verifier.md").read_text()
    assert "verdict: supported | weak | unsupported | wrong_paper | unverifiable" in agent
    # The (now-deleted) verdicts must not appear in the output schema enum.
    assert "| identity |" not in agent
    assert "unverifiable_pre_arxiv" not in agent


# --------------------------------------------------------------------------
# Legacy single-quote back-compat
# --------------------------------------------------------------------------

def test_legacy_single_quote_normalizes_to_one_anchor() -> None:
    row = {
        "use_id": "x__line_1", "citation_key": "x", "claim": "c", "doi": "d",
        "verdict": "supported",
        "quote": {"exact": "a clause of real substance here", "prefix": "p", "suffix": "s"},
        "location": {"section": "Sect. 2"},
    }
    ins = build_audit._row_to_insight(row)
    assert len(ins["evidence"]) == 1
    assert ins["evidence"][0]["quote"]["exact"] == "a clause of real substance here"
    assert ins["evidence"][0]["location"] == {"value": "Sect. 2"}


def test_supported_without_anchor_is_skipped() -> None:
    # A supported/weak row with no anchor cannot be materialized (nothing to
    # verify) — it is skipped rather than emitting an evidence-less insight.
    row = {"use_id": "x__line_1", "citation_key": "x", "claim": "c", "doi": "d",
           "verdict": "supported", "anchors": []}
    assert build_audit._row_to_insight(row) is None


# --------------------------------------------------------------------------
# merge_verdicts_into_ledger
# --------------------------------------------------------------------------

def test_merge_verdicts_stores_anchors_and_drops_legacy_quote(tmp_path: Path) -> None:
    ledger = {"rows": [
        {"use_id": "x__line_1", "citation_key": "x", "doi": "d", "verdict": None,
         "quote": {"exact": "stale"}, "location": {"section": "old"}},
    ]}
    lp = tmp_path / "ledger.json"
    lp.write_text(json.dumps(ledger))
    vp = tmp_path / "verdicts.json"
    vp.write_text(json.dumps({"verdicts": [
        {"use_id": "x__line_1", "citation_key": "x", "verdict": "supported",
         "anchors": [{"facet": "a", "exact": "q"}], "notes": "n",
         "doi_flag": "phantom"},
    ]}))
    applied = build_audit.merge_verdicts_into_ledger(lp, vp)
    assert applied == 1
    row = json.loads(lp.read_text())["rows"][0]
    assert row["verdict"] == "supported"
    assert row["anchors"] == [{"facet": "a", "exact": "q"}]
    assert row["verdict_notes"] == "n"
    assert row["doi_flag"] == "phantom"
    assert "quote" not in row and "location" not in row


def test_merge_accepts_flat_list(tmp_path: Path) -> None:
    lp = tmp_path / "ledger.json"
    lp.write_text(json.dumps({"rows": [{"use_id": "x__line_1", "citation_key": "x", "doi": "d"}]}))
    vp = tmp_path / "verdicts.json"
    vp.write_text(json.dumps([  # bare list, not {"verdicts": [...]}
        {"use_id": "x__line_1", "citation_key": "x", "verdict": "unsupported",
         "anchors": [], "notes": "no support"},
    ]))
    assert build_audit.merge_verdicts_into_ledger(lp, vp) == 1
    assert json.loads(lp.read_text())["rows"][0]["verdict"] == "unsupported"


# --------------------------------------------------------------------------
# The deterministic gate (subprocess — exercises the sibling source_match)
# --------------------------------------------------------------------------

def _run_gate(ledger_path: Path, state_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "verify_and_downgrade.py"),
         "--ledger", str(ledger_path), "--state", str(state_path)],
        capture_output=True, text=True, check=True,
    )


def test_gate_drops_failed_anchor_and_downgrades_on_total_failure(tmp_path: Path) -> None:
    src = tmp_path / "papers" / "d" / "source"
    src.mkdir(parents=True)
    (src / "main.tex").write_text(
        r"We measure $S_8 = 0.776\pm0.017$ from the cosmic shear two-point functions."
    )
    ledger = {"rows": [
        # partial: one real anchor, one fabricated → keep verdict, drop the fake
        {"use_id": "keep__line_1", "citation_key": "a", "doi": "d", "verdict": "supported",
         "anchors": [
             {"facet": "real", "exact": r"$S_8 = 0.776\pm0.017$", "prefix": "We measure",
              "suffix": "from the cosmic shear", "substrate": "tex"},
             {"facet": "fake", "exact": "we conclusively detected nine sigma oscillations",
              "substrate": "tex"},
         ]},
        # total failure: the only anchor is fabricated → downgrade to unverifiable
        {"use_id": "drop__line_2", "citation_key": "b", "doi": "d", "verdict": "supported",
         "anchors": [
             {"facet": "fake", "exact": "an entirely fabricated sentence present in no source",
              "substrate": "tex"},
         ]},
    ]}
    lp = tmp_path / "ledger.json"
    lp.write_text(json.dumps(ledger))
    sp = tmp_path / "fetch_state.json"
    sp.write_text(json.dumps({"d": {"status": "source_fetched", "backend": "tex",
                                    "source_dir": str(src)}}))

    _run_gate(lp, sp)
    rows = {r["use_id"]: r for r in json.loads(lp.read_text())["rows"]}

    keep = rows["keep__line_1"]
    assert keep["verdict"] == "supported"
    assert [a["facet"] for a in keep["anchors"]] == ["real"]  # fake dropped

    drop = rows["drop__line_2"]
    assert drop["verdict"] == "unverifiable"
    assert drop["anchors"] == []


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

def test_report_renders_facets_and_never_identity(tmp_path: Path) -> None:
    ledger = {"rows": [
        {"use_id": "a__line_1", "citation_key": "a.etal20", "doi": "d", "file": "p.tex",
         "line": 1, "claim": "A claim about cosmic shear.",
         "manuscript_prefix": "Cosmic shear is a probe.",
         "manuscript_suffix": "Systematics matter.",
         "verdict": "supported",
         "anchors": [{"facet": "the facet", "exact": "the supporting sentence verbatim",
                      "section": "abstract", "substrate": "tex"}]},
        {"use_id": "b__line_2", "citation_key": "b.etal21", "doi": "d", "file": "p.tex",
         "line": 2, "claim": "An unsupported claim.", "verdict": "unsupported", "anchors": []},
    ]}
    lp = tmp_path / "ledger.json"
    lp.write_text(json.dumps(ledger))
    refdir = tmp_path / "reference"
    refdir.mkdir()
    (refdir / "index.json").write_text(json.dumps({"title": "Test Paper Title"}))
    out = tmp_path / "report.html"
    render_report.render(lp, refdir, out)
    doc = out.read_text()

    assert "identity" not in doc.lower()
    assert "the supporting sentence verbatim" in doc
    assert "the facet" in doc                      # facet label rendered
    assert "Test Paper Title" in doc               # manuscript title in header
    assert "--sev:5" in doc and "--sev:1" in doc   # supported + unsupported severities
    assert "1 supported" in doc and "1 unsupported" in doc  # chips
