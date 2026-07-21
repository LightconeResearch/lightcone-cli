"""On-hub worker-image builds through GCP Cloud Build.

The alternative to :mod:`lightcone.engine.binder` for GCP-hosted
deployments, and the preferred one where available: builds run in
Google-managed VMs entirely off-cluster (no privileged builder pods),
auth is pure IAM through the pod's Workload Identity (no stored
credentials anywhere), and — the big UX difference — the build source is
a tarball of the project's **staged build context**, not a git ref. No
GitHub remote, no commit, no push is needed to build; private projects
build exactly like public ones.

Image identity is the content-addressed scheme the CLI already uses
everywhere else: the tag hashes the staged context
(:func:`lightcone.engine.container.compute_image_tag`), the pushed ref
is ``$LIGHTCONE_REGISTRY/lc-<project>:<hash>``, and "is the image up to
date" is a registry HEAD on that ref — unchanged files never rebuild.

Deployment contract (injected into user pods):

- :data:`~lightcone.engine.container.REGISTRY_ENV` — the Artifact
  Registry prefix images are pushed to (also names the GCP project and
  region).
- :data:`BUCKET_ENV` — a GCS bucket for build sources and logs; its
  presence is what selects this backend.
- :data:`SERVICE_ACCOUNT_ENV` (optional) — a dedicated build service
  account (``projects/-/serviceAccounts/<email>``); recommended so the
  builder holds only registry-writer rights.

The pod's Workload Identity needs ``cloudbuild.builds.editor``,
``iam.serviceAccountUser`` on the build SA, and object admin on the
bucket. Everything speaks plain REST via ``urllib`` with a
metadata-server token — no SDK dependency.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from lightcone.engine.container import (
    _metadata_access_token,
    _populate_build_context,
    compute_image_tag,
    deployment_registry,
    registry_image_exists,
    registry_image_ref,
)

#: GCS bucket for build sources/logs. Presence selects this backend.
BUCKET_ENV = "LIGHTCONE_BUILD_BUCKET"

#: Optional dedicated Cloud Build service account (full resource name or
#: bare email). Builds with a custom SA are the least-privilege setup.
SERVICE_ACCOUNT_ENV = "LIGHTCONE_BUILD_SERVICE_ACCOUNT"

#: Hard ceiling on one build, seconds (also sent as the Cloud Build
#: timeout). Slim project images finish in single-digit minutes.
_BUILD_DEADLINE_S = 1800

_POLL_INTERVAL_S = 5.0

#: Progress callback ``(phase, message)`` — same shape as the binder
#: backend so the CLI renders both identically. Phases are Cloud Build
#: statuses lowercased (queued/working/success/failure...).
ProgressFn = Callable[[str, str], None]


class CloudBuildError(RuntimeError):
    """A worker image could not be produced through Cloud Build."""


def cloudbuild_available() -> bool:
    """Is this deployment configured for Cloud Build image builds?"""
    return bool(os.environ.get(BUCKET_ENV)) and deployment_registry() is not None


def _bucket() -> str:
    bucket = (os.environ.get(BUCKET_ENV) or "").strip().removeprefix("gs://")
    if not bucket:
        raise CloudBuildError(f"{BUCKET_ENV} is not set.")
    return bucket.rstrip("/")


def _project_from_registry(registry: str) -> str:
    """GCP project id out of an Artifact Registry prefix.

    ``us-central1-docker.pkg.dev/<project>/<repo>`` → ``<project>``.
    """
    parts = registry.split("/")
    if len(parts) < 2 or not parts[0].endswith("-docker.pkg.dev"):
        raise CloudBuildError(
            f"{registry!r} is not an Artifact Registry prefix "
            "(expected <region>-docker.pkg.dev/<project>/<repo>); the "
            "Cloud Build backend only targets Artifact Registry."
        )
    return parts[1]


def _token() -> str:
    token = _metadata_access_token()
    if token is None:
        raise CloudBuildError(
            "No GCP credentials available from the metadata server. The "
            "Cloud Build backend needs Workload Identity (or another "
            "metadata-served identity) with cloudbuild.builds.editor."
        )
    return token


def _request(
    method: str,
    url: str,
    token: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            **({"Content-Type": content_type} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise CloudBuildError(f"Could not reach {url.split('?')[0]} ({exc}).") from exc


def _json_or_error(status: int, payload: bytes, what: str) -> dict[str, object]:
    if not 200 <= status < 300:
        detail = payload.decode("utf-8", errors="replace")[:500]
        raise CloudBuildError(f"{what} failed: HTTP {status}\n{detail}")
    try:
        parsed = json.loads(payload.decode("utf-8") or "{}")
    except ValueError as exc:
        raise CloudBuildError(f"{what} returned unparseable JSON.") from exc
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Source staging + upload
# ---------------------------------------------------------------------------


def _staged_context_tarball(project: Path, containerfile: Path) -> bytes:
    """gzip tarball of the staged build context (the hashed file set)."""
    with tempfile.TemporaryDirectory(prefix="lc-cloudbuild-") as tmp:
        staged = Path(tmp)
        _populate_build_context(staged, containerfile, project)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for entry in sorted(staged.rglob("*")):
                tar.add(entry, arcname=str(entry.relative_to(staged)))
        return buf.getvalue()


def _upload_source(bucket: str, object_name: str, data: bytes, token: str) -> None:
    url = (
        "https://storage.googleapis.com/upload/storage/v1/b/"
        f"{urllib.parse.quote(bucket, safe='')}/o?uploadType=media&name="
        f"{urllib.parse.quote(object_name, safe='')}"
    )
    status, payload = _request(
        "POST", url, token, body=data, content_type="application/gzip"
    )
    _json_or_error(status, payload, "Source upload to the build bucket")


def _fetch_log_tail(bucket: str, build_id: str, token: str, lines: int = 30) -> str:
    object_name = urllib.parse.quote(f"logs/log-{build_id}.txt", safe="")
    url = (
        "https://storage.googleapis.com/storage/v1/b/"
        f"{urllib.parse.quote(bucket, safe='')}/o/{object_name}?alt=media"
    )
    try:
        status, payload = _request("GET", url, token)
    except CloudBuildError:
        return ""
    if not 200 <= status < 300:
        return ""
    text = payload.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


# ---------------------------------------------------------------------------
# Build submission + polling
# ---------------------------------------------------------------------------


def _submit_build(
    *,
    project_id: str,
    bucket: str,
    source_object: str,
    containerfile_name: str,
    image_ref: str,
    token: str,
) -> str:
    """Create the build; return its id."""
    build: dict[str, object] = {
        "source": {
            "storageSource": {"bucket": bucket, "object": source_object}
        },
        "steps": [
            {
                "name": "gcr.io/cloud-builders/docker",
                "args": [
                    "build",
                    "-t",
                    image_ref,
                    "-f",
                    containerfile_name,
                    ".",
                ],
            }
        ],
        "images": [image_ref],
        "timeout": f"{_BUILD_DEADLINE_S}s",
        # GCS_ONLY logging into our own bucket: required for custom
        # service accounts, and it is where the failure tail comes from.
        "logsBucket": f"gs://{bucket}/logs",
        "options": {"logging": "GCS_ONLY"},
    }
    build_sa = (os.environ.get(SERVICE_ACCOUNT_ENV) or "").strip()
    if build_sa:
        if "/" not in build_sa:
            build_sa = f"projects/{project_id}/serviceAccounts/{build_sa}"
        build["serviceAccount"] = build_sa

    url = f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/builds"
    status, payload = _request(
        "POST", url, token, body=json.dumps(build).encode()
    )
    op = _json_or_error(status, payload, "Cloud Build submission")
    meta = op.get("metadata")
    build_info = meta.get("build") if isinstance(meta, dict) else None
    build_id = build_info.get("id") if isinstance(build_info, dict) else None
    if not isinstance(build_id, str) or not build_id:
        raise CloudBuildError(
            "Cloud Build submission returned no build id "
            f"(response keys: {sorted(op)})."
        )
    return build_id


def _wait_for_build(
    project_id: str,
    build_id: str,
    token: str,
    on_progress: ProgressFn | None,
) -> str:
    """Poll until a terminal status; return it."""
    url = (
        f"https://cloudbuild.googleapis.com/v1/projects/{project_id}"
        f"/builds/{build_id}"
    )
    deadline = time.monotonic() + _BUILD_DEADLINE_S + 120
    last_status = ""
    while time.monotonic() < deadline:
        status_code, payload = _request("GET", url, token)
        build = _json_or_error(status_code, payload, "Cloud Build status poll")
        status = str(build.get("status") or "")
        if status != last_status:
            last_status = status
            if on_progress:
                on_progress(status.lower(), "")
        if status in ("SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"):
            return status
        time.sleep(_POLL_INTERVAL_S)
    raise CloudBuildError(
        f"Timed out waiting for Cloud Build {build_id} to finish."
    )


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------


def ensure_worker_image(
    project: Path,
    containerfile_spec: str,
    *,
    project_name: str | None = None,
    on_progress: ProgressFn | None = None,
) -> str:
    """Make sure the project's worker image is built; return its registry ref.

    Content-addressed and git-free: the tag hashes the staged build
    context, so an unchanged environment is a single registry HEAD
    (no build, no upload), and any change builds from the working tree
    as it is right now.
    """
    containerfile = project / containerfile_spec
    if not containerfile.is_file():
        raise CloudBuildError(
            f"Declared container {containerfile_spec!r} not found in {project}."
        )
    registry = deployment_registry()
    if registry is None:
        raise CloudBuildError(
            "LIGHTCONE_REGISTRY is not set; the Cloud Build backend needs "
            "the Artifact Registry prefix to name and probe images."
        )
    # Same tag the local `lc build` computes (project name from
    # astra.yaml when the caller has it) — one content-addressed identity
    # across every backend.
    name = (project_name or project.name).lower().replace(" ", "-")
    tag = compute_image_tag(name, containerfile, project)
    ref = registry_image_ref(tag, registry=registry)
    if ref is None:
        raise CloudBuildError(f"Could not derive a registry ref from {tag!r}.")

    if registry_image_exists(ref) is True:
        if on_progress:
            on_progress("cached", f"{ref} already in the registry")
        return ref

    token = _token()
    bucket = _bucket()
    project_id = _project_from_registry(registry)

    if on_progress:
        on_progress("staging", "uploading build context")
    # Content-addressed object name: identical contexts collide into the
    # same object, which is exactly right.
    source_object = f"sources/{tag}.tar.gz"
    _upload_source(bucket, source_object, _staged_context_tarball(project, containerfile), token)

    build_id = _submit_build(
        project_id=project_id,
        bucket=bucket,
        source_object=source_object,
        containerfile_name=containerfile.name,
        image_ref=ref,
        token=token,
    )
    status = _wait_for_build(project_id, build_id, token, on_progress)
    if status != "SUCCESS":
        tail = _fetch_log_tail(bucket, build_id, token)
        raise CloudBuildError(
            f"Cloud Build {build_id} ended with status {status}."
            + (f" Last build output:\n{tail}" if tail else "")
        )
    return ref
