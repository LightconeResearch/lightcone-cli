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


def test_unique_exact_verifies_despite_approximate_context() -> None:
    # The Hartlap/Liaudat/Singh case: the substantive exact is verbatim and
    # UNIQUE, but the verifier's suffix is approximate — a \footnotemark[1] sits
    # where the verifier wrote \footnotemark. A unique exact needs no context, so
    # the brittle prefix+exact+suffix contiguity must NOT fail it.
    source = source_match._norm(
        r"From (\ref{eq}) it follows that an unbiased estimator of "
        r"$\tens{\Sigma}^{-1}$ is given by \footnotemark[1] \begin{equation}"
    )
    ok, reason = source_match.quote_in_source(
        source,
        exact=r"an unbiased estimator of $\tens{\Sigma}^{-1}$ is given by",
        prefix="it follows that",
        suffix=r"\footnotemark",  # source has \footnotemark[1] — does not match contiguously
    )
    assert ok
    assert "unique" in reason


def test_repeated_exact_needs_context_to_disambiguate() -> None:
    # When the exact repeats, prefix/suffix must pin an occurrence.
    source = source_match._norm(
        "the signal is detected at high significance in the red sample "
        "and the signal is detected at high significance in the blue sample"
    )
    exact = "the signal is detected at high significance"
    # Matching suffix pins the right occurrence → verified.
    ok, _ = source_match.quote_in_source(source, exact=exact, suffix="in the blue sample")
    assert ok
    # Context that matches no occurrence → cannot pin → fail.
    bad, reason = source_match.quote_in_source(source, exact=exact, suffix="in the green sample")
    assert not bad
    assert "pin" in reason


def test_absent_exact_still_fails() -> None:
    source = source_match._norm("a sentence that does not contain the claimed quote at all")
    ok, reason = source_match.quote_in_source(
        source, exact="we measure a strong intrinsic alignment signal for red galaxies"
    )
    assert not ok
    assert "not found" in reason
