"""On-hub worker-image builds through the BinderHub service.

A lightcone-hub deployment runs 2i2c's *binderhub-service* in API-only
mode: an authenticated HTTP endpoint that clones a git ref, builds it
with repo2docker, and pushes the image to the deployment's container
registry (LCR-176 Part B). There is no docker daemon in a user pod, so
this is *the* way `lc build` publishes images from the hub.

The flow implemented by :func:`ensure_worker_image`:

1. Identify the **environment ref** — the last commit that touched any
   environment-defining file (Containerfile, dependency files, COPY'd
   sources; see :func:`lightcone.engine.container.env_context_paths`).
   Code-only commits reuse the previous image.
2. If any of those files have uncommitted changes, commit them (scoped
   to exactly those paths) — the PRD's "lc build commits the current
   version of the code" step, kept minimal and predictable.
3. Push the ref when the remote doesn't have it yet (BinderHub build
   pods clone from the remote; they cannot see the hub filesystem).
4. Ask the BinderHub service to build that ref (an SSE stream). The
   service resolves the ref, checks the registry, and returns the image
   name immediately when it is already built — so this is cheap to call
   on every ``lc run``, which is exactly what makes "the image is
   always up to date" transparent.

Auth rides on the ambient JupyterHub identity: every singleuser pod
carries ``JUPYTERHUB_API_TOKEN``, which the binderhub-service accepts as
a JupyterHub-authenticated caller.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from pathlib import Path

#: Override for the BinderHub service URL. Defaults to the in-cluster
#: address every lightcone-hub singleuser pod can reach.
BINDER_URL_ENV = "LIGHTCONE_BINDER_URL"

#: In-cluster address of the binderhub-service behind the JupyterHub
#: proxy on a lightcone-hub deployment (``proxy-public`` resolves inside
#: the hub namespace; the service is mounted at ``/services/binder``).
DEFAULT_BINDER_URL = "http://proxy-public/services/binder"

#: JupyterHub-issued API token, present in every singleuser pod. Used
#: both as the auth credential for the binder service and as the "are we
#: on a hub?" marker for :func:`binder_available`.
API_TOKEN_ENV = "JUPYTERHUB_API_TOKEN"

#: Hard ceiling on one build, seconds. repo2docker builds of a slim
#: python image finish in single-digit minutes; an hour means something
#: is wedged, not slow.
_BUILD_DEADLINE_S = 3600

#: Per-read socket timeout, seconds. BinderHub streams an SSE event at
#: least every few seconds while building (log lines) and keepalives
#: while waiting; minutes of silence means the stream is dead.
_STREAM_READ_TIMEOUT_S = 300

#: Type of the progress callback: ``(phase, message)``. *message* may be
#: empty; *phase* is BinderHub's event phase (waiting/fetching/building/
#: pushing/built/ready/failed) or ``""`` for log-only events.
ProgressFn = Callable[[str, str], None]


class BinderBuildError(RuntimeError):
    """A worker image could not be produced through the binder service."""


def binder_service_url() -> str | None:
    """The BinderHub service URL to use, or ``None`` when unavailable.

    An explicit :data:`BINDER_URL_ENV` always wins (also the seam tests
    and off-hub experiments use). Otherwise the in-cluster default
    applies only where it can work: on a hub pod, marked by the ambient
    :data:`API_TOKEN_ENV` credential the service authenticates with.
    """
    explicit = (os.environ.get(BINDER_URL_ENV) or "").strip().rstrip("/")
    if explicit:
        return explicit
    if os.environ.get(API_TOKEN_ENV):
        return DEFAULT_BINDER_URL
    return None


def binder_available() -> bool:
    """Can this process reach an authenticated BinderHub service?"""
    return binder_service_url() is not None and bool(
        os.environ.get(API_TOKEN_ENV)
    )


# ---------------------------------------------------------------------------
# Git plumbing
# ---------------------------------------------------------------------------


def _git(project: Path, *args: str) -> str:
    """Run git in *project*, returning stripped stdout; raise on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BinderBuildError(
            f"`git {' '.join(args)}` failed in {project}: {detail}"
        )
    return result.stdout.strip()


def _github_org_repo(remote_url: str) -> tuple[str, str] | None:
    """``(org, repo)`` when *remote_url* points at GitHub, else ``None``.

    Handles the three shapes git produces: ``git@github.com:org/repo.git``,
    ``https://github.com/org/repo(.git)``, ``ssh://git@github.com/org/repo``.
    """
    url = remote_url.strip()
    path = None
    if url.startswith("git@github.com:"):
        path = url[len("git@github.com:"):]
    else:
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname == "github.com":
            path = parsed.path.lstrip("/")
    if not path:
        return None
    if path.endswith(".git"):
        path = path[: -len(".git")]
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def repo_provider_spec(remote_url: str, ref: str) -> str:
    """BinderHub ``/build/<spec>`` path for *remote_url* at *ref*.

    GitHub remotes use the ``gh`` provider (``gh/<org>/<repo>/<ref>``);
    anything else falls back to the generic ``git`` provider, whose spec
    is the URL-escaped clone URL. Both accept a full commit sha as ref,
    which is what we always pass — the resolved ref *is* the image tag,
    so a moving branch name would break image identity.
    """
    gh = _github_org_repo(remote_url)
    if gh is not None:
        org, repo = gh
        return f"gh/{urllib.parse.quote(org)}/{urllib.parse.quote(repo)}/{ref}"
    return f"git/{urllib.parse.quote(remote_url, safe='')}/{ref}"


def _ensure_dockerfile(
    project: Path, containerfile_spec: str, pathspecs: list[str]
) -> None:
    """Make the repo buildable by repo2docker (it looks for ``Dockerfile``).

    lightcone projects declare a ``Containerfile``; repo2docker only
    recognizes ``Dockerfile`` (repo root or ``binder/``/``.binder/``).
    A relative symlink at the repo root bridges the two — committed like
    any other file, it survives the clone the build pod makes.
    """
    if containerfile_spec == "Dockerfile":
        return
    dockerfile = project / "Dockerfile"
    if dockerfile.is_symlink():
        if os.readlink(dockerfile) == containerfile_spec:
            if "Dockerfile" not in pathspecs:
                pathspecs.append("Dockerfile")
            return
        raise BinderBuildError(
            f"Dockerfile is a symlink to {os.readlink(dockerfile)!r}, not "
            f"to the declared container {containerfile_spec!r}. Fix the "
            "symlink (repo2docker builds the Dockerfile, so it must point "
            "at the declared Containerfile)."
        )
    if dockerfile.exists():
        raise BinderBuildError(
            "The project has both a Dockerfile and a declared container "
            f"{containerfile_spec!r}. repo2docker will build the "
            "Dockerfile, so either declare `container: Dockerfile` in "
            "astra.yaml or replace the Dockerfile with a symlink to "
            f"{containerfile_spec}."
        )
    if "/" in containerfile_spec:
        raise BinderBuildError(
            f"The declared container {containerfile_spec!r} is not at the "
            "project root; repo2docker needs a root-level Dockerfile. Add "
            "one (e.g. a symlink to the Containerfile) and commit it."
        )
    dockerfile.symlink_to(containerfile_spec)
    pathspecs.append("Dockerfile")


def _resolve_env_ref(
    project: Path,
    pathspecs: list[str],
    *,
    commit: bool,
    on_progress: ProgressFn | None,
) -> str:
    """Commit env-file changes when needed; return the environment sha."""
    dirty = _git(project, "status", "--porcelain", "--", *pathspecs)
    if dirty:
        if not commit:
            raise BinderBuildError(
                "Environment-defining files have uncommitted changes:\n"
                + dirty
                + "\nCommit them (or run `lc build` without --no-commit) "
                "so the image can be built from a git ref."
            )
        if on_progress:
            on_progress(
                "commit",
                "committing environment changes ("
                + ", ".join(
                    sorted({line[3:].strip() for line in dirty.splitlines()})
                )
                + ")",
            )
        _git(project, "add", "--", *pathspecs)
        _git(
            project,
            "commit",
            "--quiet",
            "-m",
            "lc build: update worker environment",
            "--",
            *pathspecs,
        )

    sha = _git(project, "log", "-1", "--format=%H", "--", *pathspecs)
    if not sha:
        raise BinderBuildError(
            "No commit touches the environment-defining files "
            f"({', '.join(pathspecs)}); commit them first."
        )
    return sha


def _remote(project: Path) -> tuple[str, str]:
    """``(name, clone_url)`` of the remote builds are fetched from.

    ``origin`` by convention, else the first configured remote.
    """
    remotes = _git(project, "remote").splitlines()
    if not remotes:
        raise BinderBuildError(
            "The project has no git remote. The hub's BinderHub service "
            "builds images from a git hosting service (the build pod "
            "clones the repo — it cannot see the hub filesystem), so "
            "push the project to GitHub first:\n"
            "    gh repo create --source . --public --push\n"
            "(note: this deployment's binder service can only clone "
            "public repos until an access token is configured)."
        )
    name = "origin" if "origin" in remotes else remotes[0]
    return name, _git(project, "remote", "get-url", name)


def _ensure_pushed(
    project: Path, remote: str, sha: str, on_progress: ProgressFn | None
) -> None:
    """Make sure *sha* is reachable from *remote*; push if not."""
    if _git(project, "branch", "-r", "--contains", sha):
        return
    if on_progress:
        on_progress("push", f"pushing {sha[:12]} so the build pod can clone it")
    try:
        _git(project, "push", "--quiet", remote, "HEAD")
    except BinderBuildError as exc:
        raise BinderBuildError(
            f"Could not push the environment commit to the remote ({exc}). "
            "The BinderHub build pod clones from the remote, so the "
            "commit must be pushed. Fix your push access (or push "
            "manually) and re-run."
        ) from exc


# ---------------------------------------------------------------------------
# BinderHub build API (SSE)
# ---------------------------------------------------------------------------


def build_via_binder(
    provider_spec: str,
    *,
    on_progress: ProgressFn | None = None,
) -> str:
    """Build *provider_spec* through the binder service; return the image ref.

    Streams the service's SSE events until a terminal one arrives. The
    service checks its registry first, so an already-built ref returns
    in one round-trip — callers can treat this as an idempotent
    "ensure built" primitive.
    """
    base = binder_service_url()
    token = os.environ.get(API_TOKEN_ENV)
    if base is None or not token:
        raise BinderBuildError(
            "No BinderHub service is reachable from here (set "
            f"{BINDER_URL_ENV}, or run on a JupyterHub pod where "
            f"{API_TOKEN_ENV} is provided)."
        )

    url = f"{base}/build/{provider_spec}?build_only=true"
    req = urllib.request.Request(
        url, headers={"Authorization": f"token {token}"}
    )
    deadline = time.monotonic() + _BUILD_DEADLINE_S
    tail: deque[str] = deque(maxlen=30)
    image: str | None = None
    failed = False
    try:
        with urllib.request.urlopen(req, timeout=_STREAM_READ_TIMEOUT_S) as resp:
            for raw in resp:
                if time.monotonic() > deadline:
                    raise BinderBuildError(
                        f"Image build exceeded {_BUILD_DEADLINE_S}s; last "
                        "output:\n" + "".join(tail)
                    )
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue
                phase = str(event.get("phase") or "")
                message = str(event.get("message") or "").rstrip("\n")
                if message:
                    tail.append(message + "\n")
                if on_progress:
                    on_progress(phase, message)
                if isinstance(event.get("imageName"), str):
                    image = event["imageName"]
                if phase in ("failed", "failure"):
                    failed = True
                    break
                if phase in ("ready", "built") and image:
                    break
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except OSError:
            pass
        raise BinderBuildError(
            f"BinderHub build request failed: HTTP {exc.code} for "
            f"{url}\n{body}"
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise BinderBuildError(
            f"Could not reach the BinderHub service at {base} ({exc})."
        ) from exc

    if failed or image is None:
        raise BinderBuildError(
            "BinderHub could not build the image"
            + (f" for {provider_spec}" if image is None else "")
            + ". Last build output:\n"
            + ("".join(tail) or "  (no output received)")
        )
    return image


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------


def ensure_worker_image(
    project: Path,
    containerfile_spec: str,
    *,
    commit: bool = True,
    on_progress: ProgressFn | None = None,
) -> str:
    """Make sure the project's worker image is built; return its registry ref.

    Implements the PRD's "makes sure the container image is up to date,
    rebuilds it if necessary": commit env-file changes (when *commit*),
    push the environment ref, and drive the BinderHub build for it —
    a no-op round-trip when the registry already holds the image.
    """
    from lightcone.engine.container import env_context_paths

    _git(project, "rev-parse", "--git-dir")  # fail early off-git
    containerfile = project / containerfile_spec
    if not containerfile.is_file():
        raise BinderBuildError(
            f"Declared container {containerfile_spec!r} not found in "
            f"{project}."
        )

    pathspecs = env_context_paths(containerfile, project)
    if containerfile_spec not in pathspecs:
        pathspecs.insert(0, containerfile_spec)
    _ensure_dockerfile(project, containerfile_spec, pathspecs)

    sha = _resolve_env_ref(
        project, pathspecs, commit=commit, on_progress=on_progress
    )
    remote_name, remote_url = _remote(project)
    _ensure_pushed(project, remote_name, sha, on_progress)
    spec = repo_provider_spec(remote_url, sha)
    return build_via_binder(spec, on_progress=on_progress)
