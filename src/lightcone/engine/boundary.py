"""The exec boundary — the seam where recipes meet enforcement.

``run_rule`` never runs a recipe directly: it hands the command and its
declared scope to an :class:`ExecBoundary`. The sandbox layer provides
the real boundary (Landlock on Linux, Seatbelt on macOS, in-container
Landlock under podman); until it is wired in, the
:class:`HostPassthroughBoundary` runs the command bare and attests
honestly to ``mechanism: none`` — a manifest must never claim
enforcement that did not happen.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ExecScope:
    """What the recipe is declared to touch."""

    project_root: Path
    output_dir: Path | None  # None for probes (no in-tree write scope)
    read_paths: tuple[Path, ...]  # declared inputs
    writable_project: bool = False
    sandbox: Literal["on", "off", "debug"] = "on"


@dataclass(frozen=True)
class SandboxAttestation:
    """The hermeticity record for one exec — the applied flags, never
    the documentation."""

    mechanism: str  # landlock|seatbelt|podman|podman+landlock|none
    fs: str  # declared|project-rw|os-only|open
    network: str  # denied|allowed|unenforced
    landlock_abi: int | None = None
    exec_allowlist_version: int | None = None

    def to_manifest(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "mechanism": self.mechanism,
            "fs": self.fs,
            "network": self.network,
        }
        if self.landlock_abi is not None:
            record["landlock_abi"] = self.landlock_abi
        if self.exec_allowlist_version is not None:
            record["exec_allowlist_version"] = self.exec_allowlist_version
        return record


@dataclass(frozen=True)
class BoundaryResult:
    returncode: int
    stdout: str
    stderr: str
    attestation: SandboxAttestation
    #: Extra console lines the boundary wants surfaced (downgrade
    #: notices, denial explanations) — the caller emits them verbatim.
    notes: tuple[str, ...] = field(default=())


class ExecBoundary(Protocol):
    def probe(self, scope: ExecScope) -> SandboxAttestation:
        """What enforcement WOULD apply to this scope on this host —
        checked worker-side per job (the driver's kernel is not the
        worker's), and how ``--require-sandbox`` refuses before exec."""
        ...

    def execute(
        self,
        command: str,
        scope: ExecScope,
        env: dict[str, str],
    ) -> BoundaryResult: ...

    def describe_host(self) -> str:
        """One line for the ``lc status`` sandbox header."""
        ...


class HostPassthroughBoundary:
    """No enforcement: run the command bare, attest to none."""

    _ATTESTATION = SandboxAttestation(mechanism="none", fs="open", network="allowed")

    def probe(self, scope: ExecScope) -> SandboxAttestation:
        return self._ATTESTATION

    def execute(
        self,
        command: str,
        scope: ExecScope,
        env: dict[str, str],
    ) -> BoundaryResult:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            cwd=scope.project_root,
            env=env,
        )
        return BoundaryResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            attestation=self._ATTESTATION,
        )

    def describe_host(self) -> str:
        return "none (fs: open)"


def get_boundary() -> ExecBoundary:
    """The active exec boundary.

    Returns the sandbox layer's boundary once it lands; the passthrough
    until then.
    """
    return HostPassthroughBoundary()
