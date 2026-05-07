"""Tests for analysis tree helpers — pure spec walking, no orchestrator."""
from __future__ import annotations

from pathlib import Path

import pytest
from astra.helpers import load_yaml, resolve_analysis_tree

from lightcone.engine.tree import (
    collect_tree_outputs,
    resolve_output_path,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "astra.yaml").write_text(
        """
version: "1.0"
name: "Test"
inputs:
  - id: raw
    type: data
    source: /tmp/raw.csv
outputs:
  - id: root_out
    recipe:
      command: echo r
analyses:
  feat:
    outputs:
      - id: features
        recipe:
          command: echo f
"""
    )
    return tmp_path


def test_collect_tree_outputs(project: Path) -> None:
    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    outs = collect_tree_outputs(spec)
    ids = {(o.analysis_id, o.output_id) for o in outs}
    assert (None, "root_out") in ids
    assert ("feat", "features") in ids


def test_resolve_output_path_root(project: Path) -> None:
    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    [root] = [o for o in collect_tree_outputs(spec) if o.analysis_id is None]
    p = resolve_output_path(project, root, "u1")
    # Root outputs land under <project>/results/<universe>/
    assert p == project / "results" / "u1"


def test_resolve_output_path_sub_analysis_inline(project: Path) -> None:
    """Sub-analyses without an explicit `path:` share the root results dir."""
    spec = resolve_analysis_tree(load_yaml(project / "astra.yaml"), project)
    [sub] = [o for o in collect_tree_outputs(spec) if o.analysis_id == "feat"]
    p = resolve_output_path(project, sub, "u1")
    assert p == project / "results" / "u1"
