"""DataStore — resolves ASTRA input definitions to local filesystem paths."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astra.helpers import get_inputs

from prism.dagster.staging import (
    _is_local,
    _resolve_local,
    stage_uri,
    verify_checksum,
)

logger = logging.getLogger(__name__)


@dataclass
class ResolvedInput:
    """A resolved input with its local path and provenance metadata."""

    input_id: str
    local_path: Path
    source_uri: str
    checksum: dict[str, str] | None
    verified: bool
    staged: bool


class DataStore:
    """Resolves ASTRA input sources to local filesystem paths.

    Handles local absolute paths, relative paths, and remote URIs.
    Remote data is staged to a local cache directory via fsspec.
    """

    def __init__(
        self,
        project_root: Path,
        cache_dir: Path | None = None,
    ) -> None:
        self.project_root = project_root
        self.cache_dir = cache_dir or Path.home() / ".prism" / "cache"

    def resolve_input(
        self,
        input_def: dict[str, Any],
        universe_id: str,
    ) -> ResolvedInput:
        """Resolve a single input definition to a local path.

        Parameters
        ----------
        input_def:
            An input dict from ``astra.yaml`` (must have ``"id"``; may have
            ``"source"`` and ``"checksum"``).
        universe_id:
            The universe being materialized (used for internal inputs).

        Returns
        -------
        ResolvedInput
            Resolved input with a local filesystem path.
        """
        source = input_def.get("source")
        input_id: str = input_def["id"]
        checksum = input_def.get("checksum")

        if not source:
            # Internal input — sibling output
            local_path = self.project_root / "results" / universe_id / input_id
            return ResolvedInput(
                input_id=input_id,
                local_path=local_path,
                source_uri=f"results://{universe_id}/{input_id}",
                checksum=None,
                verified=False,
                staged=False,
            )

        if _is_local(source):
            local_path = _resolve_local(source, self.project_root)
            if not local_path.exists():
                logger.warning(
                    "Input '%s' source not found: %s (from source: %s). "
                    "It may appear before execution.",
                    input_id, local_path, source,
                )
            verified = (
                verify_checksum(local_path, checksum)
                if checksum and local_path.exists()
                else False
            )
            return ResolvedInput(
                input_id=input_id,
                local_path=local_path,
                source_uri=source,
                checksum=checksum,
                verified=verified,
                staged=False,
            )

        # Remote source — stage via fsspec
        local_path = stage_uri(source, self.cache_dir, checksum=checksum)
        verified = verify_checksum(local_path, checksum) if checksum else False
        return ResolvedInput(
            input_id=input_id,
            local_path=local_path,
            source_uri=source,
            checksum=checksum,
            verified=verified,
            staged=True,
        )

    def resolve_external_inputs(
        self,
        spec: dict[str, Any],
        universe_id: str,
    ) -> dict[str, str]:
        """Resolve all inputs with sources to ``{input_id: local_path_str}``.

        Drop-in replacement for ``get_external_inputs()`` — returns the same
        dict shape but supports any URI, not just absolute paths.
        """
        result: dict[str, str] = {}
        for inp in get_inputs(spec):
            source = inp.get("source")
            if not source:
                continue
            resolved = self.resolve_input(inp, universe_id)
            result[resolved.input_id] = str(resolved.local_path)
        return result
