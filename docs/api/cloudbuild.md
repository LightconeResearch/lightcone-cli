# lightcone.engine.cloudbuild

Remote image builds through GCP Cloud Build — the build backend for
deployments with no OCI runtime on the host (a JupyterHub user pod on
GKE). Pure `urllib` REST against the metadata server, GCS, the Cloud
Build API, and the Docker Registry v2 API; no SDK dependency, no
stored credentials, no git remote required.

Source: `src/lightcone/engine/cloudbuild.py`.

## Deployment contract

Env vars injected into every user pod by the deployment (see the
hub-deploy `lightcone` hub config):

| Env var | Meaning |
|---------|---------|
| `LIGHTCONE_REGISTRY` | Artifact Registry prefix (`<region>-docker.pkg.dev/<project>/<repo>`); also names the GCP project builds run in. Declared in `engine.container` (`REGISTRY_ENV`). |
| `LIGHTCONE_BUILD_BUCKET` | GCS bucket for build sources and logs. Its presence (with the registry) selects this backend — `cloudbuild_available()`. |
| `LIGHTCONE_BUILD_SERVICE_ACCOUNT` | Optional dedicated build SA; the deployment grants it registry-writer rights only. |

Auth is the pod's Workload Identity, spoken to the GCE metadata server
(`_metadata_access_token()`). The pod's identity needs
`cloudbuild.builds.editor`, `iam.serviceAccountUser` on the build SA,
object create/view on the bucket, and `artifactregistry.reader` for
the freshness probe.

## `ensure_image(project, containerfile_spec, *, project_name, force=False, on_progress=None) → str`

Make sure the project's image is in the registry; return its ref.
Content-addressed and git-free:

1. Compute the ref `$LIGHTCONE_REGISTRY/lc-<project>:<hash>` — the
   same `image_identity()` digest as the local `lc-<project>-<hash>`
   tag, spelled for a registry.
2. `registry_image_exists(ref)` — one HEAD on the Docker Registry v2
   manifest endpoint. Present → done (no build, no upload). `force`
   skips this probe.
3. Tar the **staged build context** (`_populate_build_context` — the
   exact file set the tag hashes) and upload it to the bucket under a
   content-addressed object name.
4. Submit the build (docker builder step, image push, logs to
   `gs://<bucket>/logs` with `GCS_ONLY` — required for custom SAs and
   the source of the failure tail), poll to a terminal status.
5. Non-`SUCCESS` → `CloudBuildError` carrying the build-log tail.

`on_progress(phase, detail)` phases: `cached`, `staging`, then Cloud
Build statuses lowercased (`queued`, `working`, `success`, …).

## `registry_image_exists(ref) → bool | None`

`None` — not `False` — when unknowable (no metadata credentials,
registry unreachable), so callers can distinguish "absent, build it"
from "can't tell". Artifact Registry accepts the OAuth2 access token
directly as a Bearer on `/v2/` endpoints.

## Tests

`tests/test_cloudbuild.py` mocks the two HTTP seams
(`_metadata_access_token`, `_request`) and exercises the real control
flow: backend selection, freshness probe, staging, submission
(including the custom-SA payload), polling, failure-tail reporting,
and the staged-tarball ↔ hashed-context equivalence.
