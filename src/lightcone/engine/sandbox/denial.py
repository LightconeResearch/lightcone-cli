"""The denial UX — the sandbox layer's primary user interface (spec §7).

A sandbox that only says "permission denied" trains people to disable
it. So when a sandboxed command fails, this module tries to name *what*
was blocked, guess whether it was a tool or a data file, and lead with
the remedy that matches — each as something the reader can paste.

The classification is a best-guess heuristic over the child's own error
text, and it is allowed to come up empty: a recipe can swallow the
``PermissionError``, or rewrap it past recognition. That is why
:func:`trailer` is unconditional. Between them the guarantee is that a
denial is never *invisible*, even when it cannot be explained.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from lightcone.engine.sandbox.model import Policy

#: Ways a blocked path shows up in a child's stderr. Ordered by how
#: specific they are, all applied — the first *confirmed* hit is
#: rendered.
_CANDIDATE_PATTERNS = (
    # Python: PermissionError: [Errno 13] Permission denied: '/path'
    re.compile(r"(?:PermissionError|FileNotFoundError|OSError).*?['\"]([^'\"]+)['\"]"),
    # Our own shim, when the exec itself was denied.
    re.compile(r"lc sandbox: (\S+): "),
    # bash/sh: `line 1: /path: Permission denied`, `cmd: command not found`
    re.compile(
        r"(?:bash|sh)(?::\s*line \d+)?: ([^\s:]+): "
        # macOS reports a Seatbelt denial as EPERM, Linux as EACCES.
        r"(?:Permission denied|Operation not permitted|command not found)"
    ),
    # Landlock ABI 1 has no REFER, so a cross-directory rename is EXDEV.
    re.compile(r"Invalid cross-device link.*?['\"]([^'\"]+)['\"]"),
)

#: Directories whose contents are almost certainly programs, used when
#: the exec bit alone is ambiguous.
_BIN_DIR_HINTS = ("/bin", "/sbin", "/Library/TeX", "/opt")


def explain(stderr: str, policy: Policy, *, cwd: Path) -> list[str]:
    """Lines explaining the first confirmed denial in *stderr*, or ``[]``.

    "Confirmed" is doing real work here. A candidate is dropped when the
    path does not exist (an ordinary missing-file bug, not a denial) or
    when the policy fully grants it (someone else's permission problem).
    Both checks run in the unsandboxed parent, where ``stat`` sees
    everything — which is the whole reason the parent does the
    explaining rather than the child.

    The three outcomes are three different mistakes, and conflating them
    is how a sandbox message becomes useless:

    - a path granted for **neither** access — an undeclared tool or an
      undeclared input;
    - a path granted for **read but not write** — which, since reading
      it was allowed, can only have been a write attempt into the
      read-only project tree.
    """
    # Access-aware, and that distinction is what keeps the message
    # honest: every allowlisted binary lives under `/usr`, which the read
    # baseline grants, so an executable being *readable* says nothing
    # about whether running it was allowed.
    for raw in _candidates(stderr):
        path = _resolve(raw, cwd)
        if path is None or not path.exists():
            continue
        if _classify(path) == "tool":
            if policy.grants(path, policy.execute):
                continue
            return _render_tool(path)
        if policy.grants(path, policy.write):
            continue
        if policy.grants(path, policy.read):
            return _render_write(path)
        return _render_data(path)
    return []


def trailer(mechanism: str) -> str:
    """The one line printed after *every* failed sandboxed run.

    Unconditional by design: :func:`explain` fires only when it can
    extract and confirm a path, and the cases where it cannot — a recipe
    that catches the ``PermissionError`` and exits with something else —
    are exactly the cases where someone would otherwise spend an hour
    fighting an invisible wall.
    """
    return (
        f"this ran under the lc sandbox ({mechanism}) — if the failure looks like a "
        "permissions or missing-file error, try `lc run --sandbox-debug`"
    )


def _candidates(stderr: str) -> list[str]:
    """Every path-ish string the patterns find, de-duplicated, in order."""
    found: dict[str, None] = {}
    for pattern in _CANDIDATE_PATTERNS:
        for match in pattern.finditer(stderr):
            found.setdefault(match.group(1), None)
    return list(found)


def _resolve(raw: str, cwd: Path) -> Path | None:
    """Turn a path-ish fragment from an error message into a real path.

    Three shapes reach us. An absolute path is itself. A relative one is
    relative to where the command ran — a recipe that says
    ``open('astra.yaml')`` reports exactly that string. A bare name from
    ``command not found`` is resolved against the *host* PATH, which is
    what turns "latex: command not found" into "you have latex, it just
    isn't declared" — the difference between a useful message and a
    confusing one.
    """
    if raw.startswith("/"):
        return Path(raw)
    relative = cwd / raw
    if relative.exists():
        return relative
    found = shutil.which(raw)
    return Path(found) if found else None


def _classify(path: Path) -> str:
    """``"tool"`` or ``"data"`` — which kind of denial this looks like."""
    if path.is_file() and os.access(path, os.X_OK):
        return "tool"
    if str(path.parent).endswith(_BIN_DIR_HINTS):
        return "tool"
    return "data"


def _render_tool(path: Path) -> list[str]:
    return _message(
        f"cannot execute {path}",
        [
            "  if this is a tool the command needs, declare it in the environment:",
            f"      uv add <package providing {path.name}>",
            "    (only tools that ship as Python packages can be declared today —",
            "    declaring a system package arrives with the container hatch.)",
        ],
    )


def _render_data(path: Path) -> list[str]:
    return _message(
        f"cannot read {path}",
        [
            "  if this is a data file, declare it as an input in astra.yaml:",
            "      inputs:",
            "        - id: <input_id>",
            "          type: data",
            f"          source: {path}",
        ],
    )


def _render_write(path: Path) -> list[str]:
    """The project tree is readable but not writable — say so plainly.

    Reading it was allowed, so this can only have been a write. There is
    no declaration that makes the tree writable to a probe: a probe has
    no output (spec §4), and that is the answer rather than a limitation
    to work around.
    """
    return _message(
        f"cannot write {path}",
        [
            "  the project tree is read-only in here — nothing a probe does can",
            "  land in it. Write somewhere scratch instead:",
            "      import tempfile; tempfile.mkdtemp()      # or $TMPDIR, /tmp",
        ],
    )


def _message(headline: str, remedy: list[str]) -> list[str]:
    """One shape for every denial: what, why, the fix, then diagnostics."""
    return [
        f"blocked by lc sandbox: {headline} —",
        "not part of the declared environment.",
        "",
        *remedy,
        "",
        "  diagnostics: lc run --sandbox-debug (print the policy) · "
        "lc run --no-sandbox (recorded as unsandboxed)",
    ]
