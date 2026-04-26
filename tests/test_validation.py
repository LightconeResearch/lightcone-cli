"""Tests for post-materialization result file validation."""
from __future__ import annotations

import json
import math
from pathlib import Path

from lightcone.engine.validation import validate_output


class TestValidateOutputCommon:
    def test_missing_directory_warns(self, tmp_path: Path) -> None:
        warnings = validate_output(tmp_path / "nonexistent", "metric", "my_output")
        assert len(warnings) == 1
        assert "missing" in warnings[0]

    def test_empty_directory_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        warnings = validate_output(out_dir, "metric", "my_output")
        assert len(warnings) == 1
        assert "empty" in warnings[0]

    def test_unknown_type_skips_content_check(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (out_dir / "data.bin").write_bytes(b"\x00" * 100)
        assert validate_output(out_dir, "data", "my_output") == []

    def test_none_type_skips_content_check(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        (out_dir / "data.bin").write_bytes(b"\x00" * 100)
        assert validate_output(out_dir, None, "my_output") == []


class TestValidateMetric:
    def test_valid_json_no_warning(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "metric.json").write_text(json.dumps({"value": 0.95, "count": 100}))
        assert validate_output(out_dir, "metric", "result") == []

    def test_missing_json_file_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "output.txt").write_text("some text")
        warnings = validate_output(out_dir, "metric", "result")
        assert len(warnings) == 1
        assert "no JSON files" in warnings[0]

    def test_invalid_json_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "metric.json").write_text("not valid json {{{")
        warnings = validate_output(out_dir, "metric", "result")
        assert len(warnings) == 1
        assert "not valid JSON" in warnings[0]

    def test_all_null_dict_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "metric.json").write_text(json.dumps({"a": None, "b": None}))
        warnings = validate_output(out_dir, "metric", "result")
        assert len(warnings) == 1
        assert "null/NaN" in warnings[0]

    def test_null_scalar_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "metric.json").write_text("null")
        warnings = validate_output(out_dir, "metric", "result")
        assert len(warnings) == 1
        assert "null/NaN" in warnings[0]

    def test_partial_null_no_warning(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "metric.json").write_text(json.dumps({"a": None, "b": 1.0}))
        assert validate_output(out_dir, "metric", "result") == []

    def test_empty_dict_no_warning(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "metric.json").write_text("{}")
        assert validate_output(out_dir, "metric", "result") == []

    def test_nested_all_null_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "metric.json").write_text(json.dumps({"a": {"x": None, "y": None}}))
        warnings = validate_output(out_dir, "metric", "result")
        assert len(warnings) == 1
        assert "null/NaN" in warnings[0]


class TestValidateTable:
    def test_valid_csv_no_warning(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "table.csv").write_text("col_a,col_b\n1.0,2.0\n3.0,4.0\n")
        assert validate_output(out_dir, "table", "result") == []

    def test_missing_csv_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "output.txt").write_text("some text")
        warnings = validate_output(out_dir, "table", "result")
        assert len(warnings) == 1
        assert "no CSV files" in warnings[0]

    def test_all_nan_all_columns_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        nan = math.nan
        (out_dir / "table.csv").write_text(f"col_a,col_b\n{nan},{nan}\n{nan},{nan}\n")
        warnings = validate_output(out_dir, "table", "result")
        assert len(warnings) == 1
        assert "all-NaN" in warnings[0]
        assert "every numeric column" in warnings[0]

    def test_all_nan_single_column_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        nan = math.nan
        (out_dir / "table.csv").write_text(f"col_a,col_b\n1.0,{nan}\n2.0,{nan}\n")
        warnings = validate_output(out_dir, "table", "result")
        assert len(warnings) == 1
        assert "col_b" in warnings[0]
        assert "every numeric column" not in warnings[0]

    def test_empty_csv_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "table.csv").write_text("col_a,col_b\n")
        warnings = validate_output(out_dir, "table", "result")
        assert len(warnings) == 1
        assert "no data rows" in warnings[0]

    def test_non_numeric_columns_no_warning(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "table.csv").write_text("name,label\nfoo,bar\nbaz,qux\n")
        assert validate_output(out_dir, "table", "result") == []

    def test_mixed_valid_and_nan_rows_no_warning(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        nan = math.nan
        (out_dir / "table.csv").write_text(f"col_a,col_b\n1.0,{nan}\n{nan},4.0\n")
        assert validate_output(out_dir, "table", "result") == []


class TestValidateFigure:
    def test_valid_png_no_warning(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "figure.png").write_bytes(b"\x89PNG fake content")
        assert validate_output(out_dir, "figure", "result") == []

    def test_missing_image_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "output.txt").write_text("some text")
        warnings = validate_output(out_dir, "figure", "result")
        assert len(warnings) == 1
        assert "no image files" in warnings[0]

    def test_empty_image_file_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "figure.png").write_bytes(b"")
        warnings = validate_output(out_dir, "figure", "result")
        assert len(warnings) == 1
        assert "empty" in warnings[0]
        assert "0 bytes" in warnings[0]

    def test_svg_accepted(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "figure.svg").write_text("<svg>content</svg>")
        assert validate_output(out_dir, "figure", "result") == []

    def test_pdf_accepted(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "figure.pdf").write_bytes(b"%PDF-1.4 fake content")
        assert validate_output(out_dir, "figure", "result") == []

    def test_multiple_figures_partial_empty_warns(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "result"
        out_dir.mkdir()
        (out_dir / "fig1.png").write_bytes(b"\x89PNG fake content")
        (out_dir / "fig2.png").write_bytes(b"")
        warnings = validate_output(out_dir, "figure", "result")
        assert len(warnings) == 1
        assert "fig2.png" in warnings[0]
