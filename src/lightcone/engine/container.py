"""Container runtimes: building the system layer, storing it, entering it.

The dataset is the image store; runtime-local stores are caches. An image
is built once, saved as a ``docker-archive`` at
``.datalad/environments/<tag>/image``, and committed — annexed bytes that
travel through ``git annex get`` like any other project content. Every
machine that runs the project *loads* that archive into whatever runtime
it has, so the bytes that made an output are the bytes a rerun enters,
name-pinned apt notwithstanding. A dropped archive never substitutes: a
rebuild is a new archive under a new id, never the old reference.

Runtime is host capability, not project state — podman-hpc where a site
provides it, podman preferred, docker accepted — and every one of them
consumes the same ``docker-archive``, which is what lets the repository
stay the one store.

Every command goes through :func:`~lightcone.engine.project._run`, the
seam the whole engine shares, so the suite never spawns a runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from lightcone.engine import content, dataset, identity, image, project, sandbox
from lightcone.engine.project import ProjectError, _check_call

#: The runtimes that are podman underneath and share its spellings —
#: the one statement of the set, spelled positively everywhere it is
#: asked, so a future runtime falls outside it by default rather than
#: inheriting podman behavior through a `!= "docker"` back door.
_PODMAN_FAMILY = ("podman", "podman-hpc")

@dataclass(frozen=True)
class Runtime:
    """The execution world one run enters, resolved once by whoever owns it.

    The driver resolves it and hands it to every task — the same
    discipline as the run's HEAD read — because resolving per task could
    answer differently mid-run. The rerun entry point resolves its own,
    because it *is* the driver of its one-task run.
    """

    root: Path
    mode: Literal["direct", "containerized"]
    #: Where the project environment lives: ``.venv``, or the in-image
    #: ``.lightcone/venv``.
    env_dir: Path
    #: ``podman``, ``podman-hpc`` or ``docker``; empty in direct mode.
    runtime: str = ""
    image_tag: str = ""
    #: The image id (bare hex of its config blob): what the archive is,
    #: whichever runtime runs it.
    image_id: str = ""
    #: The id the runtime's local store gives the image (bare hex) —
    #: execution pins on this, never the tag, so a retagged image cannot
    #: substitute. The image id itself under podman and Docker's classic
    #: store; a manifest's digest under Docker's containerd store.
    store_id: str = ""
    #: For a run in a checkout of its commit (:mod:`checkout`), the
    #: repository's own folder, which the checkout's ``.git`` only names.
    repository: Path | None = None

    def at(self, root: Path) -> Runtime:
        """The same world, entered from a checkout of the project's commit.

        The world was resolved, and its image built, from the project's
        working tree, which the researcher may have edited since the run
        read its commit: every record names the environment that commit
        declares, so another is refused.

        Args:
            root: The project root in the checkout.

        Returns:
            The runtime, with its environment in that tree.

        Raises:
            ProjectError: If the checkout declares another world.
        """
        mode = project.mode(root)
        if (mode, image.tag(root) if mode == "containerized" else "") != (
            self.mode,
            self.image_tag,
        ):
            raise ProjectError(
                "`[tool.lightcone.image]` in pyproject.toml is no longer what the commit "
                "this run builds declares: it was edited while the run was preparing, and "
                "every record would name an environment its recipe did not run in. Commit "
                "the change, or undo it, and run again."
            )
        return replace(
            self, root=root, env_dir=project.env_dir(root), repository=dataset.repository(root)
        )

    @property
    def env(self) -> dict[str, str]:
        """The environment for whatever lc starts in this world's tree.

        In a checkout, with the options that keep git-annex and Git LFS
        out of it (:func:`dataset.plain_env`): a build backend that asks
        git for a version, or a recipe that runs git, would otherwise
        start them there.
        """
        ours = project.child_env()
        return {**ours, **(dataset.plain_env(ours) if self.repository else {})}

    @property
    def supports_gpus(self) -> bool:
        """Whether this execution mode can expose the allocation's GPUs."""
        from lightcone.engine.sandbox.oci import supports_gpus

        return self.mode == "direct" or supports_gpus(self.runtime)

    @property
    def archive(self) -> str:
        """The committed archive, project-relative — what the run record's
        ``extra_inputs`` names. Derived through :func:`image.archive_path`
        so this cannot become a second spelling of the layout."""
        return image.archive_path(self.root, self.image_tag).relative_to(self.root).as_posix()


def runtime_for_run(root: Path, *, build: bool, use_gpus: bool = False) -> Runtime:
    """Resolve the execution world, converging the image where allowed.

    The three image checks are repository questions first and runtime
    questions second: archive committed, content fetched (through the
    annex, by lc itself), loaded into the local store. Only the first
    check's miss differs by caller — *build* is true for ``lc build`` and
    the materialize preflight, which may build and commit in a tree their
    own check found free of the researcher's uncommitted work; everything
    else (the probe, the
    rerun entry point) refuses naming the exact ``lc build``, because
    ``lc run`` never builds and the worker never commits.

    Args:
        root: The project root.
        build: Whether a missing archive may be built and committed.
        use_gpus: Refuse unsupported GPU containers before preparing their image.

    Returns:
        The resolved runtime; a direct-mode one costs a TOML read.

    Raises:
        ProjectError: If no runtime is usable, the archive is missing and
            *build* is false, its content cannot be fetched from any
            reachable copy, or the build fails.
    """
    if project.mode(root) == "direct":
        return Runtime(root=root, mode="direct", env_dir=project.env_dir(root))

    name = runtime_name(root)
    if use_gpus:
        from lightcone.engine.sandbox.oci import require_gpu_runtime

        require_gpu_runtime(name)
    tag = image.tag(root)
    archive = image.archive_path(root, tag)
    if not _committed(archive):
        if not build:
            raise ProjectError(
                f"the system-layer image `{tag}` has not been built — this verb "
                "never builds one. Run `lc build` first."
            )
        _build(root, name, tag, archive)
    _fetch(root, archive)
    image_id, arch, manifests, archive_tag = archive_identity(archive)
    _require_arch(root, archive, arch)  # before the load — see its docstring
    # podman and Docker's classic store know an image by its id. Docker's
    # containerd store, a fresh Docker 29's default, knows it by its
    # manifest's digest (measured): the one the archive's index names
    # where `docker save` wrote one, or, for an archive with none — what
    # podman saves — one Docker writes itself as it loads. That one is in
    # no file, so it is read off the tag the archive carries just after
    # the load — the image the tag then names is the one the load made —
    # and such an archive is loaded again by every run. The tag is the
    # archive's own, not this host's spelling of it: podman writes
    # `localhost/<tag>:latest`, which a bare name does not reach.
    known = [image_id, *manifests]
    store_id = _stored(root, name, known)
    if not store_id:
        _check_call([name, "load", "-i", str(archive)], cwd=root)
        if name in _PODMAN_FAMILY:
            store_id = image_id
        else:
            store_id = _stored(root, name, known) or _tagged(root, name, archive_tag)
    if name == "podman-hpc":
        # Compute nodes run only migrated images — the squashed copy on
        # the shared filesystem — never the login node's overlay store.
        # Outside the load branch, because after a fresh `lc build` the
        # image is already in the store and the load never runs.
        # Unconditional: nothing here can ask whether the squashed copy
        # already exists, so re-migrate cost is podman-hpc's to bound.
        _check_call([name, "migrate", store_id], cwd=root)
    return Runtime(
        root=root,
        mode="containerized",
        env_dir=project.env_dir(root),
        runtime=name,
        image_tag=tag,
        image_id=image_id,
        store_id=store_id,
    )


def _fetch(root: Path, archive: Path) -> None:
    """Bring the archive's bytes into this clone, if they are elsewhere.

    lc fetches its own artifact rather than printing a git-annex command —
    the storage invariant is that nobody is ever asked to run one by hand.
    Covers both annexed shapes of absent content (the pointer file and the
    dangling symlink); the refusal is reserved for a fetch that genuinely
    cannot happen, with git-annex's own reason.

    Raises:
        ProjectError: If no reachable copy could supply the content.
    """
    relative = archive.relative_to(root).as_posix()
    try:
        content.require_fetched(archive)
        return
    except content.ContentNotFetchedError:
        pass
    if failed := dataset.get(root, [relative]):
        raise ProjectError(
            f"the image archive `{relative}` is not in this clone, and fetching it "
            f"failed:\n{failed[relative]}\n"
            "It needs a reachable remote that holds the content — or rebuild a new "
            "image with `lc build`."
        )
    content.require_fetched(archive)


def _committed(archive: Path) -> bool:
    """Whether the repository carries the archive, in either annexed shape.

    A dangling symlink *is* a committed archive (a locked clone without
    the content), so the naive ``exists()`` misreads it as never built
    and tells the user to rebuild an image the repository already has.
    """
    return archive.exists() or archive.is_symlink()


def runtime_name(root: Path) -> str:
    """Detect which container runtime this host offers.

    podman-hpc first: a site installs the wrapper precisely because plain
    podman does not work on its compute nodes — the node-local overlay
    store is invisible there, only the wrapper's migrated copies run — so
    where both are on PATH, the bare sibling is the broken one. Then
    podman — rootless, no daemon, no group — then docker, whose CLI
    without a reachable daemon is probed rather than trusted, because
    `docker` on PATH with the daemon down is the common broken state and
    "cannot connect to the socket" mid-run is a worse message than this
    one. podman-hpc is presence-only: no daemon to probe, and no machine
    (it is a Linux-site tool).

    Args:
        root: The project root, for the probe's working directory.

    Returns:
        ``"podman-hpc"``, ``"podman"`` or ``"docker"``.

    Raises:
        ProjectError: If none is usable.
    """
    name = runtime_hint()
    if name == "podman":
        _machine_preflight(root)
    elif name == "docker":
        if project._run(["docker", "info"], cwd=root).returncode != 0:
            raise ProjectError(
                "docker is installed but its daemon is not reachable — start it "
                "(or install podman, which needs no daemon), then retry."
            )
    elif not name:
        raise ProjectError(
            "this project is containerized and needs a container runtime: install "
            "podman (recommended: https://podman.io/docs/installation) or docker."
        )
    return name


def backend(runtime: Runtime, *, workdir: Path) -> sandbox.Backend:
    """Pick the exec boundary for a resolved runtime.

    The only *mode* branch above the sandbox seam, mirroring
    ``sandbox.detect()``'s only *platform* branch: containerized mode is
    entered through the OCI backend, whose mount table is the
    enforcement; direct mode probes the host as it always has.

    Args:
        runtime: A resolved runtime.
        workdir: The folder the command runs in. A host mechanism runs it
            where the process is; a container is told, since the runtime
            decides where its command starts.

    Returns:
        The backend the exec goes through.
    """
    if runtime.mode == "direct":
        return sandbox.detect()
    from lightcone.engine.sandbox.oci import OCIBackend, OCIRuntime

    # `--pull=never` beside the uid flags rather than inside them: it is
    # a pull policy (a typo'd reference must fail, not fetch), the podman
    # family's spelling only, and filing it under uid mapping is where
    # the next reader would not look.
    pull = ("--pull=never",) if runtime.runtime in _PODMAN_FAMILY else ()
    return OCIBackend(
        runtime=cast(OCIRuntime, runtime.runtime),
        image_id=runtime.store_id,
        workdir=workdir,
        user_flags=(*uid_flags(runtime.runtime), *pull),
    )


def build(root: Path) -> tuple[Runtime, str]:
    """Converge the system-layer image, the whole of ``lc build``.

    Refuses, before anything else, a tree in which a file the image is
    derived from is not committed: the archive is committed, and its tag
    derives from ``pyproject.toml`` — so the declaration must be committed
    before the image it defines. Nothing else in the tree is its concern:
    the commit takes the archive and ``.datalad/config`` alone, and what a
    run in flight has put where results go is that run's to commit.

    Args:
        root: The project root.

    Returns:
        The resolved runtime, and one word for what happened: ``built``
        if this call built and committed the archive, ``present`` if it
        was already there.

    Raises:
        ProjectError: On a direct-mode project, an uncommitted
            declaration, a missing committer identity, or a failed build.
    """
    if project.mode(root) == "direct":
        raise ProjectError(
            "direct mode — no image to build; declare [tool.lightcone.image] "
            "in pyproject.toml to containerize this project."
        )
    project.require_git()
    project.require_git_annex()
    dataset.require_committer(root)
    # What the image is derived from, and what its commit carries beside it.
    derived = {*identity.files(root), ".datalad/config"}
    if uncommitted := sorted(derived.intersection(path for _, path in dataset.status(root))):
        raise ProjectError(
            f"uncommitted changes to {', '.join(uncommitted)} — the image is derived from "
            "the project's environment files and committed into the repository, so "
            "`lc build` needs them committed to say what it is building from."
        )
    dataset.require_free_index(root)
    existed = image_state(root)[0] != "absent"
    return runtime_for_run(root, build=True), ("present" if existed else "built")


def runtime_hint() -> str:
    """Name the runtime a run here would pick, or empty. Never a refusal.

    For ``lc status``'s header, which reports rather than gates — a
    missing runtime is a fact there, not a failure, and the daemon probe
    is skipped because a header must not cost a subprocess.

    Returns:
        ``"podman-hpc"``, ``"podman"``, ``"docker"``, or ``""``.
    """
    for name in ("podman-hpc", "podman", "docker"):
        if shutil.which(name):
            return name
    return ""


def image_state(root: Path) -> tuple[str, str, str]:
    """Report where the project's image stands, without a runtime.

    Repository facts only, so ``lc status`` and the CLI's pre-build
    announcement work on hosts with no runtime at all.

    Args:
        root: The project root.

    Returns:
        ``(state, tag, archive)`` — state is ``direct``, ``absent``
        (never built), ``unfetched`` (committed, content elsewhere) or
        ``present``; archive is the project-relative path a remedy can
        name, carried here so no renderer respells the layout.
    """
    if project.mode(root) == "direct":
        return ("direct", "", "")
    tag = image.tag(root)
    archive = image.archive_path(root, tag)
    relative = archive.relative_to(root).as_posix()
    if not _committed(archive):
        return ("absent", tag, relative)
    try:
        content.require_fetched(archive)
    except content.ContentNotFetchedError:
        return ("unfetched", tag, relative)
    return ("present", tag, relative)


def sync(root: Path, runtime: Runtime) -> list[str]:
    """Converge ``.lightcone/venv`` inside the image. The containerized twin
    of ``project.sync``.

    The one container run that gets a writable project mount — converge
    once, then execute without writing to the environment, the same
    discipline as direct mode. The host's uv cache
    is mounted at its identical path, so a complete environment
    materializes from cache hits in about a second; the cache location is
    ``uv cache dir``'s answer, never a guess, because that is uv's own
    resolution of env, config and platform.

    Args:
        root: The project root.
        runtime: A resolved containerized runtime.

    Returns:
        Whatever uv warned about, lifted out of its progress output.

    Raises:
        ProjectError: If uv fails inside the container.
    """
    asked = project._run(["uv", "cache", "dir"], cwd=root)
    if asked.returncode != 0:
        raise ProjectError(f"`uv cache dir` failed:\n{asked.stderr.strip()}")
    cache = asked.stdout.strip()
    argv = [
        runtime.runtime, "run", "--rm", "--entrypoint", "",
        # Same reason as the exec boundary's flag: SELinux hosts refuse
        # bind reads from container_t, and relabeling user data is worse.
        "--security-opt", "label=disable",
        *uid_flags(runtime.runtime),
        "-v", f"{root}:{root}:rw",
        "-v", f"{cache}:{cache}:rw",
        *_repository_flags(runtime),
        "--env", f"UV_CACHE_DIR={cache}",
        "--env", f"UV_PROJECT_ENVIRONMENT={runtime.env_dir}",
        "-w", str(root),
        runtime.store_id,
        # The same sync `project.sync` runs, spelled once for both modes.
        "uv", *project._SYNC_ARGS, "--project", str(root),
    ]  # fmt: skip
    return _check_call(argv, cwd=root)


def _repository_flags(runtime: Runtime) -> list[str]:
    """What a container needs for git to work in a run's checkout.

    A checkout's ``.git`` names the repository, which is outside it: the
    repository is mounted where it is, read-only, and git is given the
    options that keep git-annex and Git LFS out of a checkout. A build
    backend that asks git for a version then finds one.
    """
    if runtime.repository is None:
        return []
    return [
        "-v", f"{runtime.repository}:{runtime.repository}:ro",
        *(f"--env={name}={value}" for name, value in dataset.plain_env({}).items()),
    ]  # fmt: skip


def converge(runtime: Runtime) -> list[str]:
    """Make the environment match the lock, whichever world this is.

    The one spelling of the mode dispatch, so the entry points that
    converge (materialize, probes, the rerun worker) cannot drift apart.
    Cluster execution converges on the driver before submitting tasks;
    workers use the prepared environment without concurrent sync writes.

    Args:
        runtime: The resolved runtime.

    Returns:
        Whatever uv warned about.
    """
    if runtime.mode == "direct":
        return project.sync(runtime.root, runtime.env if runtime.repository else None)
    return sync(runtime.root, runtime)


def policy_for(
    runtime: Runtime, read_paths: list[Path], *, write_dir: Path | None = None,
    use_gpus: bool = False,
) -> sandbox.Policy:
    """Build the exec policy for a resolved runtime.

    The one place the ``env_dir``/``containerized`` pair is assembled —
    two settings that must always agree, projected from the single value
    every caller already holds. Lives here rather than in the policy
    module so the mechanism-free policy layer never learns what a
    ``Runtime`` is.

    Args:
        runtime: The resolved runtime.
        read_paths: Declared inputs, as :func:`sandbox.exec_policy` takes.
        write_dir: The directory a recipe's output lands in; absent for a probe.
        use_gpus: Inherit the allocation's CUDA mask; otherwise hide GPUs.

    Returns:
        The policy for this world.
    """
    if use_gpus and runtime.mode == "containerized":
        from lightcone.engine.sandbox.oci import require_gpu_runtime

        require_gpu_runtime(runtime.runtime)
    # A container sees what is mounted, and a checkout's `.git` names a
    # repository outside it: mounted too, so git in the image answers for
    # the run's commit, with the options that keep git-annex and Git LFS
    # out. A host mechanism grants no git to run.
    repository = runtime.repository if runtime.mode == "containerized" else None
    policy = sandbox.exec_policy(
        runtime.root,
        read_paths=[*read_paths, *([repository] if repository else [])],
        env_dir=runtime.env_dir,
        containerized=runtime.mode == "containerized",
        write_dir=write_dir,
        use_gpus=use_gpus,
    )
    return replace(policy, env={**policy.env, **dataset.plain_env({})}) if repository else policy


def archive_identity(path: Path) -> tuple[str, str, list[str], str]:
    """Read an archive's image id, architecture, manifests and tag, with no runtime.

    The id is the sha256 of the image's config blob — the same value
    ``podman inspect`` reports and the same computation datalad's docker
    adapter makes — so the image is named before anything is loaded,
    whatever runtime then runs it. The manifests are those of the OCI
    index ``docker save`` writes beside the docker-archive's own listing,
    by which Docker's containerd store knows the image; an archive podman
    saved has no index, so names none. The tag is the one a load gives the
    image, as the archive spells it.

    Args:
        path: A ``docker-archive`` file with its content present.

    Returns:
        ``(bare hex id, architecture, bare hex digest of each manifest,
        tag)``; the tag is empty for an archive saved without one.

    Raises:
        ProjectError: If the file is not a readable docker-archive.
    """
    try:
        with tarfile.open(path) as tar:
            listing = tar.extractfile("manifest.json")
            assert listing is not None  # a member, not a directory
            listed = json.load(listing)[0]
            config_name = listed["Config"]
            archive_tag = next(iter(listed.get("RepoTags") or []), "")
            blob = tar.extractfile(config_name)
            assert blob is not None
            config = blob.read()
            index = tar.extractfile("index.json") if "index.json" in tar.getnames() else None
            manifests = [
                str(entry["digest"]).removeprefix("sha256:")
                for entry in (json.load(index)["manifests"] if index else [])
            ]
    except (OSError, KeyError, IndexError, json.JSONDecodeError, tarfile.TarError) as e:
        raise ProjectError(
            f"{path} is not a readable image archive ({e}) — it was committed by "
            "`lc build`; rebuild it with `lc build` after removing the file."
        ) from e
    return (
        hashlib.sha256(config).hexdigest(),
        str(json.loads(config).get("architecture", "")),
        manifests,
        archive_tag,
    )


#: `platform.machine()` spellings mapped onto the OCI architecture names
#: an archive's config records. Closed deliberately: an unmapped host
#: cannot be refused on ignorance.
_OCI_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def _require_arch(root: Path, archive: Path, arch: str) -> None:
    """Refuse an archive built for another architecture.

    The archive is single-arch — it is the exact bytes that ran, which is
    the point of committing it — so a host on the other architecture gets
    a refusal naming the fix, not an emulated run ten times slower than
    the attestation implies, and not an `exec format error` deep inside
    a recipe. Skipped when either side is unknown.

    Args:
        root: The project root, for naming the archive.
        archive: The committed archive.
        arch: Its recorded architecture; empty means unknown.

    Raises:
        ProjectError: On a mismatch.
    """
    host = _OCI_ARCH.get(platform.machine().lower(), "")
    if not host or not arch or host == arch:
        return
    relative = archive.relative_to(root).as_posix()
    raise ProjectError(
        f"the committed image archive `{relative}` was built for {arch} and this "
        f"host is {host} — it cannot run here. Run `lc build` on a host whose "
        "architecture matches (on NERSC, a login node), commit and push; then "
        "`git pull` here."
    )


def uid_flags(runtime: str) -> list[str]:
    """The flags that keep files written through a mount owned by the user.

    The podman family maps the invoking uid into the container
    (``--userns=keep-id``); docker has no equivalent spelling, so the
    container simply runs as the invoking uid — without which a rootful
    docker writes root-owned files into ``results/`` that the host's git
    cannot manage. The missing passwd entry that leaves behind is covered
    by the private-HOME overlay every exec already gets.

    Args:
        runtime: ``"podman"``, ``"podman-hpc"`` or ``"docker"``.

    Returns:
        The argv fragment.
    """
    if runtime in _PODMAN_FAMILY:
        return ["--userns=keep-id"]
    return ["--user", f"{os.getuid()}:{os.getgid()}"]


# =============================================================================
# Building
# =============================================================================


def _build(root: Path, runtime: str, tag: str, archive: Path) -> None:
    """Build the image, save it as *archive*, and commit it.

    The build context is an empty scratch directory holding only the
    rendered Containerfile — no project file ever enters it, which is
    what makes "code edits never trigger a build" structural rather than
    observed. The archive commit is scoped to the environment directory
    plus ``.datalad/config`` (the ``datalad containers-run`` interop
    keys), so nothing else the tree holds is committed with it.
    """
    relative = archive.relative_to(root).as_posix()
    # The archive is committed, so its routing must be checked *before*
    # the bytes exist: with `.gitattributes` not sending it to the annex
    # — a user-authored file lc only ever appends to — a several-hundred-
    # MB blob would land in git itself, silently, and every clone would
    # carry it forever. The same probe-don't-assume rule as the ignore
    # check on `results/`.
    routed = dataset._git(["check-attr", "annex.largefiles", "--", relative], cwd=root)
    if "anything" not in routed:
        raise ProjectError(
            "the image archive would be committed to git itself instead of the "
            f"annex — .gitattributes does not route `{relative}`. Add the line\n"
            "  .datalad/environments/*/image annex.largefiles=anything\n"
            "(`lc init` repairs this), then re-run."
        )

    with tempfile.TemporaryDirectory(prefix="lc-build-") as context:
        containerfile = Path(context) / "Containerfile"
        containerfile.write_text(image.containerfile(root))
        proc = project._run(
            [runtime, "build", "-t", tag, "-f", str(containerfile), context], cwd=root
        )
        if proc.returncode != 0:
            raise ProjectError(_build_failure(tag, proc.stderr))

    archive.parent.mkdir(parents=True, exist_ok=True)
    # Saved beside its final name and renamed into place: a save that
    # dies midway must not leave a partial archive that the dirty-tree
    # refusal would then tell the user to commit. Under a name of its
    # own, since two runs may build one image at once.
    partial = archive.parent / f"image.{uuid4().hex[:8]}.partial"
    save_format = ["--format", "docker-archive"] if runtime in _PODMAN_FAMILY else []
    try:
        _check_call([runtime, "save", *save_format, "-o", str(partial), tag], cwd=root)
    except ProjectError:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(archive)

    for key, value in (
        ("image", relative),
        # Doubled braces survive datalad's record-time `.format`, so
        # `{pwd}` is substituted at run time like any datalad command.
        # The template names the runtime that built the image — best-
        # effort interop for humans, outside lc's guarantees either way.
        (
            "cmdexec",
            f"{runtime} run --rm -v {{{{pwd}}}}:{{{{pwd}}}} -w {{{{pwd}}}} "
            "docker-archive:{img} {cmd}",
        ),
    ):
        dataset._git(
            ["config", "-f", ".datalad/config", f"datalad.containers.{tag}.{key}", value],
            cwd=root,
        )
    dataset.save(
        root,
        [archive.parent, root / ".datalad" / "config"],
        f"Add the system-layer image {tag}",
    )


def _build_failure(tag: str, stderr: str) -> str:
    """Turn a build log into the refusal it means. Never the raw log."""
    for line in stderr.splitlines():
        if "Unable to locate package" in line:
            package = line.rsplit(" ", 1)[-1]
            return (
                f"no apt package named `{package}` — check the name in "
                f"[tool.lightcone.image] apt-install (search with "
                f"`apt-cache search {package}`)."
            )
    # Anchored on the failing instruction as well as the code: the error
    # names the STEP that died, and a user's own run-command exiting 43
    # (curl does) must not be diagnosed as a musl base.
    contract = {
        ("43", "ldd --version"): "the base image is musl-based — manylinux wheels and "
        "uv-managed interpreters need glibc; use a Debian-family `base`.",
        ("44", "command -v bash"): "the base image has no bash, and recipes run through "
        "`bash -c` — use a base that carries bash, or a Debian-family `base`.",
        ("45", "command -v apt-get"): "the base image has no apt, and `apt-install` is "
        "declared — use a Debian-family `base`, or move the packages to `run-commands`.",
    }
    for (code, instruction), message in contract.items():
        if re.search(rf"exit (status|code):? {code}\b", stderr) and instruction in stderr:
            return message
    tail = "\n".join(stderr.strip().splitlines()[-15:])
    return f"building `{tag}` failed:\n{tail}"


# =============================================================================
# The local store, and the podman machine
# =============================================================================


def _stored(root: Path, runtime: str, ids: list[str]) -> str:
    """The first of *ids* the runtime's local store holds an image under;
    empty for none."""
    probe = ["image", "exists" if runtime in _PODMAN_FAMILY else "inspect"]
    return next(
        (each for each in ids if project._run([runtime, *probe, each], cwd=root).returncode == 0),
        "",
    )


def _tagged(root: Path, runtime: str, tag: str) -> str:
    """The id of the image *tag* names in the runtime's local store.

    Raises:
        ProjectError: If the store has no image under *tag*.
    """
    argv = [runtime, "image", "inspect", "--format", "{{.Id}}", tag]
    proc = project._run(argv, cwd=root)
    if proc.returncode != 0:
        raise ProjectError(f"`{' '.join(argv)}` failed:\n{proc.stderr.strip()}")
    return str(proc.stdout).strip().removeprefix("sha256:")


def _machine_preflight(root: Path) -> None:
    """Refuse a macOS podman machine that cannot mount what a run needs.

    podman on macOS runs inside a Linux VM, and a bind mount whose source
    is outside the VM's shared directories arrives *empty* — no error,
    just a project with nothing in it. Linux needs none of this.
    """
    if sys.platform != "darwin":
        return
    proc = project._run(["podman", "machine", "inspect"], cwd=root)
    if proc.returncode != 0:
        raise ProjectError(
            "containerized mode on macOS runs in a podman machine, and there is "
            "none — one-time setup:\n  podman machine init\n  podman machine start"
        )
    try:
        machine = json.loads(proc.stdout)[0]
        state = str(machine.get("State", ""))
        mounts = [str(m.get("Source", "")) for m in machine.get("Mounts", [])]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        return  # an unreadable inspect is not a refusal; the mount will speak
    if state and state != "running":
        # `inspect` succeeds on a stopped machine, so without this the
        # preflight passes and the run dies later on a raw connection
        # error, far from the one-command fix.
        raise ProjectError(
            f"the podman machine is {state}, not running — start it:\n"
            "  podman machine start"
        )
    shared = [m for m in mounts if m]
    if not any(str(root) == m or str(root).startswith(m.rstrip("/") + "/") for m in shared):
        # An empty share list refuses too: a bind whose source the VM
        # does not share arrives *empty*, no error — a project with
        # nothing in it. (Declared inputs outside the tree are not
        # checked here; their mounts carry the same risk, recorded.)
        raise ProjectError(
            f"{root} is outside the podman machine's shared directories "
            f"({', '.join(shared) or 'none'}), so its bind mount would arrive "
            f"empty. Share it:\n  podman machine stop\n"
            f"  podman machine set --volume {root}\n  podman machine start"
        )
