"""Tests for canonical ASTRA recipe-resource normalization."""
from __future__ import annotations

import pytest

from lightcone.engine.resources import (
    ResourceValueError,
    parse_memory_mb,
    parse_recipe_resources,
    parse_time_seconds,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("512MB", 512), ("8GB", 8000), ("1.5GiB", 1611)],
)
def test_parse_memory_mb(value: str, expected: int) -> None:
    assert parse_memory_mb(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30m", 1800), ("1h30m", 5400), ("02:15:00", 8100), ("1-02:00:00", 93600)],
)
def test_parse_time_seconds(value: str, expected: int) -> None:
    assert parse_time_seconds(value) == expected


def test_parse_recipe_resources_maps_to_snakemake_names() -> None:
    normalized = parse_recipe_resources(
        {
            "command": "python fit.py",
            "resources": {
                "cpus": 8,
                "memory": "16GB",
                "gpus": 2,
                "time_limit": "1h1s",
            },
        }
    )
    assert normalized.snakemake() == {
        "cpus_per_task": 8,
        "mem_mb": 16000,
        "gpus_per_task": 2,
        "runtime": 61,
    }


def test_parse_recipe_resources_requires_time_for_async() -> None:
    with pytest.raises(ResourceValueError, match="missing resources.time_limit"):
        parse_recipe_resources(
            {"command": "echo"}, require_time_limit=True, label="output 'fit'"
        )


@pytest.mark.parametrize("value", ["eight GB", "8", "", "-1GB"])
def test_parse_memory_rejects_ambiguous_values(value: str) -> None:
    with pytest.raises(ResourceValueError):
        parse_memory_mb(value)

