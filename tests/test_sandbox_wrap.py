"""Tests for the seam: every backend is a pure argv rewrite.

These are the tests that make the abstraction pay off — the Landlock
wrap and the Seatbelt profile are both checked here, on this host,
whichever host that is. Nothing is executed and no privilege is needed,
because `wrap` is a function from a policy and an argv to an argv.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightcone import _sandbox_exec
from lightcone.engine.sandbox import seatbelt
from lightcone.engine.sandbox.boundary import Unavailable
from lightcone.engine.sandbox.landlock import LandlockBackend
from lightcone.engine.sandbox.model import Capability, Policy
from lightcone.engine.sandbox.seatbelt import SeatbeltBackend


@pytest.fixture
def policy(tmp_path: Path) -> Policy:
    return Policy(
        read=(tmp_path / "proj",),
        write=(tmp_path / "home",),
        execute=(tmp_path / "proj" / ".venv" / "bin",),
        tmp_home=tmp_path / "home",
        env={"HOME": str(tmp_path / "home")},
    )


def _backends(policy: Policy) -> list[tuple[str, object]]:
    return [
        ("landlock", LandlockBackend(capability=Capability(kind="landlock", landlock_abi=4))),
        ("seatbelt", SeatbeltBackend()),
        ("none", Unavailable()),
    ]


# ---- the shared contract --------------------------------------------------


def test_every_backend_ends_with_the_command_verbatim(policy: Policy) -> None:
    """The one property the whole design rests on: whatever a backend
    prepends, the command it was handed is still the tail of the result.
    A backend that rewrote the command would not be interchangeable."""
    argv = ["python", "-c", "print('--policy')", "--", "-x"]
    for name, backend in _backends(policy):
        wrapped = backend.wrap(policy, argv)  # type: ignore[attr-defined]
        assert wrapped[-len(argv) :] == argv, name


def test_wrap_is_pure(policy: Policy, tmp_path: Path) -> None:
    """No temp files, no file descriptors, no global state — which is
    what lets the execution path stay mechanism-blind and lets these
    tests run on a host that cannot enforce anything."""
    before = set(tmp_path.rglob("*"))
    for name, backend in _backends(policy):
        first = backend.wrap(policy, ["true"])  # type: ignore[attr-defined]
        second = backend.wrap(policy, ["true"])  # type: ignore[attr-defined]
        assert first == second, name
    assert set(tmp_path.rglob("*")) == before


# ---- Landlock -------------------------------------------------------------


def test_landlock_wraps_through_the_shim(policy: Policy) -> None:
    backend = LandlockBackend(
        capability=Capability(kind="landlock", landlock_abi=4), interpreter="/usr/bin/python3"
    )
    wrapped = backend.wrap(policy, ["echo", "hi"])
    assert wrapped[:4] == ["/usr/bin/python3", "-m", "lightcone._sandbox_exec", "--policy"]
    assert wrapped[5] == "--"


def test_the_landlock_policy_travels_as_json_the_shim_understands(policy: Policy) -> None:
    """The deliberate deviation from spec §7, which passes an inherited
    ruleset FD instead. Serializing closes §11's own open spike — a
    Landlock FD cannot be reopened, and whether one survives `uv run`'s
    spawn chain was never verified."""
    backend = LandlockBackend(capability=Capability(kind="landlock", landlock_abi=4))
    document = json.loads(backend.wrap(policy, ["true"])[4])

    assert document["version"] == _sandbox_exec.POLICY_VERSION
    assert document["read"] == [str(p) for p in policy.read]
    assert document["write"] == [str(p) for p in policy.write]
    assert document["execute"] == [str(p) for p in policy.execute]


def test_landlock_attests_the_probed_abi(policy: Policy) -> None:
    """Recording which ABI answered is what separates this from the
    "best effort silently succeeded on a kernel with no Landlock" trap."""
    backend = LandlockBackend(capability=Capability(kind="landlock", landlock_abi=3))
    attestation = backend.attest(policy)
    assert attestation.mechanism == "landlock"
    assert attestation.fs == "declared"
    assert attestation.landlock_abi == 3


# ---- Seatbelt -------------------------------------------------------------


def test_seatbelt_invokes_sandbox_exec_by_absolute_path(policy: Policy) -> None:
    """Never resolved through PATH: the sandbox must not be something an
    earlier PATH entry can replace."""
    wrapped = SeatbeltBackend().wrap(policy, ["echo", "hi"])
    assert wrapped[0] == "/usr/bin/sandbox-exec"
    assert wrapped[1] == "-p"


def test_seatbelt_passes_paths_as_parameters_not_profile_text(policy: Policy) -> None:
    """Paths are never interpolated into SBPL — they are bound with -D
    and referenced as (param "..."), so no path can close a form early
    or be quoted wrong."""
    wrapped = SeatbeltBackend().wrap(policy, ["true"])
    profile = wrapped[1]
    for path in (*policy.read, *policy.write, *policy.execute):
        assert str(path) not in profile
    assert f"-DREAD_0={policy.read[0]}" in wrapped
    assert f"-DWRITE_0={policy.write[0]}" in wrapped
    assert f"-DEXEC_0={policy.execute[0]}" in wrapped


def test_the_generated_profile_denies_by_default(policy: Policy) -> None:
    profile = seatbelt.generate_profile(policy)
    assert "(version 1)" in profile
    assert "(deny default)" in profile
    assert profile.index("(deny default)") < profile.index("(allow file-read*")


def test_the_profile_references_every_policy_path_once(policy: Policy) -> None:
    for prefix, paths in (("READ", policy.read), ("WRITE", policy.write), ("EXEC", policy.execute)):
        for index in range(len(paths)):
            assert f'(param "{prefix}_{index}")' in seatbelt.generate_profile(policy), prefix


def test_the_vendored_fragments_are_shipped() -> None:
    """They are package *data*, so a packaging slip would only surface on a
    macOS host at run time. Read them here instead."""
    for name in (seatbelt.BASE_PROFILE, seatbelt.PLATFORM_DEFAULTS):
        assert "(allow" in seatbelt.read_profile(name), name


def test_an_unknown_fragment_fails_loudly() -> None:
    with pytest.raises(KeyError, match="unknown profile fragment"):
        seatbelt.read_profile("nope.sbpl")


def test_the_vendored_base_does_not_grant_blanket_exec() -> None:
    """The one local delta. Upstream allows `process-exec` outright because
    codex does not restrict exec; restricting it is our whole guarantee, so
    the blanket allow must stay commented out however the file is re-synced."""
    base = seatbelt.read_profile(seatbelt.BASE_PROFILE)
    assert "\n(allow process-exec)" not in base
    assert "LIGHTCONE DELTA" in base


def test_the_platform_defaults_carry_the_hard_won_entries(policy: Policy) -> None:
    """Spot-checks on entries nobody would derive from first principles —
    the reason this file is vendored rather than written."""
    profile = seatbelt.generate_profile(policy)
    for needle in (
        "/dev/dtracehelper",            # debugger helpers
        "com.apple.system.opendirectoryd.libinfo",  # getpwuid() or KeyError
        "/opt/homebrew/lib",            # brew dylibs
        "^/dev/ttys[0-9]+$",            # pty handles
        "/System/Volumes/Data/private",  # firmlink parent traversal
    ):
        assert needle in profile, needle


def test_the_loader_can_map_system_libraries(policy: Policy) -> None:
    """dyld is the mach-o twin of the ELF loader tier: without
    `file-map-executable` on the system frameworks nothing dynamically
    linked starts."""
    profile = seatbelt.generate_profile(policy)
    assert "(allow file-map-executable" in profile
    assert '(subpath "/System/Library/Frameworks")' in profile


def test_the_profile_does_not_restrict_the_network(policy: Policy) -> None:
    """Recorded deviation from §7's matrix: lc controls no network on any
    platform, so the profile says so rather than denying what the
    attestation then calls `allowed`."""
    profile = seatbelt.generate_profile(policy)
    assert "(allow network* system-socket)" in profile
    assert "(deny network" not in profile
    assert SeatbeltBackend().attest(policy).network == "allowed"


def test_readable_but_unwritable_paths_are_denied_write_last(tmp_path: Path) -> None:
    """SBPL is last-match-wins, so the guard has to come after the vendored
    defaults — which grant /tmp write unconditionally and would otherwise
    reopen the hole the Linux side closes by omitting the root."""
    built = Policy(
        read=(tmp_path / "proj", tmp_path / "scratch"),
        write=(tmp_path / "scratch",),
        execute=(),
        tmp_home=tmp_path / "scratch",
        env={},
    )
    profile = seatbelt.generate_profile(built)
    guard = profile.index("(deny file-write*")
    assert guard > profile.index("(allow file-read* file-write*")
    assert guard > profile.index("/opt/homebrew/lib"), "the guard must follow the defaults"
    # READ_0 is the project (not writable); READ_1 is also a write root.
    tail = profile[guard:]
    assert '(param "READ_0")' in tail
    assert '(param "READ_1")' not in tail


# ---- the null backend -----------------------------------------------------


def test_unavailable_changes_nothing_and_admits_it(policy: Policy) -> None:
    """Not a special case callers branch on — it satisfies the same
    protocol, and the honesty lives in the attestation."""
    backend = Unavailable()
    assert backend.wrap(policy, ["echo", "hi"]) == ["echo", "hi"]
    attestation = backend.attest(policy)
    assert attestation.mechanism == "none"
    assert attestation.fs == "open"


def test_the_attestation_is_manifest_ready(policy: Policy) -> None:
    """Layer 4 writes this into every manifest; absent fields are omitted
    rather than emitted as null."""
    backend = LandlockBackend(capability=Capability(kind="landlock", landlock_abi=5))
    record = backend.attest(policy).to_manifest()
    assert record["mechanism"] == "landlock"
    assert record["landlock_abi"] == 5
    assert "landlock_abi" not in Unavailable().attest(policy).to_manifest()
