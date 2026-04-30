"""Validate every eval task's astra.yaml against the installed astra schema.

The eval scaffold runs `lc init` and overlays the task's astra.yaml on
top, so the only spec the task itself ships must validate cleanly under
the same astra version the eval harness uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "evals" / "tasks"


def _task_specs() -> list[Path]:
    return sorted(TASKS_DIR.glob("*/astra.yaml"))


@pytest.mark.parametrize(
    "spec_path",
    _task_specs(),
    ids=lambda p: p.parent.name,
)
def test_task_astra_yaml_validates(spec_path: Path) -> None:
    from astra.validation import validate_analysis_file

    errors = validate_analysis_file(spec_path)
    assert not errors, "\n".join(str(e) for e in errors)
