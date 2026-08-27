"""The daemonless backend: a SIF file, and the bind table as the mechanism.

The same shape as the OCI backend — the policy's path sets become mounts,
and the image is the OS baseline and the exec set — spelled in apptainer's
flags and with no image store behind it: the world is a file, converted
once from the committed archive and addressed by path.

Three differences from a runtime with a daemon and a store, each of them
a flag here rather than a shape above the seam:

- ``--containall`` is the isolation. It supplies the private ``/tmp`` and
  hides every host path the binds do not name, which is what the OCI
  backend spells as ``--tmpfs`` over a ``--read-only`` rootfs. A SIF is
  read-only by construction, so there is nothing to ask for there.
- ``--home`` is how the private HOME arrives. apptainer *refuses* to set
  ``HOME`` through ``--env`` (it warns and keeps the host's), so the
  policy's ``tmp_home`` is passed as the home directory itself and left
  out of the env overlay — the one policy value that is a flag here and
  an environment variable everywhere else.
- There is no uid mapping and no pull policy: the process runs as the
  invoking user, and a SIF path either exists or the run fails.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lightcone.engine.sandbox.boundary import SANDBOX_ENV
from lightcone.engine.sandbox.model import Attestation, Capability, Policy


@dataclass(frozen=True)
class ApptainerBackend:
    """apptainer, expressed as an argv rewrite."""

    #: The converted image, in the project's gitignored ``.lightcone/``
    #: cache and named by the archive's runtime-independent id — so a
    #: SIF built from another archive can never substitute.
    sif: Path
    #: The project root — the container's working directory.
    root: Path
    contains_prefix: bool = True

    @property
    def capability(self) -> Capability:
        """What this backend enforces with."""
        return Capability(kind="apptainer")

    def wrap(self, policy: Policy, argv: Sequence[str]) -> list[str]:
        """Rewrite *argv* into an apptainer invocation of itself.

        Pure: no temporary files, no file descriptors, no global state.
        Read roots bind ``:ro`` before write roots bind ``:rw``, so a
        writable directory nested in a read-only tree lands in the order
        apptainer resolves natively, and the environment is an allowlist
        — the policy's overlay as ``--env``, never the ambient
        environment.

        Args:
            policy: What the command may touch, as binds.
            argv: The command, run prefix included.

        Returns:
            The rewritten command.
        """
        # Resolved source, declared destination, for the same reason as
        # the OCI backend: the host bind must name the real file while
        # the recipe addresses the path the analysis declared.
        binds = [f"{path.resolve()}:{path}:ro" for path in policy.read]
        binds += [f"{path.resolve()}:{path}:rw" for path in policy.write]
        mounts = [flag for bind in binds for flag in ("--bind", bind)]
        # HOME is `--home`'s, above: passing it here would print a
        # warning on every run and change nothing.
        overlay = [f"--env={k}={v}" for k, v in sorted(policy.env.items()) if k != "HOME"]
        return [
            "apptainer", "exec",
            "--containall",
            "--cleanenv",
            "--home", str(policy.tmp_home),
            *mounts,
            "--pwd", str(self.root),
            *overlay,
            f"--env={SANDBOX_ENV}=apptainer",
            str(self.sif),
            *argv,
        ]  # fmt: skip

    def attest(self, policy: Policy) -> Attestation:
        """Report what the wrapped command will actually have enforced.

        Every value is a flag in :meth:`wrap`'s output: ``--containall``
        plus the binds leave the filesystem the declared set over a SIF
        that cannot be written to, and no flag touches the network
        (``--net`` is the one that would) — ``allowed``, the same answer
        every mechanism gives.

        Args:
            policy: The policy being wrapped.

        Returns:
            The record written with every output.
        """
        return Attestation(mechanism="apptainer", fs="declared")
