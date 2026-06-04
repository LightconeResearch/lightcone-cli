"""Tests for the paper-extraction DOI-resolver guards.

These guard against the phantom-DOI class of bug: a programmatic Crossref/ADS
title search returning a *wrong* DOI for an entry that has no DOI to find
(an in-preparation paper, an ASCL software record), or a too-loose fuzzy
match accepting an unrelated paper. A wrong DOI silently points evidence
verification at the wrong paper, so the resolver must prefer a flagged miss
over a fabricated hit.

The resolver lives in a hyphenated script (`extract-paper-substrate.py`) that
isn't importable as a normal module, so we load it via importlib.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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


# --- classify_unresolvable -------------------------------------------------


@pytest.mark.parametrize(
    "fields,expected",
    [
        # Published: a real DOI or arXiv eprint -> resolvable (None).
        ({"doi": "10.1051/0004-6361/202142479"}, None),
        ({"eprint": "2212.03257", "archiveprefix": "arXiv"}, None),
        ({"eprint": "astro-ph/0307393", "archiveprefix": "arXiv"}, None),
        # In preparation: no DOI to find -> flag, never fuzzy-resolve.
        ({"journal": "in preparation", "title": "Paper V"}, "in_preparation"),
        ({"note": "submitted to MNRAS"}, "in_preparation"),
        ({"journal": "to appear in A&A"}, "in_preparation"),
        # ASCL software record -> software cite, verify by existence.
        (
            {"archiveprefix": "ascl", "eprint": "1508.007", "title": "TreeCorr"},
            "software_ascl",
        ),
        ({"eprint": "ascl:1508.007"}, "software_ascl"),
        # No publication info at all -> can't form a trustworthy query.
        ({"title": "Some untethered note", "author": "Doe, J."}, "no_publication_info"),
        # Has a journal but no doi/eprint -> resolvable (will be gated-resolved).
        ({"journal": "MNRAS", "title": "A real paper", "author": "Doe, J."}, None),
    ],
)
def test_classify_unresolvable(fields, expected):
    assert eps.classify_unresolvable(fields) == expected


def test_ascl_is_not_treated_as_arxiv():
    """The treecorr15 regression: ascl:1508.007 must not resolve to a DOI."""
    fields = {"archiveprefix": "ascl", "eprint": "1508.007"}
    assert eps.classify_unresolvable(fields) == "software_ascl"


# --- _author_matches -------------------------------------------------------


def test_author_matches_blocks_real_mismatch():
    # Crossref dict form.
    assert eps._author_matches("Goh", [{"family": "Hawken"}]) is False
    # ADS / bib "Last, First" string form.
    assert eps._author_matches("Jarvis", ["Jarvis, M."]) is True


def test_author_matches_accepts_missing_metadata():
    # No candidate author -> don't block; lean on the title gate.
    assert eps._author_matches("Goh", []) is True
    assert eps._author_matches("Goh", None) is True
    # No queried surname -> nothing to check.
    assert eps._author_matches("", [{"family": "Anyone"}]) is True


# --- title gate ------------------------------------------------------------


def test_phantom_title_collision_below_gate():
    """The exact phantom-DOI collision: a 1978 DOE report whose subtitle matches
    a software title must fall below the 0.80 gate."""
    sim = eps._title_similarity(
        "TreeCorr: Two-point correlation functions",
        "Quantum electrodynamics and light rays. [Two-point correlation functions]",
    )
    assert sim < eps.TITLE_MATCH_MIN


def test_true_title_above_gate():
    sim = eps._title_similarity(
        "A general framework for removing point-spread function additive systematics",
        "A general framework for removing point spread function additive systematics",
    )
    assert sim >= eps.TITLE_MATCH_MIN
