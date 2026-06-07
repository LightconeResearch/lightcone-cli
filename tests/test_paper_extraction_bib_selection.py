"""Tests for multi-.bib selection and eprint/bibcode surfacing in paper-extraction.

Two generalization bugs surfaced on the first fresh-paper audit (a build dir with
three .bib files):

  1. `copy_embedded_bibliography` grabbed an *arbitrary* .bib
     (`next(iter(rglob("*.bib")))`), so a build dir carrying several .bib files
     resolved cites against the wrong key namespace — 42/72 cites falsely flagged
     no-DOI. It must honor the manuscript's `\\bibliography{}` / `\\addbibresource{}`
     declaration.

  2. The `.bib`'s own `eprint`/`adsurl` were parsed but dropped from the emitted
     `citations:` block, forcing fetch_sources to re-derive the eprint from the DOI
     via ADS (a token-gated round-trip). The bibcode parsed from `adsurl` must
     survive so the ADS link-gateway PDF can be reached directly.

The script is hyphenated and not importable as a normal module; load via importlib.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "claude/lightcone/skills/paper-extraction/scripts/extract-paper-substrate.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("extract_paper_substrate", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eps = _load()


# --- _bibcode_from_fields --------------------------------------------------


def test_bibcode_from_adsurl():
    fields = {"adsurl": "https://ui.adsabs.harvard.edu/abs/2015JCAP...08..015B"}
    assert eps._bibcode_from_fields(fields) == "2015JCAP...08..015B"


def test_bibcode_from_adsurl_with_abstract_suffix():
    fields = {"adsurl": "https://ui.adsabs.harvard.edu/abs/1993ApJ...412...64L/abstract"}
    assert eps._bibcode_from_fields(fields) == "1993ApJ...412...64L"


def test_bibcode_explicit_field_preferred():
    fields = {
        "bibcode": "2024arXiv240915416F",
        "adsurl": "https://ui.adsabs.harvard.edu/abs/SHOULD_NOT_WIN",
    }
    assert eps._bibcode_from_fields(fields) == "2024arXiv240915416F"


def test_bibcode_url_encoded():
    # ADS bibcodes with an ampersand arrive percent-encoded in the URL.
    fields = {"adsurl": "https://ui.adsabs.harvard.edu/abs/2020A%26A...640L..14A"}
    assert eps._bibcode_from_fields(fields) == "2020A&A...640L..14A"


def test_bibcode_absent():
    assert eps._bibcode_from_fields({"journal": "MNRAS"}) is None


# --- bib selection honors the manuscript's declaration ---------------------


def _entry(key: str, doi: str) -> str:
    return f"@ARTICLE{{{key},\n  title = {{T {key}}},\n  doi = {{{doi}}},\n}}\n"


def _make_source(tmp_path: Path, main_tex: str) -> tuple[Path, Path]:
    """Build a source tree with a declared bib under bibtex/ and a top-level decoy.

    The decoy sorts BEFORE the declared bib (rglob's old first-wins would pick it),
    so a passing test proves the declaration — not luck — drives selection.
    """
    src = tmp_path / "source"
    (src / "bibtex").mkdir(parents=True)
    (src / "main.tex").write_text(main_tex, encoding="utf-8")
    (src / "bibtex" / "references.bib").write_text(_entry("RealKey", "10.1/real"), encoding="utf-8")
    (src / "aaa_decoy.bib").write_text(_entry("DecoyKey", "10.2/decoy"), encoding="utf-8")
    ref = tmp_path / "reference"
    ref.mkdir()
    return src, ref


def test_declared_bibliography_command_wins(tmp_path: Path):
    src, ref = _make_source(tmp_path, r"\bibliography{bibtex/references}")
    bib_rel, _ = eps.copy_embedded_bibliography(ref, src)
    text = (ref / bib_rel).read_text()
    assert "RealKey" in text
    assert "DecoyKey" not in text  # the alphabetically-first decoy must NOT win


def test_addbibresource_command_wins(tmp_path: Path):
    src, ref = _make_source(tmp_path, r"\addbibresource{bibtex/references.bib}")
    bib_rel, _ = eps.copy_embedded_bibliography(ref, src)
    text = (ref / bib_rel).read_text()
    assert "RealKey" in text
    assert "DecoyKey" not in text


def test_declared_bib_matched_by_basename_when_path_differs(tmp_path: Path):
    # Declared as a bare stem with no dir; resolve by basename anywhere in the tree.
    src, ref = _make_source(tmp_path, r"\bibliography{references}")
    bib_rel, _ = eps.copy_embedded_bibliography(ref, src)
    assert "RealKey" in (ref / bib_rel).read_text()


def test_no_declaration_falls_back_deterministically(tmp_path: Path):
    # No \bibliography at all -> sorted-first fallback (deterministic, not arbitrary).
    src, ref = _make_source(tmp_path, "no bibliography command here")
    bib_rel, _ = eps.copy_embedded_bibliography(ref, src)
    # aaa_decoy.bib sorts first; the fallback is deterministic rather than FS-order.
    assert "DecoyKey" in (ref / bib_rel).read_text()
