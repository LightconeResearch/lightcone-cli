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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from lightcone.engine import assets, dataset, image, project, sandbox
from lightcone.engine.project import ProjectError, _check_call

#: The runtimes that are podman underneath and share its spellings —
#: the one statement of the set, spelled positively everywhere it is
#: asked, so a future runtime falls outside it by default rather than
#: inheriting podman behavior through a `!= "docker"` back door.
_PODMAN_FAMILY = ("podman", "podman-hpc")

#: The runtimes whose image store every node of an allocation can see —
#: podman-hpc's migrate squashes the image to the shared filesystem.
#: podman's and docker's overlay stores are node-local, which is what
#: the multi-node materialize refusal stands on.
_SHARED_STORE_RUNTIMES = ("podman-hpc",)

#: How many blind removals the squash heal will make against a store it
#: cannot list. Bounded rather than looped-until-clean: each pass is a
#: real deletion, and a store still unlistable after this many is not
#: one more `rmsqi` away from working.
_HEAL_ATTEMPTS = 3


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
    #: The image id (bare hex of its config blob) — execution pins on
    #: this, never the tag, so a retagged image cannot substitute.
    image_id: str = ""
    #: The architecture the archive was built for.
    arch: str = ""

    @property
    def archive(self) -> str:
        """The committed archive, project-relative — what the run record's
        ``extra_inputs`` names. Derived through :func:`image.archive_path`
        so this cannot become a second spelling of the layout."""
        return image.archive_path(self.root, self.image_tag).relative_to(self.root).as_posix()

    def manifest_image(self) -> dict[str, str] | None:
        """This world as the manifest's ``image`` field; ``None`` on the
        host. Beside the data, so a field added here cannot be forgotten
        at the write site — the ``asdict(attestation)`` discipline."""
        if self.mode == "direct":
            return None
        return {
            "tag": self.image_tag,
            "id": self.image_id,
            "archive": self.archive,
            "arch": self.arch,
        }


def runtime_for_run(root: Path, *, build: bool) -> Runtime:
    """Resolve the execution world, converging the image where allowed.

    The three image checks are repository questions first and runtime
    questions second: archive committed, content fetched (through the
    annex, by lc itself), loaded into the local store. Only the first
    check's miss differs by caller — *build* is true for ``lc build`` and
    the materialize preflight, which may build and commit on a tree their
    own dirty check just proved clean; everything else (the probe, the
    rerun entry point) refuses naming the exact ``lc build``, because
    ``lc run`` never builds and the worker never commits.

    Args:
        root: The project root.
        build: Whether a missing archive may be built and committed.

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
    image_id, arch = archive_identity(archive)
    _require_arch(root, archive, arch)  # before the load — see its docstring
    if name == "podman-hpc":
        # Before the load, not just the migrate: a wedged squash store
        # (see _heal_squash) fails the load too, on a node whose local
        # store does not hold the image yet.
        _heal_squash(root, tag, image_id)
    if not _loaded(root, name, image_id):
        _check_call([name, "load", "-i", str(archive)], cwd=root)
    if name == "podman-hpc":
        # Compute nodes run only migrated images — the squashed copy on
        # the shared filesystem — never the login node's overlay store.
        # Outside the load branch, because after a fresh `lc build` the
        # image is already in the store and the load never runs.
        # Unconditional: nothing here can ask whether the squashed copy
        # already exists, so re-migrate cost is podman-hpc's to bound.
        _check_call([name, "migrate", image_id], cwd=root)
    return Runtime(
        root=root,
        mode="containerized",
        env_dir=project.env_dir(root),
        runtime=name,
        image_tag=tag,
        image_id=image_id,
        arch=arch,
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
        assets.require_fetched(archive)
        return
    except assets.ContentNotFetchedError:
        pass
    got = project._run(["git", "annex", "get", "--", relative], cwd=root)
    if got.returncode != 0:
        raise ProjectError(
            f"the image archive `{relative}` is not in this clone, and fetching it "
            f"failed:\n{got.stderr.strip()}\n"
            "It needs a reachable remote that holds the content — or rebuild a new "
            "image with `lc build`."
        )
    assets.require_fetched(archive)


def _committed(archive: Path) -> bool:
    """Whether the repository carries the archive, in either annexed shape.

    A dangling symlink *is* a committed archive (a locked clone without
    the content), so the naive ``exists()`` misreads it as never built
    and tells the user to rebuild an image the repository already has.
    """
    return archive.exists() or archive.is_symlink()


def _heal_squash(root: Path, tag: str, image_id: str) -> None:
    """Remove stale squashed images before migrating the current one.

    The tag is deterministic but builds are not bit-reproducible, so a
    rebuild migrated under an unchanged tag puts a second image with the
    same name into podman-hpc's read-only squash store — after which
    *every* storage operation fails ("read-only image store assigns the
    same name to multiple images"), runs included (measured on
    Perlmutter). ``rmsqi`` drops only the squashed copy, and the migrate
    that follows re-squashes the current id.

    Removal is **by id, one call per stale image**: ``rmsqi <tag>``
    resolves a single record and takes that one, so against the very
    state this heals — two records sharing a name — it can just as
    easily take the current image and leave the stale one, re-wedging
    the store on the next migrate. Only the wedged branch has to spell
    the tag, because a store that cannot be listed cannot be enumerated;
    it re-probes so a second stale record is still reached.

    A heal must never break a run whose store is healthy, so a probe
    outcome it does not recognize is left for migrate's own loud path.

    Recorded hazard: the squash store is shared across a user's nodes
    and allocations, so removing a stale image drops layers that a run
    started earlier — under a tag whose id has since moved — may still
    be executing from. The alternative is leaving the store wedged for
    every later verb, which breaks that run too; cross-run coordination
    is not something a heal can offer.
    """
    for _ in range(_HEAL_ATTEMPTS):
        probe = project._run(
            [
                "podman-hpc", "images", "--format",
                "{{.Id}} {{.ReadOnly}}", f"localhost/{tag}",
            ],  # fmt: skip
            cwd=root,
        )
        if probe.returncode == 0:
            rows = [line.split() for line in probe.stdout.splitlines()]
            stale = {
                row[0]
                for row in rows
                if len(row) == 2 and row[1] == "true" and row[0] != image_id
            }
            for victim in sorted(stale):
                _check_call(["podman-hpc", "rmsqi", victim], cwd=root)
            return
        # A wedged store fails the listing too, with the same message
        # every other operation gets — the shape a pre-heal lc left.
        if "assigns the same name to multiple images" not in probe.stderr:
            return
        _check_call(["podman-hpc", "rmsqi", tag], cwd=root)


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
                "(or install podman, which needs no daemon), then retry. "
                "`lc status` shows what this project needs."
            )
    elif not name:
        raise ProjectError(
            "this project is containerized and needs a container runtime: install "
            "podman (recommended: https://podman.io/docs/installation) or docker. "
            "`lc status` shows what this project needs."
        )
    return name


def backend(runtime: Runtime) -> sandbox.Backend:
    """Pick the exec boundary for a resolved runtime.

    The only *mode* branch above the sandbox seam, mirroring
    ``sandbox.detect()``'s only *platform* branch: containerized mode is
    entered through the OCI backend, whose mount table is the
    enforcement; direct mode probes the host as it always has.

    Args:
        runtime: A resolved runtime.

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
        image_id=runtime.image_id,
        root=runtime.root,
        user_flags=(*uid_flags(runtime.runtime), *pull),
        site_modules=site_modules(runtime.runtime, runtime.root),
    )


def site_modules(runtime: str, root: Path) -> tuple[str, ...]:
    """Name the site container modules the runtime will apply, if any.

    podman-hpc reads a site's module table from its own environment, and
    a module widens the container by more than lc declared: NERSC's
    ``ENABLE_CVMFS`` binds the whole ``/cvmfs`` hierarchy — reference
    data a recipe can read without declaring it — and
    ``ENABLE_MPICH_SS`` adds ``--privileged`` plus the host's network,
    pid and ipc namespaces. They are left working, because they are how
    a GPU or MPI recipe reaches the hardware it was written for, and
    named in the attestation instead, so no manifest claims the mount
    table was the whole boundary.

    The table is **asked for, never guessed**: each module declares its
    own gate in an ``env:`` key, and the directory holding them comes
    from ``podman-hpc infohpc`` (a site can move it). Matching a
    ``MOUNT_``/``ENABLE_`` prefix instead would record any passing
    variable that happens to share it — GitHub's ``ENABLE_RUNNER_TRACING``
    was enough to make a manifest name a module the host does not
    have, which is the opposite of what the field is for.

    The environment consulted is :func:`project.child_env`, what the
    runtime actually receives — so the ``MOUNT_*`` gates scrubbed there
    are already gone and cannot be reported as applied.

    Args:
        runtime: The resolved runtime's name.
        root: The project root, for the probe's working directory.

    Returns:
        The gate names that are set, sorted; empty for a runtime with no
        module system, and empty rather than a refusal when the table
        cannot be read — attestation must not fail a run.
    """
    if runtime != "podman-hpc":
        return ()
    info = project._run(["podman-hpc", "infohpc"], cwd=root)
    # Greedy up to the *last* colon: the line reads
    # `modules_dir (file: modules_dir): /etc/podman_hpc/modules.d`.
    found = re.search(r"^modules_dir.*:\s*(\S+)\s*$", info.stdout, re.MULTILINE)
    if info.returncode != 0 or not found:
        return ()
    gates = set()
    for module in sorted(Path(found.group(1)).glob("*.yaml")):
        try:
            text = module.read_text()
        except OSError:
            continue
        if gate := re.search(r"^env:\s*(\S+)", text, re.MULTILINE):
            gates.add(gate.group(1))
    env = project.child_env()
    return tuple(sorted(g for g in gates if env.get(g)))


def build(root: Path) -> tuple[Runtime, str]:
    """Converge the system-layer image, the whole of ``lc build``.

    Refuses a dirty tree before anything else: the archive is committed,
    ``dataset.save`` stages scoped but commits the whole index, and the
    tag derives from ``pyproject.toml`` — so the declaration must be
    committed before the image it defines, and nothing of the user's may
    be swept into the image commit.

    Args:
        root: The project root.

    Returns:
        The resolved runtime, and one word for what happened: ``built``
        if this call built and committed the archive, ``present`` if it
        was already there.

    Raises:
        ProjectError: On a direct-mode project, a dirty tree, a missing
            committer identity, or a failed build.
    """
    if project.mode(root) == "direct":
        raise ProjectError(
            "direct mode — no image to build; declare [tool.lightcone.image] "
            "in pyproject.toml to containerize this project."
        )
    project.require_git()
    project.require_git_annex()
    dataset.require_committer(root)
    if dataset.status(root):
        raise ProjectError(
            "uncommitted changes — the image is derived from pyproject.toml and "
            "committed into the repository, so `lc build` needs the tree to say "
            "what it is building from. Commit first, then re-run `lc build`."
        )
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
        assets.require_fetched(archive)
    except assets.ContentNotFetchedError:
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
    # A bind mount's source must exist — and in containerized mode uv
    # never runs on the host, so nothing else has created it. Otherwise
    # the first sync of a fresh project dies as the runtime's `statfs`
    # error about a path the user never named.
    try:
        Path(cache).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ProjectError(
            f"uv's cache directory `{cache}` cannot be created ({e}). It is "
            "mounted into the image to converge the environment; set "
            "UV_CACHE_DIR to a writable path and retry."
        ) from e
    argv = [
        runtime.runtime, "run", "--rm", "--entrypoint", "",
        # Same reason as the exec boundary's flag: SELinux hosts refuse
        # bind reads from container_t, and relabeling user data is worse.
        "--security-opt", "label=disable",
        *uid_flags(runtime.runtime),
        "-v", f"{root}:{root}:rw",
        "-v", f"{cache}:{cache}:rw",
        "--env", f"UV_CACHE_DIR={cache}",
        "--env", f"UV_PROJECT_ENVIRONMENT={runtime.env_dir}",
        "-w", str(root),
        runtime.image_id,
        # The same sync `project.sync` runs, spelled once for both modes.
        "uv", *project._SYNC_ARGS, "--project", str(root),
    ]  # fmt: skip
    return _check_call(argv, cwd=root)


def converge(runtime: Runtime) -> list[str]:
    """Make the environment match the lock, whichever world this is.

    The one spelling of the mode dispatch, so the entry points that
    converge (materialize, the rerun worker) cannot drift apart. The
    probe deliberately does not call this in direct mode — its syncing
    ``uv run`` hop *is* its converge, documented at the call site.

    Args:
        runtime: The resolved runtime.

    Returns:
        Whatever uv warned about.
    """
    if runtime.mode == "direct":
        return project.sync(runtime.root)
    return sync(runtime.root, runtime)


def policy_for(
    runtime: Runtime, read_paths: list[Path], *, output_dir: Path | None = None
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
        output_dir: A recipe's own output directory; absent for a probe.

    Returns:
        The policy for this world.
    """
    return sandbox.exec_policy(
        runtime.root,
        read_paths=read_paths,
        env_dir=runtime.env_dir,
        containerized=runtime.mode == "containerized",
        output_dir=output_dir,
    )


def archive_identity(path: Path) -> tuple[str, str]:
    """Read an archive's image id and architecture, with no runtime.

    The id is the sha256 of the image's config blob — the same value
    ``podman inspect`` reports and the same computation datalad's docker
    adapter makes — so execution can pin by id before anything is loaded.

    Args:
        path: A ``docker-archive`` file with its content present.

    Returns:
        ``(bare hex id, architecture)``.

    Raises:
        ProjectError: If the file is not a readable docker-archive.
    """
    try:
        with tarfile.open(path) as tar:
            listing = tar.extractfile("manifest.json")
            assert listing is not None  # a member, not a directory
            config_name = json.load(listing)[0]["Config"]
            blob = tar.extractfile(config_name)
            assert blob is not None
            config = blob.read()
    except (OSError, KeyError, IndexError, json.JSONDecodeError, tarfile.TarError) as e:
        raise ProjectError(
            f"{path} is not a readable image archive ({e}) — it was committed by "
            "`lc build`; rebuild it with `lc build` after removing the file."
        ) from e
    return (
        hashlib.sha256(config).hexdigest(),
        str(json.loads(config).get("architecture", "")),
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
    keys), and the caller has already proven the tree clean.
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
    # refusal would then tell the user to commit.
    partial = archive.parent / "image.partial"
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


def _loaded(root: Path, runtime: str, image_id: str) -> bool:
    """Whether the runtime's local store already holds *image_id*."""
    probe = ["image", "exists" if runtime in _PODMAN_FAMILY else "inspect", image_id]
    return project._run([runtime, *probe], cwd=root).returncode == 0


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


