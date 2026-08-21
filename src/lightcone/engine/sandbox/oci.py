"""The containerized backend: the mount table is the mechanism.

One backend for podman, podman-hpc and docker, data-parameterized — they
differ in spellings (how the invoking uid is kept, whether pulling must
be forbidden), not in shape. The policy's path sets map one-to-one onto
mounts: ``read`` becomes ``:ro``, ``write`` becomes ``:rw``, and the
image itself is the OS baseline and the exec set — everything present in
it was declared, which is why the containerized policy carries no
baseline and no exec tier to translate.

Unlike the host mechanisms, this wrap owns the whole command line
(``contains_prefix``): the ``uv run`` hop executes *inside* the world
being entered, and the env overlay is applied through the runtime's own
``--env`` rather than a host-resolved ``env`` binary — whose path (NixOS
keeps it under ``/run/current-system/sw``) need not exist in the image.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lightcone.engine.sandbox.boundary import SANDBOX_ENV
from lightcone.engine.sandbox.model import Attestation, Capability, Policy

#: The runtimes this backend can speak for — the one statement of the
#: set, so the type does not get hand-copied out of step at its uses.
OCIRuntime = Literal["podman", "docker", "podman-hpc"]


@dataclass(frozen=True)
class OCIBackend:
    """A container runtime, expressed as an argv rewrite."""

    #: ``podman``, ``podman-hpc`` or ``docker`` — also what the
    #: attestation names.
    runtime: OCIRuntime
    #: The image id (bare hex). Execution pins on the id, never a tag, so
    #: a retagged image in the local store can never substitute.
    image_id: str
    #: The project root — the container's working directory.
    root: Path
    #: How this runtime keeps mount writes owned by the invoking user,
    #: resolved by the caller so the wrap stays a pure function of its
    #: fields (``--userns=keep-id`` / ``--user uid:gid``).
    user_flags: tuple[str, ...] = ()
    #: Site container modules this runtime will apply on top of the
    #: mount table, named by the gates that enable them. Resolved by the
    #: caller, like ``user_flags``, because the runtime reads them from
    #: its own environment and the wrap stays a pure function of its
    #: fields. Reported by :meth:`attest`, never acted on here — a
    #: module is the site's mechanism, applied by the runtime itself.
    site_modules: tuple[str, ...] = ()
    contains_prefix: bool = True

    @property
    def capability(self) -> Capability:
        """What this backend enforces with, named by the runtime."""
        return Capability(kind=self.runtime)

    def wrap(self, policy: Policy, argv: Sequence[str]) -> list[str]:
        """Rewrite *argv* into a container invocation of itself.

        Pure: no temporary files, no file descriptors, no global state.
        Read roots mount ``:ro`` before write roots mount ``:rw``, so a
        writable directory nested in a read-only tree lands in the order
        the runtimes resolve natively. ``/tmp`` is a fresh tmpfs (the
        policy's ``tmp_home`` is a bind inside it), ``/dev/shm`` gets a
        real size because the 64 MB default is a scientific-workload
        footgun, and the environment is an allowlist — the policy's
        overlay as ``--env``, never the ambient environment.

        Args:
            policy: What the command may touch, as mounts.
            argv: The command, run prefix included.

        Returns:
            The rewritten command.
        """
        # Resolved source, declared destination: the host bind must name
        # the real file, while the recipe addresses the path the analysis
        # declared — a symlinked `/data` input mounted at its target
        # would leave the container with no `/data` at all. Residue,
        # recorded: a declared input living under `/tmp` is shadowed by
        # the tmpfs on runtimes that mount over it — the same class of
        # host-layout collision `_write_roots` documents for direct mode.
        mounts = [f"--volume={path.resolve()}:{path}:ro" for path in policy.read]
        mounts += [f"--volume={path.resolve()}:{path}:rw" for path in policy.write]
        overlay = [f"--env={k}={v}" for k, v in sorted(policy.env.items())]
        return [
            self.runtime, "run", "--rm",
            "--entrypoint", "",
            # The rootfs is read-only so a write outside the declared set
            # is a loud denial rather than bytes vanishing with the
            # container — without this, `mkdir /output` *succeeds* into
            # the ephemeral layer and the run attests `fs: declared`
            # over silently lost output.
            "--read-only",
            # SELinux hosts (Fedora/RHEL, podman's home turf) would
            # otherwise refuse every bind read from container_t. Label
            # separation is disabled rather than relabeling (`:z`), which
            # would rewrite the user's own file contexts on disk.
            "--security-opt", "label=disable",
            *self.user_flags,
            *mounts,
            "--tmpfs", "/tmp:rw,exec",
            "--shm-size", "1g",
            "--workdir", str(self.root),
            *overlay,
            f"--env={SANDBOX_ENV}={self.runtime}",
            self.image_id,
            *argv,
        ]  # fmt: skip

    def attest(self, policy: Policy) -> Attestation:
        """Report what the wrapped command will actually have enforced.

        Every value is a flag in :meth:`wrap`'s output: the mounts plus
        the read-only rootfs bound the filesystem to the declared set,
        and no flag touches the network — ``allowed``, the same answer
        every mechanism gives, because lc does not control the network
        anywhere and the attestation says only what was enforced.

        The exception is :attr:`site_modules`, which the *runtime*
        applies from its own environment rather than from this argv.
        They are named rather than silently dropped: a module can widen
        the world well past the mount table, so a record saying only
        ``fs: declared`` would overstate what bounded the run.

        Args:
            policy: The policy being wrapped.

        Returns:
            The record written with every output.
        """
        return Attestation(
            mechanism=self.runtime,
            fs="declared",
            site_modules=self.site_modules,
        )
