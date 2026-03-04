"""Tests for ASTRA IO Manager."""
from __future__ import annotations

from prism.dagster.io_manager import ASTRAIOManager


class TestASTRAIOManager:
    def test_get_path_basic(self, tmp_path):
        mgr = ASTRAIOManager(project_root=str(tmp_path))
        path = mgr.get_output_path("accuracy", "baseline")
        assert path == tmp_path / "results" / "baseline" / "accuracy"

    def test_get_path_different_universe(self, tmp_path):
        mgr = ASTRAIOManager(project_root=str(tmp_path))
        path = mgr.get_output_path("accuracy", "experiment1")
        assert path == tmp_path / "results" / "experiment1" / "accuracy"

    def test_get_input_paths(self, tmp_path):
        mgr = ASTRAIOManager(project_root=str(tmp_path))
        paths = mgr.get_input_paths(["cleaned_data", "params"], "baseline")
        assert paths == {
            "cleaned_data": tmp_path / "results" / "baseline" / "cleaned_data",
            "params": tmp_path / "results" / "baseline" / "params",
        }
