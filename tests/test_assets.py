"""Tests for `lightcone.engine.assets` — whether a result is still current.

The classification table is the important part of this file. Whether a
record still describes the analysis is ASTRA's checker's to say, so the
rule is driven with the statuses that checker returns; what is pinned is
what lc makes of them, and what it adds — the bytes the results it reads
have now, the environment a result ran under, and whether its files were
last written by its own run.

The `stale` / `behind` split is the other half. `stale` means the artifact
contradicts the project and must be remade; `behind` means it is still
exactly what the analysis asks for and only the environment moved. Getting
that line wrong in either direction is expensive: one way spends compute
nobody asked for, the other way leaves a result quietly describing an
environment that no longer exists.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
from astra.api import (
    Diagnostic,
    Environment,
    Generation,
    Location,
    ProvenanceRecord,
    RecordedInput,
    Runner,
    Status,
)
from conftest import CLUSTER_ID

from lightcone.engine import assets, dataset, identity, materialize, plan
from lightcone.engine.assets import classify

# ---- where the record lives ------------------------------------------------


def test_a_result_file_names_its_record() -> None:
    assert assets.record_places("results/baseline/fit.json") == [
        ".astra/records/baseline/fit.json"
    ]
    assert assets.record_places("results/summary.csv") == [".astra/records/summary.json"]


def test_a_result_of_an_analysis_in_a_folder_keeps_its_record_there() -> None:
    assert assets.record_places("stage/results/v/mask.txt") == [
        "stage/.astra/records/v/mask.json"
    ]


def test_a_dotted_file_name_offers_every_split() -> None:
    """A name is ids joined by dots and a format may hold dots too, so the
    file's name alone cannot say where one ends."""
    assert assets.record_places("results/u/stage.chain.tar.gz") == [
        ".astra/records/u/stage.json",
        ".astra/records/u/stage.chain.json",
        ".astra/records/u/stage.chain.tar.json",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "data/catalog.csv",
        "astra.yaml",
        "results/a/b/fit.json",
        "results/baseline/README",
    ],
)
def test_a_file_at_no_results_place_has_no_record(path: str) -> None:
    """Two levels under `results/` is no place ASTRA gives, and a name
    with no format is nobody's bytes."""
    assert assets.record_places(path) == []


# ---- content that is not there ---------------------------------------------


def test_an_unfetched_annexed_file_is_refused(tmp_path: Path) -> None:
    """`filter=annex` leaves a pointer file where the content would be, so
    the path *exists* and is readable — using it would be a well-formed
    answer to the wrong question."""
    pointer = tmp_path / "catalog.fits"
    pointer.write_text("/annex/objects/SHA256E-s300000--4367c4a63392fa9b887bbcf046033d89.fits\n")

    assert assets.is_pointer(pointer)
    with pytest.raises(assets.ContentNotFetchedError, match="git annex get"):
        assets.require_fetched(pointer)


def test_an_unfetched_locked_file_is_refused_like_an_unfetched_pointer(tmp_path: Path) -> None:
    """The other shape an annexed file takes. A researcher may run `git
    annex lock`, or set `annex.thin`, whenever they like — so detection
    cannot depend on which one lc's own writes happen to produce."""
    locked = tmp_path / "catalog.fits"
    locked.symlink_to("../.git/annex/objects/2K/9P/SHA256E-s300000--4367c4a6.fits")

    assert locked.is_symlink() and not locked.exists()
    with pytest.raises(assets.ContentNotFetchedError, match="git annex get"):
        assets.require_fetched(locked)


def test_a_fetched_file_passes_in_either_shape(tmp_path: Path) -> None:
    content = tmp_path / "object.fits"
    content.write_text("x,y\n1,2\n")
    locked = tmp_path / "catalog.fits"
    locked.symlink_to(content)

    assets.require_fetched(content)
    assets.require_fetched(locked)


def test_a_broken_symlink_that_is_not_annexed_is_not_called_unfetched(tmp_path: Path) -> None:
    """Not git-annex's doing, so not `git annex get`'s to fix — whatever
    opens the file reports it."""
    stray = tmp_path / "scratch.dat"
    stray.symlink_to(tmp_path / "gone-with-the-scratch-dir")

    assets.require_fetched(stray)


def test_a_file_that_merely_mentions_the_prefix_is_content(tmp_path: Path) -> None:
    """The test is the prefix at the very start, as git-annex's own is —
    a script that talks about annex paths is not a pointer."""
    script = tmp_path / "fit.py"
    script.write_text("# reads /annex/objects/ sometimes\nprint(1)\n")

    assert not assets.is_pointer(script)
    assets.require_fetched(script)


def test_a_large_file_is_never_a_pointer(tmp_path: Path) -> None:
    """git-annex's own bound: a pointer is small, so real content that
    happens to begin like one is still content."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"/annex/objects/" + b"0" * (32 * 1024))

    assert not assets.is_pointer(big)


# ---- content, as a record names it -----------------------------------------


@pytest.mark.parametrize("data", [b"", b"x,y\n1,2\n", b"\x00" * (3 << 20)])
def test_a_file_is_hashed_the_way_a_record_names_content(tmp_path: Path, data: bytes) -> None:
    """What a record says of bytes is the runner's to find out: the
    SHA-256 under its algorithm's name, and the size, of the bytes
    themselves."""
    path = tmp_path / "result.bin"
    path.write_bytes(data)

    assert assets.digest_file(path) == (f"sha256:{hashlib.sha256(data).hexdigest()}", len(data))


def test_hashing_a_file_that_is_not_there_raises(tmp_path: Path) -> None:
    """The caller words the failure: an input that cannot be read and a
    result that was never written are different sentences."""
    with pytest.raises(OSError):
        assets.digest_file(tmp_path / "gone.csv")


# ---- classification --------------------------------------------------------
#
# Every case names the environment it is classified against, because that
# argument is what decides `behind`, and a default would hide it.

_ENV = "urn:lightcone:env:sha256:env"

_TWEAK = dataset.LastWrite(
    "8d31f00" + "0" * 33, "tweak colors", "Ada", "ada@example.org", "2026-08-19"
)

_AT = Location(".astra/records/baseline/best_fit.json", "", None, None)

#: What the record says the recipe read of the result it reads, and the
#: same result made again with other bytes.
_READ: assets.Digest = ("sha256:summary", 9)
_OTHER: assets.Digest = ("sha256:other", 9)

_ABSENT = Diagnostic("ABSENT_RESULT", "warning", "the bytes are not here", _AT)


def _record(environment: str = _ENV) -> ProvenanceRecord:
    return ProvenanceRecord(
        "best_fit#baseline",
        "sha256:data",
        8,
        Generation(
            "c" * 40,
            Environment(environment),
            Runner("lightcone-cli", "1.0"),
            (RecordedInput("summary", *_READ),),
        ),
    )


def _unchanged(environment: str = _ENV, *, absent: bool = False) -> Status:
    if absent:
        return Status("unchanged", "absent", _record(environment), (_ABSENT,))
    return Status("unchanged", "present", _record(environment))


def _changed(*messages: str, environment: str = _ENV) -> Status:
    """Stale in itself: something of its own declaration moved."""
    why = tuple(Diagnostic("STALE_RESULT", "error", message, _AT) for message in messages)
    return Status("stale", "present", _record(environment), why)


#: Stale only through what it reads: ASTRA found `summary` not unchanged,
#: which is no rule this result breaks — so nothing is in `why`.
_READS_STALE = Status("stale", "present", _record(), (), ("summary",))

_MISSING = Status(
    "missing",
    "absent",
    None,
    (Diagnostic("MISSING_RECORD", "error", "no record is at the place", _AT),),
)


def test_a_result_with_no_record_is_stale() -> None:
    verdict = classify(found=_MISSING, environment=_ENV)
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)
    assert verdict.why == "no record is at the place"


def test_an_unchanged_result_is_current() -> None:
    verdict = classify(found=_unchanged(), environment=_ENV)
    assert verdict.status == "current"
    assert not verdict.calls_for_a_remake(refresh=False)
    assert not verdict.calls_for_a_remake(refresh=True)
    assert verdict.why == ""


def test_a_result_astra_finds_changed_is_stale_in_astras_words() -> None:
    """Every reason, because a result can have moved in more than one way
    and the user fixes them in one pass."""
    verdict = classify(
        found=_changed("the command changed", "`catalog` changed"), environment=_ENV
    )
    assert verdict.status == "stale"
    assert verdict.why == "the command changed; `catalog` changed"


def test_one_reason_found_at_two_places_is_said_once() -> None:
    """A result with two names has one status, and ASTRA says there what
    it found at each place — the same sentence twice, for the same edit."""
    found = _changed("the command changed", "the command changed")
    assert classify(found=found, environment=_ENV).why == "the command changed"


def test_absent_bytes_alone_do_not_make_a_result_stale() -> None:
    """A clone that has fetched no annex content holds results that are
    perfectly up to date."""
    assert classify(found=_unchanged(absent=True), environment=_ENV).status == "current"


def test_only_a_broken_rule_is_a_reason_to_remake() -> None:
    """ASTRA says everything it found; what stales is an error. Bytes
    absent from this clone are a warning beside it, and no part of why."""
    found = Status(
        "stale",
        "absent",
        _record(),
        (Diagnostic("STALE_RESULT", "error", "the command changed", _AT), _ABSENT),
    )
    verdict = classify(found=found, environment=_ENV)
    assert verdict.status == "stale"
    assert verdict.why == "the command changed"


# ---- the bytes of what it reads --------------------------------------------
#
# ASTRA's checker speaks before the run, and says which of the results a
# result reads were not unchanged then. By the time lc asks, a run may have
# remade them — so that is decided here, from the bytes they have now
# against the bytes the record says were read. It is what stops a
# byte-identical rebuild cascading.


def test_in_itself_a_result_reading_a_stale_one_is_not_stale() -> None:
    """While `summary` may still be remade nobody knows its bytes, so the
    only thing to say of the result is what it is of its own."""
    assert classify(found=_READS_STALE, environment=_ENV, read=None).status == "current"


def test_in_itself_a_result_still_answers_for_its_own_changes() -> None:
    """Taking what it reads as recorded excuses nothing of its own."""
    verdict = classify(found=_changed("the command changed"), environment=_ENV, read=None)
    assert verdict.status == "stale"
    assert verdict.why == "the command changed"


def test_a_rebuild_that_gives_the_recorded_bytes_leaves_the_result_standing() -> None:
    """The cascade stops here: `summary` was stale, was remade, and came
    out as the bytes this result's record says it read."""
    verdict = classify(found=_READS_STALE, environment=_ENV, read={"summary": _READ})
    assert verdict.status == "current"
    assert not verdict.calls_for_a_remake(refresh=True)


def test_a_rebuild_that_gives_other_bytes_makes_the_result_stale() -> None:
    """Worded from the bytes it has now — what ASTRA found of `summary`
    before the run is no longer what is wrong."""
    verdict = classify(found=_READS_STALE, environment=_ENV, read={"summary": _OTHER})
    assert verdict.status == "stale"
    assert verdict.why == "`summary` was remade with other bytes than this result read"


def test_an_unchanged_result_is_stale_once_what_it_reads_comes_out_differently() -> None:
    """ASTRA found nothing wrong, and nothing was: `summary` was remade
    for a reason of lc's own, and no longer gives what was read."""
    verdict = classify(found=_unchanged(), environment=_ENV, read={"summary": _OTHER})
    assert verdict.status == "stale"
    assert "remade with other bytes" in verdict.why
    assert classify(found=_unchanged(), environment=_ENV, read={"summary": _READ}).status == (
        "current"
    )


def test_the_size_read_is_compared_as_well_as_the_hash() -> None:
    """A record gives both of what was read, and both are what was read."""
    resized = (_READ[0], _READ[1] + 1)
    verdict = classify(found=_unchanged(), environment=_ENV, read={"summary": resized})
    assert verdict.status == "stale"


def test_an_entry_the_record_never_listed_was_not_read_as_it_now_is() -> None:
    """Whatever bytes it has, they are not bytes this result's record
    says its recipe read."""
    verdict = classify(found=_unchanged(), environment=_ENV, read={"other": _READ})
    assert verdict.status == "stale"
    assert verdict.why == "`other` was remade with other bytes than this result read"


@pytest.mark.parametrize("read", [None, {}, {"summary": _READ}])
def test_no_record_is_stale_whatever_it_reads(read: dict[str, assets.Digest] | None) -> None:
    verdict = classify(found=_MISSING, environment=_ENV, read=read)
    assert verdict.status == "stale"
    assert verdict.why == "no record is at the place"


def test_its_own_change_is_kept_beside_what_it_reads() -> None:
    """Both are said: one is fixed in the spec, the other by the run."""
    both = Status(
        "stale", "present", _record(), _changed("the command changed").why, ("summary",)
    )
    verdict = classify(found=both, environment=_ENV, read={"summary": _OTHER})
    assert verdict.why == (
        "the command changed; `summary` was remade with other bytes than this result read"
    )


# ---- behind, which is the whole point of the split -------------------------


def test_a_moved_environment_is_behind_and_not_stale() -> None:
    """The headline. One added dependency rewrites `uv.lock` for the whole
    project, and folding that into the rebuild trigger is what made a
    week-old result disappear over a plotting library."""
    verdict = classify(found=_unchanged("urn:lightcone:env:sha256:old"), environment=_ENV)
    assert verdict.status == "behind"
    assert not verdict.calls_for_a_remake(refresh=False)
    assert verdict.calls_for_a_remake(refresh=True)


def test_the_reason_says_what_happened_and_not_where() -> None:
    """The commit a result came from is a field of its record, not a
    phrase in a sentence — a caller with a column for it reads the record."""
    verdict = classify(found=_unchanged("urn:lightcone:env:sha256:old"), environment=_ENV)
    assert verdict.why == "made under an earlier environment"


def test_behind_is_decided_whether_or_not_the_bytes_are_here() -> None:
    """A clone with no content still knows which environment made what."""
    found = _unchanged("urn:lightcone:env:sha256:old", absent=True)
    assert classify(found=found, environment=_ENV).status == "behind"


def test_reading_what_was_recorded_leaves_a_behind_result_behind() -> None:
    """The bytes of what it reads settle `stale`; the environment is a
    separate question, asked after."""
    found = Status("stale", "present", _record("urn:lightcone:env:sha256:old"), (), ("summary",))
    assert classify(found=found, environment=_ENV, read={"summary": _READ}).status == "behind"


def test_stale_wins_over_behind() -> None:
    """Both moved. The artifact has to be remade either way, so the reason
    reported is the one that calls for the work — a `behind` verdict here
    would say "left alone" about something about to run."""
    verdict = classify(
        found=_changed("the command changed", environment="urn:lightcone:env:sha256:old"),
        environment=_ENV,
    )
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)


def test_a_foreign_write_is_stale_and_the_prose_is_composed_here() -> None:
    """History enters the one rule as a value — the caller with git hands
    over the offending commit, and a hit is a contradiction: the record
    no longer describes the bytes. ASTRA knows nothing of it, so the
    sentence is this module's."""
    verdict = classify(found=_unchanged(), environment=_ENV, foreign=_TWEAK)
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)
    assert "8d31f00" in verdict.why and "tweak colors" in verdict.why
    assert "git show 8d31f00" in verdict.why


def test_a_result_astra_finds_stale_keeps_its_own_why_over_a_foreign_write() -> None:
    """Both call for the same remake, and what changed in the analysis is
    the more actionable reason to report."""
    verdict = classify(found=_changed("the command changed"), environment=_ENV, foreign=_TWEAK)
    assert verdict.status == "stale"
    assert "tweak colors" not in verdict.why


def test_a_foreign_write_wins_over_behind() -> None:
    """A behind result is not wrong; a foreign-written one is — reporting
    "left alone" about something the run is about to remake is the one
    wrong answer, the same rule as stale-over-behind."""
    verdict = classify(
        found=_unchanged("urn:lightcone:env:sha256:old"), environment=_ENV, foreign=_TWEAK
    )
    assert verdict.status == "stale"
    assert "tweak colors" in verdict.why


# ---- the rule over what ASTRA's checker finds in a real project ------------

_SPEC = """
spec_version: "0.1.0"
inputs:
  catalog: {path: data/catalog.txt}
decisions:
  method:
    options:
      mcmc: {}
      nested: {}
universes:
  baseline: {decisions: {method: mcmc}}
outputs:
  summary:
    type: data
    format: txt
    inputs: [catalog]
    recipe:
      command: cp {inputs.catalog} {output}
  fit:
    type: data
    format: txt
    inputs: [summary]
    decisions: [method]
    recipe:
      command: cp {inputs.summary} {output}
"""


def test_before_any_run_every_planned_result_is_missing_and_so_stale(
    analysis: Callable[..., Path],
) -> None:
    root = analysis(_SPEC, {"data/catalog.txt": "measured\n"})

    found = plan.build(root).found

    assert list(found) == ["summary", "fit#baseline"]
    for status in found.values():
        assert status.state == "missing"
        verdict = classify(found=status, environment=identity.environment(root))
        assert verdict.status == "stale" and verdict.why


def test_a_materialized_result_is_current_until_something_it_is_made_from_moves(
    analysis: Callable[..., Path], inline: None
) -> None:
    """The rule over the real checker, end to end: current after its own
    run, behind against another environment, stale once its input changes
    — and the result reading it stale only through it."""
    root = analysis(_SPEC, {"data/catalog.txt": "measured\n"})
    assert materialize.materialize(root, [], cluster_id=CLUSTER_ID).ok
    environment = identity.environment(root)

    graph = plan.build(root)
    found = graph.found
    for key, status in found.items():
        assert (status.state, status.bytes, status.why) == ("unchanged", "present", ())
        assert status.record is not None
        assert status.record.generated_by.environment.iri == environment
        # The record gives the bytes as this module hashes them, which is
        # how ASTRA's checker found them present.
        made = root / graph.tasks[key].result.places[0].bytes_path
        assert assets.digest_file(made) == (status.record.content_hash, status.record.size)
        assert classify(found=status, environment=environment).status == "current"
        assert classify(found=status, environment=environment + "0").status == "behind"
    summary_made = found["summary"].record
    assert summary_made is not None
    recorded: assets.Digest = (summary_made.content_hash, summary_made.size)

    # Unlinked first: the results are copies of the catalog, so all three
    # share one annex object, and a write in place would change them too.
    (root / "data" / "catalog.txt").unlink()
    (root / "data" / "catalog.txt").write_text("measured again\n")
    dataset.save(root, [root / "data"], "new catalog")

    found = plan.build(root).found
    summary = classify(found=found["summary"], environment=environment, read={})
    assert summary.status == "stale" and "catalog" in summary.why
    # `fit` moved only because `summary` did: ASTRA finds no rule it
    # breaks itself, and names the entry. In itself it stands; whether it
    # is remade turns on the bytes `summary` comes out with.
    fit = found["fit#baseline"]
    assert (fit.state, fit.why, fit.stale_inputs) == ("stale", (), ("summary",))
    assert classify(found=fit, environment=environment).status == "current"
    assert classify(found=fit, environment=environment, read={"summary": recorded}).status == (
        "current"
    )
    remade = classify(
        found=fit, environment=environment, read={"summary": ("sha256:" + "0" * 64, 15)}
    )
    assert (remade.status, remade.why) == (
        "stale",
        "`summary` was remade with other bytes than this result read",
    )
