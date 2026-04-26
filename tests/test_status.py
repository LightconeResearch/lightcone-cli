"""Tests for materialization status queries."""
from __future__ import annotations

import dagster as dg
from conftest import materialize_via_dagster

from lightcone.engine.status import get_all_universe_status, get_output_status


class TestOutputStatus:
    def test_no_results_dir(self, tmp_path):
        """Status should show 'pending' when recipe exists but no results."""
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: test
inputs: []
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
""")
        status = get_output_status(tmp_path, "baseline")
        assert status["result"] == "pending"

    def test_results_exist(self, tmp_path):
        """Status should show 'materialized' when Dagster event exists."""
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: test
inputs: []
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
""")
        instance = dg.DagsterInstance.ephemeral()
        materialize_via_dagster(instance, "baseline", "result")
        status = get_output_status(tmp_path, "baseline", instance=instance)
        assert status["result"] == "materialized"

    def test_output_without_recipe(self, tmp_path):
        """Output with no recipe should show 'no_recipe'."""
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: test
inputs: []
outputs:
  - id: result
    type: metric
    description: "A metric with no recipe yet"
""")
        status = get_output_status(tmp_path, "baseline")
        assert status["result"] == "no_recipe"

    def test_mixed_states(self, tmp_path):
        """All three states should coexist."""
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: test
inputs: []
outputs:
  - id: done_output
    type: metric
    recipe:
      command: python done.py
  - id: pending_output
    type: metric
    recipe:
      command: python pending.py
  - id: no_recipe_output
    type: figure
    description: "Not integrated yet"
""")
        # Materialize only done_output via Dagster
        instance = dg.DagsterInstance.ephemeral()
        materialize_via_dagster(instance, "baseline", "done_output")

        status = get_output_status(tmp_path, "baseline", instance=instance)
        assert status["done_output"] == "materialized"
        assert status["pending_output"] == "pending"
        assert status["no_recipe_output"] == "no_recipe"

    def test_files_on_disk_without_dagster_event_count_as_materialized(self, tmp_path):
        """Files in the IO manager's canonical location are the ground truth.

        When the Dagster instance isn't reachable (no active cluster), status
        falls back to filesystem inspection — the IO manager guarantees
        ``results/<universe>/<output>/`` is canonical, so non-empty means
        the output exists.
        """
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: test
inputs: []
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
""")
        result_dir = tmp_path / "results" / "baseline" / "result"
        result_dir.mkdir(parents=True)
        (result_dir / "output.json").write_text("{}")

        status = get_output_status(tmp_path, "baseline")
        assert status["result"] == "materialized"

    def test_different_universes_independent(self, tmp_path):
        """Materializing in one universe should not affect another."""
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: test
inputs: []
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
""")
        instance = dg.DagsterInstance.ephemeral()
        materialize_via_dagster(instance, "baseline", "result")

        baseline = get_output_status(tmp_path, "baseline", instance=instance)
        assert baseline["result"] == "materialized"
        alt = get_output_status(tmp_path, "alt", instance=instance)
        assert alt["result"] == "pending"


class TestAllUniverseStatus:
    def test_no_universes_dir(self, tmp_path):
        """Should return empty dict when no universes directory exists."""
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: test
inputs: []
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
""")
        result = get_all_universe_status(tmp_path)
        assert result == {}

    def test_multiple_universes(self, tmp_path, monkeypatch):
        """Should return status for each universe YAML file.

        Uses filesystem ground-truth (output dir non-empty = materialized)
        since there's no active cluster in the test.
        """
        astra_yaml = tmp_path / "astra.yaml"
        astra_yaml.write_text("""
version: "1.0"
name: test
inputs: []
outputs:
  - id: result
    type: metric
    recipe:
      command: python run.py
""")
        universes_dir = tmp_path / "universes"
        universes_dir.mkdir()
        (universes_dir / "baseline.yaml").write_text(
            'id: baseline\ndecisions: {}\n'
        )
        (universes_dir / "alt.yaml").write_text(
            'id: alt\ndecisions: {}\n'
        )
        # baseline has on-disk output; alt does not.
        baseline_out = tmp_path / "results" / "baseline" / "result"
        baseline_out.mkdir(parents=True)
        (baseline_out / "output.json").write_text("{}")

        result = get_all_universe_status(tmp_path)
        assert "baseline" in result
        assert "alt" in result
        assert result["baseline"]["result"] == "materialized"
        assert result["alt"]["result"] == "pending"
