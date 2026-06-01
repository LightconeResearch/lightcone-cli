"""Regression tests for the citation-audit reward-hack defense.

`source_match.is_substantive` is the substance bar that rejects degenerate
"quotes" — the title scrap (`Year 3`) that a verifier emits to cheaply pass
the gate. It lives in the skill bundle (outside `src/`), so we load it by
path. These tests pin the exact failure the gate-hardening work exists to
prevent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "claude/lightcone/skills/citation-audit/scripts/source_match.py"
)
_spec = importlib.util.spec_from_file_location("citation_audit_source_match", _SCRIPT)
assert _spec and _spec.loader
source_match = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(source_match)


@pytest.mark.parametrize(
    "quote",
    [
        "Year 3",  # the motivating reward-hack: title scrap
        "Dark Energy Survey",  # title fragment
        "consistent with zero",  # short prose, no measured value
        "$=$",  # bare operator
        "we use",  # function words
    ],
)
def test_degenerate_quotes_rejected(quote: str) -> None:
    ok, reason = source_match.is_substantive(quote)
    assert not ok
    assert "degenerate" in reason


@pytest.mark.parametrize(
    "quote",
    [
        r"$S_8 = 0.776\pm0.017$",  # ideal quantitative quote (terse but substantive)
        r"we detect a B-mode signal at $3.2\sigma$ significance",
        "B-modes are produced by the clustering of source galaxies",  # clause-length prose
        "an efficient tree-based algorithm for two-point correlation functions",
    ],
)
def test_substantive_quotes_pass(quote: str) -> None:
    ok, _ = source_match.is_substantive(quote)
    assert ok


def test_bare_integer_is_not_a_measured_value() -> None:
    # "Year 3" contains a digit but no measured-value signal — it must NOT
    # clear the bar on the strength of the integer alone.
    ok, _ = source_match.is_substantive("Year 3")
    assert not ok


def test_quote_in_source_rejects_scrap_before_substring_check() -> None:
    # The scrap appears verbatim in the source, with valid contiguous
    # context — but the substance bar rejects it anyway, because evidence
    # padding cannot rescue a scrap `exact`.
    source = source_match._norm(
        "consistent with previous Dark Energy Survey Year 3 analyses"
    )
    ok, reason = source_match.quote_in_source(
        source,
        exact="Year 3",
        prefix="Dark Energy Survey",
        suffix="analyses",
    )
    assert not ok
    assert "degenerate" in reason


def test_quote_in_source_accepts_real_quote() -> None:
    source = source_match._norm(
        r"We find $S_8 = 0.776\pm0.017$ from the cosmic shear two-point functions."
    )
    ok, _ = source_match.quote_in_source(
        source,
        exact=r"$S_8 = 0.776\pm0.017$",
        prefix="We find",
        suffix="from the cosmic shear",
    )
    assert ok
