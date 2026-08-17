"""The denial UX — the design's primary UI (spec §7, mandatory).

When a sandboxed recipe fails, the *unsandboxed parent* re-stats the
paths named in the error output and classifies each confirmed denial as
a **tool** (executable / bin-dir path) or a **data file**, then renders
the two-remedy message: the copy-pasteable ``[tool.lightcone.image]``
fix (with its cost stated) and the ``astra.yaml`` input fix — ordering
by the classification, both always shown. Escape hatches live in a
subdued diagnostics trailer, never as peer remedies.

And on **every** nonzero sandboxed exit — including recipes that
swallow the PermissionError, and rewrapped errors that defeat the
re-stat classifier — a fixed one-line trailer points at
``lc run --sandbox-debug``, so a denial can never fail invisibly.

Pure functions over captured output: rendered worker-side and emitted
through the SENTINEL stream; the same renderer serves ``lc run``
driver-side.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from lightcone.engine.sandbox.hints import apt_hint
from lightcone.engine.sandbox.model import SandboxPolicy

#: Path-bearing error shapes recipes commonly surface.
_CANDIDATE_RES = (
    # Python: PermissionError: [Errno 13] Permission denied: '/path'
    re.compile(r"(?:PermissionError|FileNotFoundError).*?['\"]([^'\"]+)['\"]"),
    # bash: line 1: /path: Permission denied
    re.compile(r"(?:bash|sh): (?:line \d+: )?([^\s:]+): Permission denied"),
    # bash: cmd: command not found
    re.compile(r"(?:bash|sh): (?:line \d+: )?([^\s:]+): command not found"),
    # OSError: [Errno 18] Invalid cross-device link (Landlock ABI-1 REFER)
    re.compile(r"Invalid cross-device link.*?['\"]([^'\"]+)['\"]"),
)

_BIN_DIR_HINTS = ("/bin", "/sbin", "/Library/TeX", "/opt")


def _in_policy(path: Path, policy: SandboxPolicy, *, kind: str) -> bool:
    """Access-aware membership: an executable being in the READ baseline
    (e.g. under /usr) does not make its *execution* granted — check the
    set matching the classified access."""
    resolved = Path(os.path.realpath(path))
    granted_sets = (
        (policy.execute,) if kind == "tool" else (policy.read, policy.write)
    )
    for granted_set in granted_sets:
        for granted in granted_set:
            try:
                resolved.relative_to(granted)
                return True
            except ValueError:
                continue
    return False


def _classify(path: Path) -> str:
    """'tool' or 'data' — ordering heuristic only; both remedies always
    render."""
    if os.access(path, os.X_OK) and path.is_file():
        return "tool"
    if any(str(path.parent).endswith(h) or h in str(path) for h in _BIN_DIR_HINTS):
        return "tool"
    return "data"


def explain_failure(
    *,
    stdout: str,
    stderr: str,
    policy: SandboxPolicy,
) -> list[str]:
    """Render the denial message; empty when no denial is confirmed.

    The re-stat step is what separates a sandbox denial from an
    ordinary recipe bug: a path that exists on the host but lies
    outside the policy sets is a confirmed denial; a path that truly
    does not exist is an ordinary error (the trailer still fires).
    """
    combined = stdout + "\n" + stderr
    candidates: list[str] = []
    for regex in _CANDIDATE_RES:
        candidates.extend(regex.findall(combined))

    confirmed: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for raw in candidates:
        if raw in seen:
            continue
        seen.add(raw)
        path = Path(raw)
        if not path.is_absolute():
            resolved = _which_on_host(raw)
            if resolved is None:
                continue
            path = resolved
        if not path.exists():
            continue
        kind = _classify(path)
        if not _in_policy(path, policy, kind=kind):
            confirmed.append((path, kind))

    if not confirmed:
        return []

    path, kind = confirmed[0]
    tool_name = path.name
    hint = apt_hint(tool_name)
    pkg = hint or f"<package providing {tool_name}>"

    tool_remedy = [
        "  if this is a tool the recipe needs, declare it in the system layer:",
        "      [tool.lightcone.image]",
        f'      system-packages = ["{pkg}"]',
    ]
    if hint is None:
        tool_remedy.append(
            f"    (apt package names — unsure? try: apt-cache search {tool_name})"
        )
    tool_remedy += [
        "    note: this containerizes the project — podman required (macOS:",
        "    one-time `podman machine` VM setup, ~minutes) — and re-stages",
        "    all materialized outputs.",
    ]

    data_remedy = [
        "  if this is a data file, declare it as an input in astra.yaml:",
        "      outputs:",
        "        <output_id>:",
        "          inputs:",
        f"            - path: {path}",
    ]

    remedies = (
        tool_remedy + [""] + data_remedy
        if kind == "tool"
        else data_remedy + [""] + tool_remedy
    )
    verb = "execute" if kind == "tool" else "read"
    return [
        f"blocked by lc sandbox: cannot {verb} {path} —",
        "not part of the declared environment.",
        "",
        *remedies,
        "",
        "  diagnostics: lc run --sandbox-debug (shell inside the sandbox) ·",
        "  lc run --no-sandbox (recorded as unsandboxed) · lc status",
    ]


def _which_on_host(name: str) -> Path | None:
    import shutil

    hit = shutil.which(name)
    return Path(hit) if hit else None


def trailer(mechanism: str) -> str:
    """The fixed line appended to EVERY nonzero sandboxed exit — catches
    recipes that swallow the PermissionError and errors that defeat the
    classifier."""
    return (
        f"this recipe ran under the lc sandbox ({mechanism}) — if the "
        "failure looks like a permissions/missing-file error, try "
        "`lc run --sandbox-debug`"
    )
