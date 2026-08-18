"""Selecting a mechanism, and running a command through it.

This is the mechanism-blind half of the layer. It picks a backend, asks
it to rewrite the argv, runs the result, and turns whatever came back
into an :class:`Outcome`. It contains the only ``sys.platform`` branch in
the codebase, in :func:`detect`; everything else here would read the same
if a third mechanism landed tomorrow.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from lightcone._sandbox_exec import SETUP_FAILURE_EXIT
from lightcone.engine.sandbox import policy as policy_module
from lightcone.engine.sandbox.model import Attestation, Backend, Capability, Policy

#: How much of the child's stderr to keep for the denial classifier. The
#: denial is in the last few lines of a traceback, and a recipe that
#: prints megabytes must not be buffered whole.
_STDERR_TAIL_BYTES = 64 * 1024

#: Set inside the boundary so a nested lc — or a test — can tell it is
#: already sandboxed. Neither mechanism nests: `sandbox-exec` refuses,
#: and a Landlock domain can only be tightened.
SANDBOX_ENV = "LC_SANDBOX"

#: `Capability.detail` marking a deliberate opt-out rather than a host
#: that cannot enforce. The two look identical to every other consumer
#: and must not read identically to the user.
DISABLED = "disabled by --no-sandbox"


@dataclass(frozen=True)
class Unavailable:
    """The honest null backend: no rewrite, and it says so.

    Not a special case for callers to branch on — it satisfies the same
    protocol, wraps to the same argv it was given, and attests
    ``fs: open``. Refusing to run is ``--require-sandbox``'s job, and
    telling the user is the caller's; pretending is nobody's.
    """

    capability: Capability = field(default_factory=lambda: Capability(kind="none"))

    def wrap(self, policy: Policy, argv: Sequence[str]) -> list[str]:
        return list(argv)

    def attest(self, policy: Policy) -> Attestation:
        return Attestation(mechanism="none", fs="open")


@dataclass(frozen=True)
class Outcome:
    """What one trip through the boundary produced."""

    returncode: int
    attestation: Attestation
    #: Console lines the caller prints verbatim: the downgrade notice,
    #: the denial explanation, the failure trailer.
    notes: tuple[str, ...] = ()
    #: The argv actually spawned, and the policy it ran under — both for
    #: ``--sandbox-debug``, which has to show what really happened rather
    #: than what would have been built.
    argv: tuple[str, ...] = ()
    policy: Policy | None = None


def detect() -> Backend:
    """The best mechanism this host can offer.

    The single platform branch. A backend that probes unavailable falls
    through to the next candidate and finally to :class:`Unavailable`,
    so adding bubblewrap or podman later is one import and one line.
    """
    if sys.platform == "linux":
        from lightcone.engine.sandbox.landlock import LandlockBackend, capability

        found = capability()
        if found.kind == "landlock":
            return LandlockBackend(capability=found)
        return Unavailable(capability=found)
    if sys.platform == "darwin":
        from lightcone.engine.sandbox.seatbelt import SeatbeltBackend, capability

        found = capability()
        if found.kind == "seatbelt":
            return SeatbeltBackend(capability=found)
        return Unavailable(capability=found)
    return Unavailable(
        capability=Capability(kind="none", detail=f"no sandbox mechanism on {sys.platform}")
    )


@contextmanager
def scope(project: Path, *, read_paths: Sequence[Path] = ()) -> Iterator[Policy]:
    """A probe policy, with its per-run HOME cleaned up afterwards.

    The policy owns a real directory on disk (the private ``$HOME``), so
    building one is not free and leaking one is a real cost on a machine
    that runs many probes. A context manager makes the lifetime the
    caller's, visibly.
    """
    built = policy_module.probe_policy(project, read_paths=read_paths)
    try:
        yield built
    finally:
        shutil.rmtree(built.tmp_home, ignore_errors=True)


def run(
    backend: Backend,
    policy: Policy,
    argv: Sequence[str],
    *,
    cwd: Path,
    prefix: Sequence[str] = (),
    env: dict[str, str] | None = None,
    explain: bool = True,
    announce: Callable[[Policy, Attestation, list[str]], None] | None = None,
) -> Outcome:
    """Run *argv* through *backend*, and explain it if it fails.

    *prefix* is spawned **outside** the rewrite — it is how the caller
    says "wrap the command, not this". ``lc run`` uses it for the
    ``uv run`` hop, because uv, its config, and its caches are trusted
    plumbing that must stay outside the boundary (spec §7).

    stdout is inherited untouched, so a probe stays a probe — output
    arrives live. stderr is teed: written through as it arrives *and*
    retained, because the denial classifier needs text and the user
    needs immediacy. (A denial printed only to stdout is therefore
    missed; the trailer still fires.)

    ``explain=False`` inherits stderr instead, giving up classification.
    That is the right trade for an interactive shell, whose prompt is
    written to stderr without a newline and would sit invisible in a
    line-buffered tee.

    *announce* is called once the wrap is built and before anything is
    spawned — the hook ``--sandbox-debug`` hangs on, so the policy is
    printed while it can still be acted on rather than after a shell has
    been exited.
    """
    wrapped = [*prefix, *backend.wrap(policy, argv)]
    attestation = backend.attest(policy)
    # `policy.env` is deliberately **not** merged here. The overlay is
    # applied by the wrap — the shim on Linux, `env` inside `sandbox-exec`
    # on macOS — because everything outside the rewrite must keep the real
    # environment: `uv` resolves its cache from `XDG_CACHE_HOME` and its
    # managed interpreters from `XDG_DATA_HOME`, so redirecting those for
    # the `uv run` hop would point it at an empty, throwaway cache and
    # re-download the world on every probe (and fail outright offline).
    child_env = {
        **(os.environ if env is None else env),
        SANDBOX_ENV: attestation.mechanism,
    }

    notes: list[str] = []
    if backend.capability.kind == "none":
        notes.append(_downgrade_note(backend.capability))
    if announce is not None:
        announce(policy, attestation, wrapped)

    proc = subprocess.Popen(
        wrapped,
        cwd=cwd,
        env=child_env,
        stderr=subprocess.PIPE if explain else None,
        text=True,
        errors="replace",
    )
    tail = _tee_stderr(proc) if explain else None
    returncode = proc.wait()
    if tail is not None:
        tail.thread.join(timeout=5)

    if returncode == SETUP_FAILURE_EXIT:
        # The shim's reserved code: the sandbox could not be *built*. Say
        # so plainly — the trailer would otherwise point the user at their
        # own command's permissions for a failure that is entirely ours.
        notes.append(
            "lc could not set up the sandbox (see above) — this is an lc "
            "problem, not your command's"
        )
    elif returncode != 0 and attestation.mechanism != "none":
        from lightcone.engine.sandbox import denial

        if tail is not None:
            explanation = denial.explain(tail.text(), policy, cwd=cwd)
            notes.extend([*explanation, ""] if explanation else [])
        notes.append(denial.trailer(attestation.mechanism))

    return Outcome(
        returncode=returncode,
        attestation=attestation,
        notes=tuple(notes),
        argv=tuple(wrapped),
        policy=policy,
    )


def _downgrade_note(capability: Capability) -> str:
    """The line a user must see when they were not actually sandboxed.

    Never silent (spec §7): finishing a run believing you were sandboxed
    when you were not is the failure this layer exists to prevent, and
    it is the one shipped implementations are cited for.
    """
    if capability.detail == DISABLED:
        return "sandbox disabled by --no-sandbox; recorded as `fs: open`"
    reason = f" — {capability.detail}" if capability.detail else ""
    return f"not sandboxed on this host{reason}; recorded as `fs: open`"


def _tee_stderr(proc: subprocess.Popen[str]) -> _Tail:
    """Start pumping the child's stderr to ours, retaining a bounded tail."""
    stream = proc.stderr
    if stream is None:  # pragma: no cover - Popen was given stderr=PIPE
        raise RuntimeError("stderr was not piped")
    tail = _Tail()

    def pump() -> None:
        for line in stream:
            sys.stderr.write(line)
            tail.add(line)
        sys.stderr.flush()

    tail.thread = threading.Thread(target=pump, daemon=True)
    tail.thread.start()
    return tail


class _Tail:
    """The last :data:`_STDERR_TAIL_BYTES` of a stream, cheaply."""

    thread: threading.Thread

    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._size = 0

    def add(self, chunk: str) -> None:
        self._chunks.append(chunk)
        self._size += len(chunk)
        while self._size > _STDERR_TAIL_BYTES and len(self._chunks) > 1:
            self._size -= len(self._chunks.pop(0))

    def text(self) -> str:
        return "".join(self._chunks)
