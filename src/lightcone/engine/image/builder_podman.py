"""The podman builder: build invocation and pointed error mapping.

Every failure class surfaces as a specific error with an actionable
message — never a raw build log (the Modal lesson): the generated
contract-check layer's distinct exit codes map to
:class:`BaseContractError`, apt's "Unable to locate package" maps to
:class:`AptPackageNotFoundError`, a manifest-list architecture miss
maps to the platform contract, and anything else carries a bounded log
tail.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections import deque
from pathlib import Path

from lightcone.engine.image import constants
from lightcone.engine.image.builder import BuildContext, BuildResult
from lightcone.engine.image.errors import (
    AptPackageNotFoundError,
    BaseContractError,
    ImageBuildError,
    PodmanUnavailableError,
)

_APT_NOT_FOUND_RE = re.compile(r"E: Unable to locate package (\S+)")
_EXIT_STATUS_RE = re.compile(r"exit (?:status|code):? (\d+)")
_ARCH_MISS_RE = re.compile(r"no image found in manifest list for architecture")

_LOG_TAIL_LINES = 60

_CONTRACT_MESSAGES = {
    constants.EXIT_NO_SH: (
        "the base image has no POSIX shell at /bin/sh — build stages run "
        "through it. Use a base that provides one."
    ),
    constants.EXIT_MUSL_BASE: (
        "the base image is musl-based (Alpine?) — manylinux wheels and "
        "uv-managed interpreters require glibc. Use a glibc base "
        "(the default Debian base, or a Debian/Ubuntu-family ref)."
    ),
    constants.EXIT_NO_APT: (
        "the base image has no apt, but system-packages are declared. "
        "Two escapes: use a Debian/Ubuntu-family base, or move the "
        "install into Containerfile.extra."
    ),
}


class PodmanBuilder:
    def __init__(self, podman: str = "podman") -> None:
        self._podman = podman
        if shutil.which(podman) is None:
            raise PodmanUnavailableError(
                "podman is not on PATH — the container hatch needs rootless "
                "podman. Install it (https://podman.io/docs/installation); "
                "`lc status` shows this host's readiness."
            )

    def exists(self, tag: str) -> bool:
        proc = subprocess.run(
            [self._podman, "image", "exists", tag],
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0

    def build(self, context: BuildContext, *, tag: str) -> BuildResult:
        with tempfile.TemporaryDirectory(prefix="lc-image-") as staged:
            containerfile = context.stage(Path(staged))
            proc = subprocess.run(
                [
                    self._podman, "build",
                    "--file", str(containerfile),
                    "--tag", tag,
                    staged,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if proc.returncode != 0:
            self._raise_mapped(proc.stdout, proc.stderr)

        image_id, digest, platform = self._inspect(tag)
        snapshot = self._capture_snapshot(tag)
        return BuildResult(
            tag=tag,
            image_id=image_id,
            digest=digest,
            platform=platform,
            dpkg_snapshot_text=snapshot,
        )

    def _raise_mapped(self, stdout: str, stderr: str) -> None:
        combined = stdout + "\n" + stderr
        if m := _APT_NOT_FOUND_RE.search(combined):
            raise AptPackageNotFoundError(m.group(1))
        if _ARCH_MISS_RE.search(combined):
            raise BaseContractError(
                "the declared base provides no image for this architecture "
                "— the base contract requires linux/amd64 (linux/arm64 is "
                "used on Apple silicon when available)."
            )
        if m := _EXIT_STATUS_RE.search(combined):
            code = int(m.group(1))
            if code in _CONTRACT_MESSAGES:
                raise BaseContractError(_CONTRACT_MESSAGES[code])
        tail = "\n".join(
            deque((stdout + stderr).splitlines(), maxlen=_LOG_TAIL_LINES)
        )
        raise ImageBuildError(f"podman build failed:\n{tail}")

    def _inspect(self, tag: str) -> tuple[str, str | None, str]:
        proc = subprocess.run(
            [
                self._podman, "image", "inspect", tag,
                "--format", "{{.Id}}|{{.Digest}}|{{.Os}}/{{.Architecture}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ImageBuildError(
                f"podman image inspect {tag} failed after a successful "
                f"build:\n{proc.stderr.strip()}"
            )
        image_id, digest, platform = proc.stdout.strip().split("|", 2)
        if not image_id.startswith("sha256:"):
            image_id = f"sha256:{image_id}"
        return image_id, (digest or None), platform

    def _capture_snapshot(self, tag: str) -> str:
        """Read the baked dpkg snapshot (taken in the final build stage,
        after any extra stage, so it attests everything installed)."""
        proc = subprocess.run(
            [
                self._podman, "run", "--rm", "--pull=never", "--net=none",
                "--entrypoint=", tag,
                "cat", constants.DPKG_SNAPSHOT_PATH,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return "snapshot unavailable"
        return proc.stdout
