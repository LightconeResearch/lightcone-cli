"""Content-addressed image identity.

``tag = lc-env-<sha256(framed(rendered Containerfile) ‖ framed(pyproject)
‖ framed(uv.lock))[:16]>``

The rendered text already embeds every other identity input verbatim —
base digest, uv digest, interpreter version, sorted apt list, extra
stage — so hashing it plus the two build-context files covers the spec
§3 env-input document exactly. ``pyproject.toml`` and ``uv.lock`` are
hashed raw-bytes because they enter the build context and determine
``/opt/venv`` (raw-bytes over-invalidation is the carried-over honest
boundary). Project code contributes nothing (G5): code edits never
move the tag.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from lightcone.engine.image.errors import DeclarationError
from lightcone.engine.image.render import RenderedContainerfile

TAG_PREFIX = "lc-env-"


@dataclass(frozen=True)
class EnvInputs:
    """The two project files that enter the build context."""

    pyproject_bytes: bytes
    uv_lock_bytes: bytes

    @classmethod
    def read(cls, project: Path) -> EnvInputs:
        pyproject = project / "pyproject.toml"
        uv_lock = project / "uv.lock"
        for p in (pyproject, uv_lock):
            if not p.is_file():
                raise DeclarationError(
                    f"{p} is missing — the image is rendered from the "
                    "locked environment. Run `uv lock` (or `lc init`) first."
                )
        return cls(
            pyproject_bytes=pyproject.read_bytes(),
            uv_lock_bytes=uv_lock.read_bytes(),
        )


def _frame(h: hashlib._Hash, label: str, data: bytes) -> None:
    """Length-framed update — stops boundary-shifting collisions."""
    h.update(label.encode("utf-8"))
    h.update(b"\0")
    h.update(str(len(data)).encode("ascii"))
    h.update(b"\0")
    h.update(data)


def compute_tag(rendered: RenderedContainerfile, inputs: EnvInputs) -> str:
    h = hashlib.sha256()
    _frame(h, "containerfile", rendered.text.encode("utf-8"))
    _frame(h, "pyproject", inputs.pyproject_bytes)
    _frame(h, "uv.lock", inputs.uv_lock_bytes)
    return f"{TAG_PREFIX}{h.hexdigest()[:16]}"
