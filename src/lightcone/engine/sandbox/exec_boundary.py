"""The sandbox-backed :class:`~lightcone.engine.boundary.ExecBoundary`.

One boundary for every venue: the capability probe decides the
mechanism (Landlock on Linux — including inside a podman container,
where the same code path answers the seccomp question; Seatbelt on
macOS; none elsewhere), the policy realizes spec §7's declared sets,
the shim applies the restriction between fork and exec, and the
attestation records exactly what ran. When the probe lands below the
venue's expectation the exec still proceeds — recorded and announced,
never silent, never pretended.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from lightcone.engine.boundary import (
    BoundaryResult,
    ExecScope,
    SandboxAttestation,
)
from lightcone.engine.sandbox import denial
from lightcone.engine.sandbox import probe as probe_mod
from lightcone.engine.sandbox.policy import EXEC_ALLOWLIST_VERSION, build_policy
from lightcone.engine.sandbox.wrap import wrap_command

_IN_IMAGE_VENV = Path("/opt/venv")


class SandboxExecBoundary:
    """Enforced recipe execution with honest attestation."""

    def probe(self, scope: ExecScope) -> SandboxAttestation:
        capability = probe_mod.probe()
        fs_scope = "project-rw" if scope.writable_project else "declared"
        return probe_mod.compose_attestation(
            capability,
            fs_scope=fs_scope,
            exec_allowlist_version=EXEC_ALLOWLIST_VERSION,
            disabled=scope.sandbox == "off",
        )

    def execute(
        self,
        command: str,
        scope: ExecScope,
        env: dict[str, str],
    ) -> BoundaryResult:
        capability = probe_mod.probe()
        if scope.sandbox == "off":
            attestation = probe_mod.compose_attestation(
                capability,
                fs_scope="open",
                exec_allowlist_version=None,
                disabled=True,
            )
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                cwd=scope.project_root,
                env=env,
            )
            return BoundaryResult(
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                attestation=attestation,
            )

        policy = build_policy(
            scope,
            env_prefix=self._env_prefix(scope),
            scratch_dirs=self._scratch_dirs(scope),
        )
        wrapped = wrap_command(command, policy, capability)
        attestation = probe_mod.compose_attestation(
            capability,
            fs_scope=policy.fs_scope,
            exec_allowlist_version=policy.exec_allowlist_version,
        )

        notes: list[str] = []
        if capability.kind == "none":
            # Downgrade below the venue expectation: one console line —
            # a user must never finish a run believing they were
            # sandboxed when they weren't.
            notes.append(
                f"\033[33msandbox: no mechanism available "
                f"({capability.detail}) — running unsandboxed\033[0m"
            )

        try:
            proc = subprocess.run(
                list(wrapped.argv),
                capture_output=True,
                text=True,
                check=False,
                cwd=scope.project_root,
                env={**env, **wrapped.env},
                pass_fds=wrapped.pass_fds,
            )
        finally:
            for fd in wrapped.close_after_spawn:
                try:
                    os.close(fd)
                except OSError:
                    pass
            shutil.rmtree(policy.tmp_home, ignore_errors=True)

        if proc.returncode == 97:
            # Reserved: sandbox-setup failure — attributed to lc, never
            # to the recipe.
            notes.append(
                "\033[31mlc sandbox setup failed (see above) — this is "
                "an lc problem, not the recipe's\033[0m"
            )
        elif proc.returncode != 0 and attestation.mechanism != "none":
            explanation = denial.explain_failure(
                stdout=proc.stdout,
                stderr=proc.stderr,
                policy=policy,
                mechanism=attestation.mechanism,
            )
            notes.extend(explanation)
            if explanation:
                notes.append("")
            notes.append(f"\033[2m{denial.trailer(attestation.mechanism)}\033[0m")

        return BoundaryResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            attestation=attestation,
            notes=tuple(notes),
        )

    def describe_host(self) -> str:
        return probe_mod.status_line()

    @staticmethod
    def _env_prefix(scope: ExecScope) -> Path:
        if os.environ.get(probe_mod.WORKER_RUNTIME_ENV) == "container":
            return _IN_IMAGE_VENV
        return scope.project_root / ".venv"

    @staticmethod
    def _scratch_dirs(scope: ExecScope) -> tuple[Path, ...]:
        from lightcone.engine.scratch import resolve_scratch_root

        scratch = resolve_scratch_root(scope.project_root) / ".lightcone"
        return (scratch,) if scratch.exists() else ()
