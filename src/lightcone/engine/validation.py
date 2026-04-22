"""Post-materialization result file validation for ASTRA outputs."""
from __future__ import annotations

import csv
import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def validate_output(
    output_dir: Path,
    output_type: str | None,
    output_id: str,
) -> list[str]:
    """Validate result files after a successful recipe run.

    Returns a list of warning strings. An empty list means no issues detected.
    Never raises — all errors are surfaced as warning strings.
    """
    if not output_dir.exists():
        return [f"Output directory missing after successful run: {output_dir}"]
    if not output_dir.is_dir():
        return [f"Output '{output_id}': expected a directory at {output_dir}, found a file"]

    try:
        files = list(output_dir.iterdir())
    except OSError:
        return []

    if not files:
        return [f"Output directory is empty after successful run: {output_dir}"]

    if output_type == "metric":
        return _validate_metric(output_dir, output_id)
    if output_type == "table":
        return _validate_table(output_dir, output_id)
    if output_type == "figure":
        return _validate_figure(output_dir, output_id)
    return []


def _validate_metric(output_dir: Path, output_id: str) -> list[str]:
    json_files = list(output_dir.glob("*.json"))
    if not json_files:
        return [
            f"Output '{output_id}' (type: metric) produced no JSON files in {output_dir}"
        ]

    warnings: list[str] = []
    for json_file in json_files:
        try:
            data: Any = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            warnings.append(
                f"Output '{output_id}': metric file '{json_file.name}' "
                f"is not valid JSON: {exc}"
            )
            continue

        if _all_scalars_null(data):
            warnings.append(
                f"Output '{output_id}': metric file '{json_file.name}' "
                f"contains only null/NaN values"
            )

    return warnings


def _validate_table(output_dir: Path, output_id: str) -> list[str]:
    csv_files = list(output_dir.glob("*.csv"))
    if not csv_files:
        return [
            f"Output '{output_id}' (type: table) produced no CSV files in {output_dir}"
        ]

    warnings: list[str] = []
    for csv_file in csv_files:
        try:
            warnings.extend(_check_csv_nan(csv_file, output_id))
        except (OSError, csv.Error) as exc:
            warnings.append(
                f"Output '{output_id}': could not validate table '{csv_file.name}': {exc}"
            )
    return warnings


def _check_csv_nan(csv_file: Path, output_id: str) -> list[str]:
    with open(csv_file, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    if not rows:
        return [
            f"Output '{output_id}': table file '{csv_file.name}' has no data rows"
        ]

    fieldnames = list(rows[0].keys())
    all_nan_cols: list[str] = []
    numeric_cols: list[str] = []

    for col in fieldnames:
        numeric_vals: list[float] = []
        for row in rows:
            raw = row.get(col, "")
            try:
                numeric_vals.append(float(raw))
            except (ValueError, TypeError):
                pass
        if numeric_vals:
            numeric_cols.append(col)
            if all(math.isnan(v) for v in numeric_vals):
                all_nan_cols.append(col)

    if not numeric_cols:
        return []

    if len(all_nan_cols) == len(numeric_cols):
        return [
            f"Output '{output_id}': table file '{csv_file.name}' "
            f"has all-NaN values in every numeric column"
        ]
    if all_nan_cols:
        cols = ", ".join(f"'{c}'" for c in all_nan_cols)
        return [
            f"Output '{output_id}': table file '{csv_file.name}' "
            f"has all-NaN values in column(s): {cols}"
        ]
    return []


def _validate_figure(output_dir: Path, output_id: str) -> list[str]:
    figure_exts = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".eps"}
    figure_files = [
        f for f in output_dir.iterdir()
        if f.is_file() and f.suffix.lower() in figure_exts
    ]

    if not figure_files:
        return [
            f"Output '{output_id}' (type: figure) produced no image files "
            f"(.png, .jpg, .svg, .pdf, .eps) in {output_dir}"
        ]

    return [
        f"Output '{output_id}': figure file '{fig.name}' is empty (0 bytes)"
        for fig in figure_files
        if fig.stat().st_size == 0
    ]


def _all_scalars_null(data: Any) -> bool:
    """Return True if every scalar in the JSON structure is null or NaN."""
    scalars = _collect_scalars(data)
    return bool(scalars) and all(_is_null_scalar(s) for s in scalars)


def _collect_scalars(data: Any) -> list[Any]:
    if isinstance(data, dict):
        result: list[Any] = []
        for v in data.values():
            result.extend(_collect_scalars(v))
        return result
    if isinstance(data, list):
        result = []
        for item in data:
            result.extend(_collect_scalars(item))
        return result
    return [data]


def _is_null_scalar(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return False
