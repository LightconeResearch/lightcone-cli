"""Engine constants for the image layer.

These digests ship inside the locked engine, so new constants reach a
project only through an engine release + relock — the image tag and
``env_version`` move together (spec §3). They are never resolved at
run time.

Bump procedure: resolve the current manifest-*list* digests (they must
cover linux/amd64 and linux/arm64 so the rendered Containerfile text —
and therefore the tag — stays architecture-independent) and paste them
here, e.g.::

    podman manifest inspect docker.io/library/debian@sha256:<candidate>
    # must report an OCI image index with amd64 + arm64 entries

Honest residue, documented: ``uv python install`` fetches a
python-build-standalone interpreter pinned by (uv version,
``.python-version``), not by a digest we hold — attestation-grade
interpreter identity is recorded in the manifest (``python_build``),
digest-pinning it is a hardening candidate alongside apt snapshot
pinning.
"""
from __future__ import annotations

#: Default base image (used when ``[tool.lightcone.image]`` declares no
#: ``base``). Digest is the manifest-LIST digest — one spelling covers
#: amd64 and arm64.
DEFAULT_BASE_NAME = "docker.io/library/debian:bookworm-slim"
DEFAULT_BASE_DIGEST = (
    "sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241"
)

#: The pinned uv distribution: the official uv image, copied from by
#: digest (``COPY --from=ghcr.io/astral-sh/uv@<digest> /uv …``).
#: Manifest-list digest, same arch-independence rationale.
UV_VERSION = "0.12.3"
UV_IMAGE = "ghcr.io/astral-sh/uv"
UV_IMAGE_DIGEST = (
    "sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc"
)

#: Exact interpreter patch scaffolded into ``.python-version`` by
#: ``lc init`` (projects may pin a different one; identity follows the
#: file, not this constant).
DEFAULT_PYTHON = "3.12.12"

#: In-image filesystem layout. ``/opt`` because the base contract
#: guarantees nothing about the base beyond an OS layer — these paths
#: are lc's own namespace.
OPT_PYTHON = "/opt/python"
OPT_VENV = "/opt/venv"
LC_DIR = "/opt/lc"
UV_BIN = f"{LC_DIR}/bin/uv"
PROJECT_STAGE_DIR = f"{LC_DIR}/project"
DPKG_SNAPSHOT_PATH = f"{LC_DIR}/dpkg-snapshot.txt"
IDENTITY_PATH = f"{LC_DIR}/identity.json"

#: Contract-check exit codes emitted by the generated Containerfile's
#: check layer; the builder maps them back to BaseContractError.
EXIT_NO_SH = 41
EXIT_MUSL_BASE = 43
EXIT_NO_APT = 44

#: The offline overlay baked into the image's FINAL stage only — the
#: build's own ``uv sync`` layer must keep network (spec §11 step 6;
#: ordering pinned by golden tests).
OFFLINE_ENV = {
    "UV_OFFLINE": "1",
    "UV_PYTHON_DOWNLOADS": "never",
    "UV_NO_SYNC": "1",
}
