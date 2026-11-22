"""The environments a run's recipes run in.

A run builds one commit, and its recipes run in the environment that
commit's lock describes: never the project's own ``.venv``, which is the
researcher's, synced and installed into as they work, and holds a packaged
project from their working tree. Built afresh for every run, though, an
environment is tens of thousands of files and seconds of compiling each
time, and on a cluster's shared filesystem far more: inodes against a
quota, and every byte copied where uv cannot link from its cache, which is
always so in a container (measured: Linux makes no hard link across two
mounts, even of one filesystem).

So an environment is built once for its identity and shared. Its identity
is the one every record already names, ``urn:lightcone:env:<env_version>``
(:func:`identity.environment`): what the lock, the interpreter pin, uv's
install settings and the image make. It lives under the project's ignored
``.lightcone/envs/``, at a path of its own, ``<env_version>.<random>``,
since a virtualenv cannot be moved once built: its scripts and its
``pyvenv.cfg`` name where it is. The name ``<env_version>`` is a link to
the one in use, replaced once a new one is whole, so a run never finds one
half built and two runs that build one at once each finish theirs.

**Never written once built.** A published environment is not synced again:
a repair would rewrite it under the recipes of another run. A run that
finds one asks uv whether it is still what the lock says, which writes
nothing (measured), and builds another where it is not.

**What is shared holds nothing of a tree.** A project uv installs as a
package goes into its environment from the tree that synced it (measured:
an editable ``.pth`` naming that tree's folder), so its environment is
built for each run, in the run's checkout, as before. A project that is no
package, as ``lc init`` scaffolds one, has nothing but its dependencies
there.

**Who uses one.** uv is told which, by name (``Runtime.env``), so nothing
depends on where it looks for one. A run links it from its checkout, at
``.lightcone/env``, which tells another run it is in use: not at ``.venv``,
whose ignore rule matches a folder and not a link (measured). The rerun
entry point, which runs in the project's own tree, uses it without a link.
Each marks it used as it starts. One no checkout links, no run has used
for a day, and that is not the one in use for the identity of the run that
looks is removed.
"""

from __future__ import annotations

import os
import shutil
import time
import tomllib
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from lightcone.engine import checkout, container, dataset
from lightcone.engine.container import Runtime

#: Where a project's shared environments are, from the project root.
ENVS = ".lightcone/envs"

#: Where a run's checkout links the environment it uses, from its root.
_LINK = ".lightcone/env"

#: How long an environment nothing links stays after it was last used, in
#: seconds: a run between finding it and linking it, or a rerun by hand,
#: has marked it used, and neither takes a day.
_UNUSED = 86400.0


def shared(root: Path) -> bool:
    """Whether a project's environment holds nothing of the tree that syncs it.

    uv installs the project itself only where it is a package: as
    ``[tool.uv] package`` says, else where it declares a build system.

    Args:
        root: The project root.

    Returns:
        True where uv installs the dependencies alone.
    """
    declared = tomllib.loads((root / "pyproject.toml").read_text())
    package = declared.get("tool", {}).get("uv", {}).get("package")
    return not ("build-system" in declared if package is None else bool(package))


def prepare(root: Path, runtime: Runtime, identity: str) -> tuple[Runtime, list[str]]:
    """Give a run the environment its recipes run in.

    Args:
        root: The project root, in the researcher's tree.
        runtime: The world the run enters, from the tree its recipes run
            in: the run's checkout, or the project's own tree for the
            rerun entry point.
        identity: The environment as a record names it.

    Returns:
        The runtime with that environment, and whatever uv warned about.

    Raises:
        ProjectError: If uv cannot build it.
    """
    if not shared(runtime.root):
        return runtime, container.converge(runtime)
    envs = root / ENVS
    envs.mkdir(parents=True, exist_ok=True)
    envs = envs.resolve()
    name = identity.rsplit(":", 1)[-1]
    published = envs / name
    _sweep(root, envs, published)
    warned: list[str] = []
    if published.is_symlink() and (found := published.resolve()).is_dir():
        # Marked used first: another run removes one nobody used for a day.
        with suppress(OSError):
            os.utime(found)
        if container.current(replace(runtime, env_dir=found)):
            return _use(runtime, found), warned
        warned.append(
            f"the environment {name[:12]} had changed since it was built, so it was built again"
        )
    made = envs / f"{name}.{uuid4().hex[:8]}"
    try:
        warned += container.converge(replace(runtime, env_dir=made))
    except BaseException:
        shutil.rmtree(made, ignore_errors=True)
        raise
    # Whole: the name now leads to it, for this run and the next.
    beside = envs / f".{name}.{uuid4().hex[:8]}"
    beside.symlink_to(made.name)
    os.replace(beside, published)
    return _use(runtime, made), warned


def _use(runtime: Runtime, env: Path) -> Runtime:
    """The runtime in *env*, linked from the run's checkout, where there is
    one: what tells another run this one is using it."""
    if runtime.repository is not None:
        link = runtime.root / _LINK
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(env)
    return replace(runtime, env_dir=env)


def _sweep(root: Path, envs: Path, published: Path) -> None:
    """Remove the environments nobody uses any more.

    Kept: one a checkout of the project links, one used in the last day,
    and the one *published* leads to. A link that leads nowhere goes too.
    """
    prefix = dataset._git(["rev-parse", "--show-prefix"], cwd=root).strip()
    kept = {published.resolve()} if published.is_symlink() else set()
    for top in checkout.held(root):
        if (link := top / prefix / _LINK).is_symlink():
            kept.add(link.resolve())
    now = time.time()
    # Another run may be sweeping too: what it removed first is gone.
    for entry in sorted(envs.iterdir(), key=Path.is_symlink):
        with suppress(OSError):
            if entry.is_symlink():
                if not entry.exists():
                    entry.unlink()
            elif entry not in kept and now - entry.stat().st_mtime > _UNUSED:
                shutil.rmtree(entry, ignore_errors=True)
