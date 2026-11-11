"""Tests for `lightcone.engine.assets` — whether a result is still current.

The classification table is the important part of this file. Whether a
record still describes the analysis is ASTRA's checker's to say, so the
rule is driven with the statuses that checker returns; what is pinned is
what lc makes of them, and the two things it adds — the environment a
result ran under, and whether its files were last written by its own run.

The `stale` / `behind` split is the other half. `stale` means the artifact
contradicts the project and must be remade; `behind` means it is still
exactly what the analysis asks for and only the environment moved. Getting
that line wrong in either direction is expensive: one way spends compute
nobody asked for, the other way leaves a result quietly describing an
environment that no longer exists.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from astra.check import Reason, Status
from astra.diagnostics import Location
from astra.records import Record, Runner
from astra.universes import Place
from conftest import CLUSTER_ID

from lightcone.engine import assets, dataset, identity, materialize
from lightcone.engine.assets import classify
from lightcone.engine.project import ProjectError

_PLACE = Place(
    "best_fit",
    "best_fit#baseline",
    ".astra/records/baseline/best_fit.json",
    "results/baseline/best_fit.csv",
)


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


# ---- classification --------------------------------------------------------
#
# Every case names the environment it is classified against, because that
# argument is what decides `behind`, and a default would hide it.

_ENV = "urn:lightcone:env:sha256:env"

_TWEAK = dataset.LastWrite(
    "8d31f00" + "0" * 33, "tweak colors", "Ada", "ada@example.org", "2026-08-19"
)

_AT = Location(_PLACE.record, "", None, None)


def _record(environment: str = _ENV) -> Record:
    return Record(
        _PLACE.address, "sha256:data", 8, "c" * 40, environment, Runner("lightcone-cli", "1.0")
    )


def _unchanged(environment: str = _ENV, *, absent: bool = False) -> Status:
    reasons = (Reason("ABSENT_RESULT", "warning", "the bytes are not here", _AT),) if absent else ()
    return Status(
        _PLACE, "unchanged", "absent" if absent else "present", True, reasons,
        _record(environment), (),
    )


def _changed(*messages: str, environment: str = _ENV) -> Status:
    reasons = tuple(Reason("STALE_RESULT", "error", message, _AT) for message in messages)
    return Status(_PLACE, "stale", "present", True, reasons, _record(environment), ())


_MISSING = Status(
    _PLACE, "missing", "absent", False,
    (Reason("MISSING_RECORD", "error", "no record is at the place", _AT),), None, (),
)


def test_a_result_with_no_record_is_stale() -> None:
    verdict = classify(found=[_MISSING], environment=_ENV)
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)
    assert verdict.why == "no record is at the place"


def test_an_unchanged_result_is_current() -> None:
    verdict = classify(found=[_unchanged()], environment=_ENV)
    assert verdict.status == "current"
    assert not verdict.calls_for_a_remake(refresh=False)
    assert not verdict.calls_for_a_remake(refresh=True)
    assert verdict.why == ""


def test_a_result_astra_finds_changed_is_stale_in_astras_words() -> None:
    """Every reason, because a result can have moved in more than one way
    and the user fixes them in one pass."""
    verdict = classify(
        found=[_changed("the command changed", "`catalog` changed")], environment=_ENV
    )
    assert verdict.status == "stale"
    assert verdict.why == "the command changed; `catalog` changed"


def test_one_reason_found_at_two_places_is_said_once() -> None:
    found = [_changed("the command changed"), _changed("the command changed")]
    assert classify(found=found, environment=_ENV).why == "the command changed"


def test_one_place_that_is_not_unchanged_makes_the_result_stale() -> None:
    """The recipe writes every place in one run, so there is no remaking
    half a result."""
    verdict = classify(found=[_unchanged(), _MISSING], environment=_ENV)
    assert verdict.status == "stale"


def test_absent_bytes_alone_do_not_make_a_result_stale() -> None:
    """A clone that has fetched no annex content holds results that are
    perfectly up to date."""
    assert classify(found=[_unchanged(absent=True)], environment=_ENV).status == "current"


# ---- behind, which is the whole point of the split -------------------------


def test_a_moved_environment_is_behind_and_not_stale() -> None:
    """The headline. One added dependency rewrites `uv.lock` for the whole
    project, and folding that into the rebuild trigger is what made a
    week-old result disappear over a plotting library."""
    verdict = classify(found=[_unchanged("urn:lightcone:env:sha256:old")], environment=_ENV)
    assert verdict.status == "behind"
    assert not verdict.calls_for_a_remake(refresh=False)
    assert verdict.calls_for_a_remake(refresh=True)


def test_the_reason_says_what_happened_and_not_where() -> None:
    """The commit a result came from is a field of its record, not a
    phrase in a sentence — a caller with a column for it reads the record."""
    verdict = classify(found=[_unchanged("urn:lightcone:env:sha256:old")], environment=_ENV)
    assert verdict.why == "made under an earlier environment"


def test_one_place_made_under_an_earlier_environment_is_behind() -> None:
    found = [_unchanged(), _unchanged("urn:lightcone:env:sha256:old")]
    assert classify(found=found, environment=_ENV).status == "behind"


def test_stale_wins_over_behind() -> None:
    """Both moved. The artifact has to be remade either way, so the reason
    reported is the one that calls for the work — a `behind` verdict here
    would say "left alone" about something about to run."""
    verdict = classify(
        found=[_changed("the command changed", environment="urn:lightcone:env:sha256:old")],
        environment=_ENV,
    )
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)


def test_a_foreign_write_is_stale_and_the_prose_is_composed_here() -> None:
    """History enters the one rule as a value — the caller with git hands
    over the offending commit, and a hit is a contradiction: the record
    no longer describes the bytes. ASTRA knows nothing of it, so the
    sentence is this module's."""
    verdict = classify(found=[_unchanged()], environment=_ENV, foreign=_TWEAK)
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)
    assert "8d31f00" in verdict.why and "tweak colors" in verdict.why
    assert "git show 8d31f00" in verdict.why


def test_a_result_astra_finds_stale_keeps_its_own_why_over_a_foreign_write() -> None:
    """Both call for the same remake, and what changed in the analysis is
    the more actionable reason to report."""
    verdict = classify(found=[_changed("the command changed")], environment=_ENV, foreign=_TWEAK)
    assert verdict.status == "stale"
    assert "tweak colors" not in verdict.why


def test_a_foreign_write_wins_over_behind() -> None:
    """A behind result is not wrong; a foreign-written one is — reporting
    "left alone" about something the run is about to remake is the one
    wrong answer, the same rule as stale-over-behind."""
    verdict = classify(
        found=[_unchanged("urn:lightcone:env:sha256:old")], environment=_ENV, foreign=_TWEAK
    )
    assert verdict.status == "stale"
    assert "tweak colors" in verdict.why


# ---- asking ASTRA's checker about a real project ---------------------------

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

_SUMMARY = ".astra/records/summary.json"
_FIT = ".astra/records/baseline/fit.json"


def test_before_any_run_every_planned_result_is_missing_and_so_stale(
    analysis: Callable[..., Path],
) -> None:
    """Keyed by where the record would be, from the project root — the
    one name the driver, the worker and `lc view` all hold for a result."""
    root = analysis(_SPEC, {"data/catalog.txt": "measured\n"})

    found = assets.check(root)

    assert list(found) == [_SUMMARY, _FIT]
    for statuses in found.values():
        assert [status.record_status for status in statuses] == ["missing"]
        verdict = classify(found=statuses, environment=identity.environment(root))
        assert verdict.status == "stale" and verdict.why


def test_checking_writes_nothing(analysis: Callable[..., Path]) -> None:
    root = analysis(_SPEC, {"data/catalog.txt": "measured\n"})
    assets.check(root)
    assert dataset.status(root) == []


def test_a_materialized_result_is_current_until_something_it_is_made_from_moves(
    analysis: Callable[..., Path], inline: None
) -> None:
    """The rule over the real checker, end to end: current after its own
    run, behind against another environment, stale once its input changes
    — and the result reading it stale with it."""
    root = analysis(_SPEC, {"data/catalog.txt": "measured\n"})
    assert materialize.materialize(root, [], cluster_id=CLUSTER_ID).ok
    environment = identity.environment(root)

    found = assets.check(root)
    for statuses in found.values():
        assert [status.record_status for status in statuses] == ["unchanged"]
        assert statuses[0].record is not None
        assert statuses[0].record.environment == environment
        assert classify(found=statuses, environment=environment).status == "current"
        assert classify(found=statuses, environment=environment + "0").status == "behind"

    (root / "data" / "catalog.txt").write_text("measured again\n")
    dataset.save(root, [root / "data"], "new catalog")

    found = assets.check(root)
    for record in (_SUMMARY, _FIT):
        assert classify(found=found[record], environment=environment).status == "stale"
    assert "catalog" in classify(found=found[_SUMMARY], environment=environment).why


def test_a_spec_astra_rejects_is_a_clean_error(analysis: Callable[..., Path]) -> None:
    root = analysis(_SPEC.replace("inputs: [catalog]", "inputs: [missing]"))
    with pytest.raises(ProjectError, match="does not validate"):
        assets.check(root)


def test_no_spec_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no astra.yaml"):
        assets.check(tmp_path)
