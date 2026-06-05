"""Tests for the PDF-tolerant matching utilities and bibkey → author-year.

The fresh-paper run surfaced that pre-arXiv cites resolve to image-scan PDFs whose
OCR text is noisy (a Greek µ extracted as ``fi``), and that the report's
author-year helper only understood dotted bibkeys. These guard the fuzzy PDF
match's order-sensitivity (the property that keeps it trustworthy) and the
author-year parser across naming conventions.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SM = _ROOT / "claude/lightcone/skills/citation-audit/scripts/source_match.py"
_RR = _ROOT / "claude/lightcone/skills/citation-audit/scripts/render_report.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sm = _load(_SM, "source_match")
rr = _load(_RR, "render_report")


# --- norm_pdf --------------------------------------------------------------


def test_norm_pdf_folds_ligatures_and_dehyphenates():
    # NFKD folds the ﬁ ligature to "fi"; a hyphenated line break joins.
    assert sm.norm_pdf("de-\nblended") == "deblended"
    assert "fi" in sm.norm_pdf("ﬁnite")
    assert sm.norm_pdf("$S_8 = 0.776$") == "s 8 0 776"  # punctuation/math → spaces


# --- partial_ratio: order-sensitive --------------------------------------


def test_partial_ratio_contiguous_high_scattered_low():
    hay = sm.norm_pdf(
        "objects are detected and deblended with source extractor using a one-pass algorithm"
    )
    true = sm.partial_ratio(sm.norm_pdf("detected and deblended with source extractor"), hay)
    scattered = sm.partial_ratio(sm.norm_pdf("source objects pass detected extractor one"), hay)
    assert true >= 0.85
    assert scattered < 0.65
    assert true - scattered > 0.2  # the gap is the trust margin


def test_partial_ratio_tolerates_one_symbol_artifact():
    # The Kaiser1987 case: pymupdf renders µ as "fi"; the rest of the clause matches.
    hay = sm.norm_pdf("where fi is the cosine of the angle between k and the line-of-sight")
    q = sm.norm_pdf("where mu is the cosine of the angle between k and the line-of-sight")
    assert sm.partial_ratio(q, hay) >= 0.80


def test_partial_ratio_empty_needle():
    assert sm.partial_ratio("", "anything") == 0.0


# --- ordered_recall: insertion-tolerant scan rescue -----------------------


def test_ordered_recall_tolerates_spliced_table():
    # The Bertin96 case: a multi-column scan's OCR splices a parameter table
    # ("mode 2.5x median 1.5x mean") into the middle of the sentence, between
    # "one-" and "pass". The quote's words are all present and in order; only
    # contiguity is broken. ordered_recall recovers it where partial_ratio cannot.
    needle = sm.norm_pdf(
        "SExtractor uses Lutz's one-pass algorithm to extract 8-connected contiguous pixels"
    )
    spliced = sm.norm_pdf(
        "SExtractor uses Lutz's one mode 2.5 x median 1.5 x mean 1 "
        "pass algorithm to extract 8-connected contiguous pixels"
    )
    assert sm.ordered_recall(needle, spliced) >= 0.95
    # the symmetric metric is fooled by the splice — this is *why* recall exists
    assert sm.partial_ratio(needle, spliced) < sm.ordered_recall(needle, spliced)


def test_ordered_recall_rejects_scrambled_words():
    # Word-order scrambling (the reward-hack shape) must stay well below the
    # 0.85 gate bar even though every word is individually present.
    hay = sm.norm_pdf(
        "objects are detected and deblended with source extractor using a one-pass algorithm "
        "and then measured and classified with care across the survey image"
    )
    scrambled = sm.norm_pdf("pixels extractor source one detected pass objects deblended algorithm")
    assert sm.ordered_recall(scrambled, hay) < 0.85


def test_ordered_recall_window_bounds_scattered_subsequence():
    # All needle tokens appear in order, but spread across far more than the
    # 2x window — the bounded window forbids unlimited insertion, so a genuine
    # contiguous quote is required, not a document-spanning subsequence.
    needle = sm.norm_pdf("alpha beta gamma delta epsilon zeta")
    filler = " " + "lorem ipsum dolor sit amet " * 6
    scattered = sm.norm_pdf(
        "alpha" + filler + "beta" + filler + "gamma" + filler + "delta" + filler + "epsilon" + filler + "zeta"
    )
    assert sm.ordered_recall(needle, scattered) < 0.85


def test_ordered_recall_perfect_for_contiguous_quote():
    hay = sm.norm_pdf("we introduce and recommend an improved estimator whose variance is nearly Poisson")
    needle = sm.norm_pdf("an improved estimator whose variance is nearly Poisson")
    assert sm.ordered_recall(needle, hay) >= 0.99


def test_ordered_recall_empty_needle():
    assert sm.ordered_recall("", "anything") == 0.0


# --- author_year across bibkey conventions --------------------------------


def test_author_year_concatenated_keys():
    assert rr.author_year("Gwyn2008") == "Gwyn 2008"
    assert rr.author_year("Bertin96") == "Bertin 1996"
    assert rr.author_year("Singh2015") == "Singh 2015"
    assert rr.author_year("LandySzalay1993") == "Landy-Szalay 1993"
    assert rr.author_year("HervasPeters2024") == "Hervas-Peters 2024"


def test_author_year_dotted_keys_unchanged():
    assert rr.author_year("asgari.etal19a") == "Asgari et al. 2019"
