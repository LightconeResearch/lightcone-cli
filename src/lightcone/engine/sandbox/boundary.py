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
from collections.abc import Iterator, Sequence
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


@dataclass(frozen=True)
class Unavailable:
    """The honest null backend: no rewrite, and it says so.

    Not a special case for callers to branch on — it satisfies the same
    protocol, wraps to the same argv it was given, and attests
    ``fs: open``. Telling the user is the caller's job; pretending is
    nobody's.
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
    so adding another mechanism is one import and one line.
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
def scope(policy: Policy) -> Iterator[Policy]:
    """*policy*, with the directory it allocated cleaned up afterwards.

    Every policy owns a real directory on disk — the private ``$HOME`` —
    so building one is not free and leaking one is a real cost on a
    machine that runs many of them. Taking the policy rather than
    building it keeps that lifetime in **one** place for probes and
    recipes alike; a second caller doing its own ``rmtree`` is a second
    thing to find when a policy starts allocating something else.
    """
    try:
        yield policy
    finally:
        shutil.rmtree(policy.tmp_home, ignore_errors=True)


def run(
    backend: Backend,
    policy: Policy,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    prefix: Sequence[str] = (),
) -> Outcome:
    """Run *argv* through *backend*, and explain it if it fails.

    *prefix* is spawned **outside** the rewrite — it is how the caller
    says "wrap the command, not this". ``lc run`` uses it for the
    ``uv run`` hop, because uv, its config, and its caches are trusted
    plumbing that must stay outside the boundary.

    stdout is inherited untouched, so a probe stays a probe — output
    arrives live. stderr is teed: written through as it arrives *and*
    retained, because the denial classifier needs text and the user
    needs immediacy. (A denial printed only to stdout is therefore
    missed; the trailer still fires.)
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

    proc = subprocess.Popen(
        wrapped,
        cwd=cwd,
        env=child_env,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    assert proc.stderr is not None  # Popen was given PIPE
    tail = _Tail(proc.stderr)
    tail.start()
    returncode = proc.wait()
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
    sandboxed run.
    Composed inside the wrap rather than around it so the ``prefix``
    (the ``uv run`` hop) keeps the real environment: uv resolves its
    cache from ``XDG_CACHE_HOME`` and its interpreters from
    ``XDG_DATA_HOME``.

    ``env`` is resolved the same way the exec set resolved it, not by a
    literal path: the set grants whatever
    :func:`~lightcone.engine.sandbox.policy.utility` found, so a hardcoded
    ``/usr/bin/env`` is a denial on the first exec of every run on any
    host that keeps its copy elsewhere.
    """
    if not policy.env:
        return []
    found = policy_module.utility("env")
    if found is None:  # pragma: no cover - no `env` on the search path
        raise RuntimeError(f"`env` not found on {policy_module._UTILITY_PATH}")
    return [str(found), *(f"{k}={v}" for k, v in sorted(policy.env.items()))]


def _downgrade_note(capability: Capability) -> str:
    """The line a user must see when they were not actually sandboxed.

    Never silent: finishing a run believing you were sandboxed when you
    were not is the failure this design exists to prevent, and it is the
    one shipped implementations are cited for.
    """
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
