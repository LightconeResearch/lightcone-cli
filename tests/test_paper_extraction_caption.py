"""Tests for paper-extraction caption parsing."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "claude"
    / "lightcone"
    / "skills"
    / "paper-extraction"
    / "scripts"
    / "extract-paper-substrate.py"
)
_SPEC = spec_from_file_location("paper_extraction_extract_script", SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SCRIPT = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SCRIPT)


def test_extract_caption_handles_nested_braces_and_last_nonempty_caption() -> None:
    text = r"\caption{}\caption{X $A^{\mathrm{Y}}$ Z}"
    assert _SCRIPT.extract_caption(text, {}) == r"X $A^{\mathrm{Y}}$ Z"
