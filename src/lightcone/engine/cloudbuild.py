"""Remote image builds through GCP Cloud Build.

The build backend for deployments where no OCI runtime exists on the
host — a JupyterHub user pod on GKE. ``lc build`` tars the project's
**staged build context** (the exact file set the content-addressed tag
hashes), uploads it to a deployment-provided GCS bucket, and submits a
Cloud Build job that pushes the image to the deployment's Artifact
Registry. Auth is the pod's Workload Identity, spoken to the GCE
metadata server — no stored credentials, no SDK dependency, no git
remote required.

Image identity is the same content-addressed scheme as everywhere else
(:func:`lightcone.engine.container.image_identity`); the pushed ref is
``$LIGHTCONE_REGISTRY/lc-<project>:<hash>`` and "is the image up to
date" is a single registry HEAD on that ref — unchanged files never
rebuild, never even upload.

Deployment contract (env vars injected into user pods — see the
hub-deploy ``lightcone`` hub config):

- :data:`~lightcone.engine.container.REGISTRY_ENV` — Artifact Registry
  prefix (``<region>-docker.pkg.dev/<project>/<repo>``); also names the
  GCP project builds run in.
- :data:`BUCKET_ENV` — GCS bucket for build sources and logs. Its
  presence (with the registry) is what selects this backend.
- :data:`SERVICE_ACCOUNT_ENV` (optional) — dedicated build service
  account; the deployment grants it registry-writer rights only.

The pod's identity needs ``cloudbuild.builds.editor``,
``iam.serviceAccountUser`` on the build SA, object create/view on the
bucket, and ``artifactregistry.reader`` for the freshness probe.
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
    ContainerBuildError,
    _populate_build_context,
    deployment_registry,
    image_identity,
    registry_image_ref,
)

#: GCS bucket for build sources/logs. Presence selects this backend.
BUCKET_ENV = "LIGHTCONE_BUILD_BUCKET"

#: Optional dedicated Cloud Build service account (bare email or full
#: ``projects/…/serviceAccounts/…`` resource name).
SERVICE_ACCOUNT_ENV = "LIGHTCONE_BUILD_SERVICE_ACCOUNT"

#: Hard ceiling on one build, seconds (also sent as the Cloud Build
#: timeout). Project images are slim; single-digit minutes is typical.
_BUILD_DEADLINE_S = 1800

_POLL_INTERVAL_S = 5.0

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)

#: Progress callback ``(phase, detail)``; phases are ``cached``,
#: ``staging``, then Cloud Build statuses lowercased (queued/working/…).
ProgressFn = Callable[[str, str], None]


class CloudBuildError(ContainerBuildError):
    """An image could not be produced through Cloud Build.

    Subclasses :class:`ContainerBuildError` so one handler covers every
    way an image can fail to materialize, local or cloud.
    """


def cloudbuild_available() -> bool:
    """Is this environment configured for Cloud Build image builds?"""
    return bool(os.environ.get(BUCKET_ENV)) and deployment_registry() is not None


# ---------------------------------------------------------------------------
# Auth + HTTP plumbing
# ---------------------------------------------------------------------------


def _metadata_access_token() -> str | None:
    """OAuth2 access token from the GCE metadata server, or ``None``.

    On GKE with Workload Identity this returns a token for the
    Kubernetes service account's bound GCP identity. Off-GCP the
    metadata host doesn't resolve and we return ``None`` quickly.
    """
    req = urllib.request.Request(
        _METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    token = payload.get("access_token")
    return token if isinstance(token, str) and token else None


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
# Registry freshness probe
# ---------------------------------------------------------------------------


def registry_image_exists(ref: str) -> bool | None:
    """Does *ref* exist in its registry? ``None`` when unknowable.

    Speaks the Docker Registry v2 API with the metadata-server token
    (Artifact Registry accepts OAuth2 access tokens as Bearer). Returns
    ``None`` — not ``False`` — when there are no credentials or the
    registry can't be reached, so callers can distinguish "absent,
    build it" from "can't tell".
    """
    host, _, path = ref.partition("/")
    repo, _, tag = path.rpartition(":")
    if not (host and repo and tag):
        return None
    token = _metadata_access_token()
    if token is None:
        return None
    url = f"https://{host}/v2/{repo}/manifests/{urllib.parse.quote(tag, safe='')}"
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": (
                "application/vnd.oci.image.index.v1+json, "
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.v2+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json"
            ),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return bool(200 <= resp.status < 300)
    except urllib.error.HTTPError as exc:
        return None if exc.code in (401, 403) else False
    except (urllib.error.URLError, OSError):
        return None


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


def _gcp_project(registry: str) -> str:
    """GCP project id out of an Artifact Registry prefix.

    ``<region>-docker.pkg.dev/<project>/<repo>`` → ``<project>``.
    """
    parts = registry.split("/")
    if len(parts) < 2 or not parts[0].endswith("-docker.pkg.dev"):
        raise CloudBuildError(
            f"{registry!r} is not an Artifact Registry prefix "
            "(expected <region>-docker.pkg.dev/<project>/<repo>); the "
            "Cloud Build backend only targets Artifact Registry."
        )
    return parts[1]


def _submit_build(
    *,
    gcp_project: str,
    bucket: str,
    source_object: str,
    containerfile_name: str,
    image_ref: str,
    token: str,
) -> str:
    """Create the build; return its id."""
    build: dict[str, object] = {
        "source": {"storageSource": {"bucket": bucket, "object": source_object}},
        "steps": [
            {
                "name": "gcr.io/cloud-builders/docker",
                "args": ["build", "-t", image_ref, "-f", containerfile_name, "."],
            }
        ],
        "images": [image_ref],
        "timeout": f"{_BUILD_DEADLINE_S}s",
        # Logs into our own bucket: GCS_ONLY is required when running as
        # a custom service account, and it is where the failure tail
        # comes from.
        "logsBucket": f"gs://{bucket}/logs",
        "options": {"logging": "GCS_ONLY"},
    }
    build_sa = (os.environ.get(SERVICE_ACCOUNT_ENV) or "").strip()
    if build_sa:
        if "/" not in build_sa:
            build_sa = f"projects/{gcp_project}/serviceAccounts/{build_sa}"
        build["serviceAccount"] = build_sa

    url = f"https://cloudbuild.googleapis.com/v1/projects/{gcp_project}/builds"
    status, payload = _request("POST", url, token, body=json.dumps(build).encode())
    op = _json_or_error(status, payload, "Cloud Build submission")
    meta = op.get("metadata")
    build_info = meta.get("build") if isinstance(meta, dict) else None
    build_id = build_info.get("id") if isinstance(build_info, dict) else None
    if not isinstance(build_id, str) or not build_id:
        raise CloudBuildError(
            f"Cloud Build submission returned no build id (response keys: {sorted(op)})."
        )
    return build_id


def _wait_for_build(
    gcp_project: str,
    build_id: str,
    token: str,
    on_progress: ProgressFn | None,
) -> str:
    """Poll until a terminal status; return it."""
    url = (
        f"https://cloudbuild.googleapis.com/v1/projects/{gcp_project}"
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
        if status in (
            "SUCCESS",
            "FAILURE",
            "INTERNAL_ERROR",
            "TIMEOUT",
            "CANCELLED",
            "EXPIRED",
        ):
            return status
        time.sleep(_POLL_INTERVAL_S)
    raise CloudBuildError(f"Timed out waiting for Cloud Build {build_id} to finish.")


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------


def ensure_image(
    project: Path,
    containerfile_spec: str,
    *,
    project_name: str,
    force: bool = False,
    on_progress: ProgressFn | None = None,
) -> str:
    """Make sure the project's image is in the registry; return its ref.

    Content-addressed and git-free: the tag hashes the staged build
    context, so an unchanged environment is a single registry HEAD (no
    build, no upload), and any change builds from the working tree as
    it is right now. *force* skips the freshness probe and rebuilds.
    """
    containerfile = project / containerfile_spec
    if not containerfile.is_file():
        raise CloudBuildError(
            f"Declared container {containerfile_spec!r} not found in {project}."
        )
    registry = deployment_registry()
    bucket = (os.environ.get(BUCKET_ENV) or "").strip().removeprefix("gs://").rstrip("/")
    if registry is None or not bucket:
        raise CloudBuildError(
            "This environment is not configured for Cloud Build: both "
            f"LIGHTCONE_REGISTRY and {BUCKET_ENV} must be set (they are "
            "injected by the deployment)."
        )
    ref = registry_image_ref(project_name, containerfile, project, registry=registry)

    if not force and registry_image_exists(ref) is True:
        if on_progress:
            on_progress("cached", f"{ref} already in the registry")
        return ref

    token = _token()
    gcp_project = _gcp_project(registry)

    if on_progress:
        on_progress("staging", "uploading build context")
    # Content-addressed object name: identical contexts collide into
    # the same object, which is exactly right.
    _, digest = image_identity(project_name, containerfile, project)
    source_object = f"sources/lc-{project_name}-{digest}.tar.gz"
    _upload_source(
        bucket, source_object, _staged_context_tarball(project, containerfile), token
    )

    build_id = _submit_build(
        gcp_project=gcp_project,
        bucket=bucket,
        source_object=source_object,
        containerfile_name=containerfile.name,
        image_ref=ref,
        token=token,
    )
    status = _wait_for_build(gcp_project, build_id, token, on_progress)
    if status != "SUCCESS":
        tail = _fetch_log_tail(bucket, build_id, token)
        raise CloudBuildError(
            f"Cloud Build {build_id} ended with status {status}."
            + (f" Last build output:\n{tail}" if tail else "")
        )
    return ref
