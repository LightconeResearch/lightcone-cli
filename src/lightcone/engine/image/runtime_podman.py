"""Digest-pinned podman execution — the containerized full stack.

One ``podman run`` hosts everything (spec §1): the delegated engine,
its LocalCluster dask workers, the child snakemake, and every recipe —
all from the baked ``/opt/venv``. The invocation is pinned **by image
id** at the argv (a retagged image cannot substitute), runs under
``--net=none`` (loopback intact — in-container LocalCluster keeps
working) with ``--userns=keep-id`` (the invoking uid owns project
writes) and the entrypoint cleared (base ``ENTRYPOINT``/``CMD``/
``USER`` are inert, §2).

Honesty note: a process inside a container cannot independently observe
its own image digest. What IS assertable: (a) the host-side launcher
pins by image id at the argv after checking the store against the build
record — substitution is impossible at the pin point; (b) inside, the
baked ``identity.json`` env_version must equal the job's expected
env_version — a *content* assertion independent of the wrapper; (c)
``LC_IMAGE_DIGEST`` equals the job-command digest — on the
single-container laptop path this is self-consistent rather than
independent. The manifest records mechanism honestly, never claiming
(c) is stronger than it is.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import NoReturn

from lightcone.engine.image.errors import PodmanUnavailableError
from lightcone.engine.image.mounts import MountSet
from lightcone.engine.image.record import BuildRecord

#: Ambient variables passed through into the container — a closed
#: allowlist, never the ambient environment wholesale.
_ENV_PASSTHROUGH = ("TERM", "COLUMNS", "LINES", "LANG")


class PodmanRuntime:
    def __init__(self, podman: str = "podman") -> None:
        self._podman = podman
        if shutil.which(podman) is None:
            raise PodmanUnavailableError(
                "podman is not on PATH — containerized projects execute "
                "inside their environment image. Install podman "
                "(https://podman.io/docs/installation); `lc status` shows "
                "this host's readiness."
            )

    def run_argv(
        self,
        *,
        record: BuildRecord,
        mounts: MountSet,
        argv: Sequence[str],
        interactive: bool = False,
    ) -> list[str]:
        cmd = [
            self._podman, "run", "--rm", "--pull=never",
            # --net=none denies egress with loopback intact — what the
            # hermeticity record's `network: denied` means.
            "--net=none",
            "--userns=keep-id",
            "--entrypoint=",
            # SELinux hosts: never relabel user data.
            "--security-opt", "label=disable",
        ]
        if interactive:
            cmd.append("-it")
        cmd += mounts.to_podman_args()
        cmd += ["-w", str(mounts.project)]
        cmd += [
            "-e", "LC_DELEGATED=1",
            "-e", "LC_WORKER_RUNTIME=container",
            "-e", "LC_CONTAINER_NETWORK=none",
            "-e", f"LC_IMAGE_TAG={record.tag}",
            "-e", f"LC_IMAGE_DIGEST={record.image_id}",
        ]
        for name in _ENV_PASSTHROUGH:
            if name in os.environ:
                cmd += ["-e", f"{name}={os.environ[name]}"]
        # The pin point: run BY image id.
        cmd.append(record.image_id)
        cmd.extend(argv)
        return cmd

    def exec_full_stack(
        self,
        *,
        record: BuildRecord,
        mounts: MountSet,
        lc_argv: Sequence[str],
    ) -> NoReturn:
        """Direct exec (§4 step 5): re-enter ``lc`` from the image's
        baked env. Never returns."""
        argv = self.run_argv(
            record=record,
            mounts=mounts,
            argv=["/opt/venv/bin/lc", *lc_argv],
            interactive=sys.stdin.isatty(),
        )
        os.execvp(self._podman, argv)

    def run(
        self,
        *,
        record: BuildRecord,
        mounts: MountSet,
        argv: Sequence[str],
        interactive: bool = False,
    ) -> int:
        proc = subprocess.run(
            self.run_argv(
                record=record, mounts=mounts, argv=list(argv),
                interactive=interactive,
            ),
            check=False,
        )
        return proc.returncode
