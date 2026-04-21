"""ASTRA IO Manager for Dagster — maps (asset, universe) to filesystem paths."""
from __future__ import annotations

from pathlib import Path


class ASTRAIOManager:
    """Maps ASTRA outputs to filesystem paths following ASTRA conventions.

    Path convention: results/<universe_id>/<output_id>/
    """

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)

    def get_output_path(self, output_id: str, universe_id: str) -> Path:
        """Get the filesystem path for an output in a given universe."""
        return self.project_root / "results" / universe_id / output_id

    def get_input_paths(
        self, input_ids: list[str], universe_id: str
    ) -> dict[str, Path]:
        """Get filesystem paths for input dependencies."""
        return {
            inp_id: self.get_output_path(inp_id, universe_id)
            for inp_id in input_ids
        }
