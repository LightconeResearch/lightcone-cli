"""Selecting a mechanism, and running a command through it.

This is the mechanism-blind half of the layer. It picks a backend, asks
it to rewrite the argv, runs the result, and turns whatever came back
into an :class:`Outcome`. It contains the only ``sys.platform`` branch in
the codebase, in :func:`detect`; everything else here would read the same
if a third mechanism landed tomorrow.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

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


def disabled() -> Unavailable:
    """The backend for a deliberate ``--no-sandbox``.

    Distinct from a host that *cannot* enforce: both end up unsandboxed,
    but only one is the user's choice, and telling them apart must not
    depend on string-matching a field documented as prose.
    """
    return Unavailable(capability=Capability(kind="none", opted_out=True))


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
    env: dict[str, str],
    prefix: Sequence[str] = (),
    explain: bool = True,
    announce: Callable[[Sequence[str]], None] | None = None,
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

    *announce* receives the spawned argv once the wrap is built and
    before anything runs — the hook ``--sandbox-debug`` hangs on, so the
    plan is printed while it can still be acted on rather than after a
    shell has been exited. It is handed only the argv: the caller already
    holds the policy, and ``attest`` is pure.
    """
    wrapped = [*prefix, *backend.wrap(policy, [*env_argv(policy), *argv])]
    attestation = backend.attest(policy)
    # `policy.env` is deliberately **not** merged here: it went inside
    # the wrap, above, via :func:`env_argv`. Everything *outside* the
    # rewrite has to keep the real environment — `uv` resolves its cache
    # from `XDG_CACHE_HOME` and its interpreters from `XDG_DATA_HOME`, so
    # overlaying those for the `uv run` prefix would point it at a
    # throwaway directory `scope()` then deletes.
    child_env = {**env, SANDBOX_ENV: attestation.mechanism}

    notes: list[str] = []
    if backend.capability.kind == "none":
        notes.append(_downgrade_note(backend.capability))
    if announce is not None:
        announce(wrapped)

    proc = subprocess.Popen(
        wrapped,
        cwd=cwd,
        env=child_env,
        stderr=subprocess.PIPE if explain else None,
        text=True,
        errors="replace",
    )
    tail = None
    if proc.stderr is not None:  # iff explain: Popen was given PIPE
        tail = _Tail(proc.stderr)
        tail.start()
    returncode = proc.wait()
    if tail is not None:
        tail.join(timeout=5)

    # Imported here, not at module scope: `sandbox/__init__` loads this
    # module eagerly, and the shim drags ctypes in for one integer.
    from lightcone._sandbox_exec import SETUP_FAILURE_EXIT

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
    )


def env_argv(policy: Policy) -> list[str]:
    """``env K=V …``, prefixed to the command *inside* the wrap.

    One place, every backend — including :class:`Unavailable`, which
    would otherwise silently run in a different environment than a
    sandboxed run and make ``--no-sandbox`` change two variables at once.
    Composed inside the wrap rather than around it so the ``prefix``
    (the ``uv run`` hop) keeps the real environment: uv resolves its
    cache from ``XDG_CACHE_HOME`` and its interpreters from
    ``XDG_DATA_HOME``.

    ``env`` is in the exec allowlist by construction, so it is runnable
    under every mechanism.
    """
    if not policy.env:
        return []
    return ["/usr/bin/env", *(f"{k}={v}" for k, v in sorted(policy.env.items()))]


def _downgrade_note(capability: Capability) -> str:
    """The line a user must see when they were not actually sandboxed.

    Never silent (spec §7): finishing a run believing you were sandboxed
    when you were not is the failure this layer exists to prevent, and
    it is the one shipped implementations are cited for.
    """
    if capability.opted_out:
        return "sandbox disabled by --no-sandbox; recorded as `fs: open`"
    reason = f" — {capability.detail}" if capability.detail else ""
    return f"not sandboxed on this host{reason}; recorded as `fs: open`"


class _Tail(threading.Thread):
    """Pumps the child's stderr through to ours, keeping a bounded tail.

    Concurrent by necessity: the pipe has to be drained while the child
    runs, or a chatty command blocks on a full buffer. Bounded because
    the denial classifier only needs the last few lines of a traceback
    and a recipe that prints megabytes must not be held whole.
    """

    def __init__(self, stream: IO[str]) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        # A deque because the bound is on *bytes*, so it cannot be
        # delegated to `maxlen` — but eviction is from the left, and
        # `list.pop(0)` is O(n) under exactly the load the bound exists
        # to survive.
        self._chunks: deque[str] = deque()
        self._size = 0

    def run(self) -> None:
        for line in self._stream:
            sys.stderr.write(line)
            self._chunks.append(line)
            self._size += len(line)
            while self._size > _STDERR_TAIL_BYTES and len(self._chunks) > 1:
                self._size -= len(self._chunks.popleft())
        sys.stderr.flush()

    def text(self) -> str:
        return "".join(self._chunks)
