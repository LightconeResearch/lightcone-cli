"""Tests for `lightcone.engine.assets` — an output's bytes, record, and
whether it is still current.

The classification table is the important part of this file. It is driven
the way both callers drive it — the worker with live content identities,
and `--check` with `None` for anything it has already decided will be
remade — because the entire justification for one rule is that those two
cannot disagree.

The `stale` / `behind` split is the other half. `stale` means the artifact
contradicts the project and must be remade; `behind` means it is still
exactly what the analysis asks for and only the environment moved. Getting
that line wrong in either direction is expensive: one way spends compute
nobody asked for, the other way leaves a result quietly describing an
environment that no longer exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightcone.engine import assets, dataset
from lightcone.engine.assets import Manifest, classify, data_version, output_dir
from lightcone.engine.project import ProjectError


def _manifest(**overrides: object) -> Manifest:
    base: dict[str, object] = {
        "output_id": "best_fit",
        "universe_id": "baseline",
        "recipe": "python src/fit.py {output}",
        "definition_version": "sha256:code",
        "env_version": "sha256:env",
        "data_version": "sha256:data",
        "decisions": {"method": "mcmc"},
        "input_versions": {"catalog": "sha256:cat"},
        "git_sha": "abc123",
        "git_remote": "https://example/demo.git",
        "lc_version": "0.4.2",
        "hermeticity": {"mechanism": "landlock", "fs": "declared", "network": "allowed"},
    }
    return Manifest(**{**base, **overrides})  # type: ignore[arg-type]


# ---- where an asset lives --------------------------------------------------


def test_an_asset_is_addressed_by_its_path(tmp_path: Path) -> None:
    """The path in a rendered recipe is the path on disk — no staging, no
    scratch, no relocation."""
    assert output_dir(tmp_path, "baseline", "best_fit") == tmp_path / "results/baseline/best_fit"


# ---- content identity ------------------------------------------------------


def test_the_same_bytes_hash_the_same(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "fit.csv").write_text("x,y\n1,2\n")
    assert data_version(tmp_path / "a") == data_version(tmp_path / "b")


def test_changed_bytes_move_it(tmp_path: Path) -> None:
    (tmp_path / "fit.csv").write_text("x,y\n1,2\n")
    before = data_version(tmp_path)
    (tmp_path / "fit.csv").write_text("x,y\n1,3\n")
    assert data_version(tmp_path) != before


def test_a_rename_moves_it(tmp_path: Path) -> None:
    """The path goes into the hash beside the bytes: the same content under
    a different name is a different output."""
    (tmp_path / "fit.csv").write_text("x,y\n")
    before = data_version(tmp_path)
    (tmp_path / "fit.csv").rename(tmp_path / "result.csv")
    assert data_version(tmp_path) != before


def test_touching_a_file_does_not_move_it(tmp_path: Path) -> None:
    """Content, never mtime. A file restored from history carries an old
    timestamp and must not look changed because of it."""
    (tmp_path / "fit.csv").write_text("x,y\n")
    before = data_version(tmp_path)
    (tmp_path / "fit.csv").touch()
    assert data_version(tmp_path) == before


def test_the_manifest_is_not_part_of_its_own_hash(tmp_path: Path) -> None:
    """It carries the hash, so hashing it would be circular — and the
    driver commits both together, so the two must agree."""
    (tmp_path / "fit.csv").write_text("x,y\n")
    before = data_version(tmp_path)
    assets.write(tmp_path, _manifest())
    assert data_version(tmp_path) == before


def test_a_file_and_a_directory_holding_it_are_different(tmp_path: Path) -> None:
    """Framed apart deliberately: a declared input can be either, and the
    two must never collide."""
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "fit.csv").write_text("x,y\n")
    (tmp_path / "fit.csv").write_text("x,y\n")
    assert data_version(tmp_path / "one") != data_version(tmp_path / "fit.csv")


@pytest.mark.parametrize("bad", ["", "/", "..", ".", "a/b", "results/../.."])
def test_output_dir_refuses_an_id_that_is_not_one_path_component(
    tmp_path: Path, bad: str
) -> None:
    """The path is composed from the two ids, and a worker empties it
    before running a recipe — so an id that collapses it onto a parent
    would take every other universe's outputs with it."""
    with pytest.raises(ProjectError):
        assets.output_dir(tmp_path, bad, "best_fit")
    with pytest.raises(ProjectError):
        assets.output_dir(tmp_path, "baseline", bad)


def test_output_dir_is_two_components_below_results(tmp_path: Path) -> None:
    """The shape everything else in the layer addresses by."""
    assert assets.output_dir(tmp_path, "baseline", "best_fit") == (
        tmp_path / "results" / "baseline" / "best_fit"
    )


def test_an_unfetched_annexed_file_is_refused_not_hashed(tmp_path: Path) -> None:
    """`filter=annex` leaves a pointer file where the content would be, so
    the path *exists* and is readable. Hashing it would be a well-formed
    answer to the wrong question, and would land in a manifest as if it
    described the data."""
    pointer = tmp_path / "catalog.fits"
    pointer.write_text(
        "/annex/objects/SHA256E-s300000--4367c4a63392fa9b887bbcf046033d89.fits\n"
    )

    with pytest.raises(assets.ContentNotFetchedError, match="git annex get"):
        data_version(pointer)


def test_a_directory_holding_an_unfetched_file_is_refused_too(tmp_path: Path) -> None:
    """An output directory is hashed as a whole, so one absent file must
    not be quietly folded in as its pointer."""
    (tmp_path / "fit.csv").write_text("a,b\n")
    (tmp_path / "big.bin").write_text("/annex/objects/SHA256E-s9--abc.bin\n")

    with pytest.raises(assets.ContentNotFetchedError):
        data_version(tmp_path)


def test_an_unfetched_locked_file_is_refused_like_an_unfetched_pointer(
    tmp_path: Path,
) -> None:
    """The other shape an annexed file takes. A researcher may run `git
    annex lock`, or set `annex.thin`, whenever they like — so detection
    cannot depend on which one lc's own writes happen to produce."""
    locked = tmp_path / "catalog.fits"
    locked.symlink_to("../.git/annex/objects/2K/9P/SHA256E-s300000--4367c4a6.fits")

    assert locked.is_symlink() and not locked.exists()
    with pytest.raises(assets.ContentNotFetchedError, match="git annex get"):
        data_version(locked)


def test_a_directory_holding_an_unfetched_locked_file_is_refused_too(
    tmp_path: Path,
) -> None:
    """The quiet one. A dangling symlink answers False to `is_file()`, so
    filtering a directory walk on that alone drops the absent file from the
    digest without a word — reporting a hash of the subset that happens to
    be present, which is a worse lie than the pointer's."""
    (tmp_path / "fit.csv").write_text("a,b\n")
    (tmp_path / "big.bin").symlink_to("../.git/annex/objects/xx/yy/SHA256E-s9--abc.bin")

    with pytest.raises(assets.ContentNotFetchedError):
        data_version(tmp_path)


def test_a_broken_symlink_that_is_not_annexed_is_still_loud(tmp_path: Path) -> None:
    """Not git-annex's doing, so not `git annex get`'s to fix — but it may
    not vanish from the digest either."""
    (tmp_path / "fit.csv").write_text("a,b\n")
    (tmp_path / "scratch.dat").symlink_to("/tmp/gone-with-the-scratch-dir")

    with pytest.raises(FileNotFoundError):
        data_version(tmp_path)


def test_a_file_that_merely_mentions_the_prefix_is_still_hashed(tmp_path: Path) -> None:
    """The test is the prefix at the very start, as git-annex's own is —
    a script that talks about annex paths is not a pointer."""
    script = tmp_path / "fit.py"
    script.write_text("# reads /annex/objects/ sometimes\nprint(1)\n")

    assert data_version(script).startswith("sha256:")


def test_a_missing_path_is_an_error_not_an_empty_hash(tmp_path: Path) -> None:
    """The failure mode worth refusing loudly: a constant digest for
    everything absent would silently disable the whole chain."""
    with pytest.raises(FileNotFoundError):
        data_version(tmp_path / "nothing")


def test_one_input_is_hashed_once_per_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared input is asked for once per `(universe, output)` that names
    it — for a multiverse spec, the same bytes over and over. Eight
    universes times four outputs sharing one catalog is thirty-two reads of
    one file."""
    catalog = tmp_path / "catalog.txt"
    catalog.write_text("measured\n")
    hashed: list[Path] = []
    real = assets.data_version
    monkeypatch.setattr(assets, "data_version", lambda p: (hashed.append(p), real(p))[1])

    versions = assets.Versions()
    digests = {versions.of(catalog) for _ in range(32)}

    assert len(hashed) == 1
    assert digests == {real(catalog)}


def test_the_memo_does_not_confuse_two_inputs(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\n")
    (tmp_path / "b.txt").write_text("two\n")
    versions = assets.Versions()

    assert versions.of(tmp_path / "a.txt") != versions.of(tmp_path / "b.txt")


# ---- the manifest ----------------------------------------------------------


def test_a_manifest_round_trips(tmp_path: Path) -> None:
    written = _manifest()
    assets.write(tmp_path, written)
    assert assets.read(tmp_path) == written


def test_the_manifest_is_readable_json_with_the_schema_first(tmp_path: Path) -> None:
    """It stays out of the annex precisely so a clone with no content
    fetched can read it — including with a plain `grep`."""
    assets.write(tmp_path, _manifest())
    text = (tmp_path / assets.MANIFEST_FILENAME).read_text()
    assert next(iter(json.loads(text))) == "schema_version"
    assert "best_fit" in text


def test_no_manifest_reads_as_none(tmp_path: Path) -> None:
    assert assets.read(tmp_path) is None


def test_an_unparseable_manifest_reads_as_none(tmp_path: Path) -> None:
    """The safe direction: an unreadable record means make it again, not
    trust it."""
    (tmp_path / assets.MANIFEST_FILENAME).write_text("{not json")
    assert assets.read(tmp_path) is None


def test_writing_a_manifest_replaces_the_previous_one_whole(tmp_path: Path) -> None:
    assets.write(tmp_path, _manifest())
    assets.write(tmp_path, _manifest(data_version="sha256:second"))

    manifest = assets.read(tmp_path)
    assert manifest is not None and manifest.data_version == "sha256:second"
    assert not list(tmp_path.glob("*.tmp"))


# ---- classification --------------------------------------------------------
#
# Every case names the environment it is classified against, because that
# argument is what decides `behind`, and a default would hide it.

_ENV = "sha256:env"


_TWEAK = dataset.LastWrite(
    "8d31f00" + "0" * 33, "tweak colors", "Ada", "ada@example.org", "2026-08-19"
)


def _classify(**overrides: object) -> assets.Verdict:
    """Classify against the manifest `_manifest()` builds, unchanged."""
    call: dict[str, object] = {
        "definition_version": "sha256:code",
        "env_version": _ENV,
        "manifest": _manifest(),
        "inputs": {"catalog": "sha256:cat"},
    }
    return classify(**{**call, **overrides})  # type: ignore[arg-type]


def test_a_never_materialized_output_is_stale() -> None:
    verdict = _classify(manifest=None, inputs={})
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)
    assert "never been materialized" in verdict.why


def test_an_unchanged_output_is_current() -> None:
    verdict = _classify()
    assert verdict.status == "current"
    assert not verdict.calls_for_a_remake(refresh=False)
    assert verdict.why == ""


def test_a_drifted_definition_is_stale() -> None:
    """One reason covers the recipe and the decisions, because
    `definition_version` is what both feed."""
    verdict = _classify(definition_version="sha256:other")
    assert verdict.status == "stale"
    assert "recipe or its decisions" in verdict.why


def test_a_drifted_input_is_stale_and_says_which() -> None:
    verdict = _classify(inputs={"catalog": "sha256:new"})
    assert verdict.status == "stale"
    assert "`catalog`" in verdict.why


def test_a_newly_declared_input_is_stale() -> None:
    """The manifest has no version recorded for it, so the sets differ."""
    verdict = _classify(inputs={"catalog": "sha256:cat", "mask": "sha256:mask"})
    assert verdict.status == "stale"
    assert "`mask`" in verdict.why


def test_an_input_the_output_no_longer_declares_is_stale() -> None:
    """`definition_version` hashes the recipe and the decisions — neither of
    which a dropped input moves — so nothing else would catch it and the
    output would stay current with a dependency set that changed."""
    verdict = _classify(inputs={})
    assert verdict.status == "stale"
    assert "`catalog`" in verdict.why


def test_an_input_that_will_be_remade_is_stale() -> None:
    """`--check`'s sentinel. It cannot know whether a rebuild comes out
    byte-identical, so `None` means "this is going to change" — deliberate
    over-approximation, and the only difference between the two callers."""
    assert _classify(inputs={"catalog": None}).status == "stale"


def test_the_sentinel_and_a_real_drift_give_the_same_verdict() -> None:
    """The property that justifies one rule with two callers: for the same
    state they agree, and `--check` differs from the worker only in what it
    is able to know."""
    assert _classify(inputs={"catalog": None}) == _classify(
        inputs={"catalog": "sha256:rebuilt"}
    )


def test_a_byte_identical_rebuild_stops_the_cascade() -> None:
    """What content hashing buys, and what the sentinel deliberately gives
    up: the worker sees the upstream came out the same and does not rerun,
    where `--check` had to assume it would."""
    assert _classify(inputs={"catalog": "sha256:cat"}).status == "current"


# ---- behind, which is the whole point of the split -------------------------


def test_a_moved_environment_is_behind_and_not_stale() -> None:
    """The headline. One added dependency rewrites `uv.lock` for the whole
    project, and folding that into the rebuild trigger is what made a
    week-old result disappear over a plotting library."""
    verdict = _classify(env_version="sha256:moved")
    assert verdict.status == "behind"
    assert not verdict.calls_for_a_remake(refresh=False)


def test_the_reason_says_what_happened_and_not_where() -> None:
    """The commit an output came from is a field of its manifest, not a
    phrase in a sentence — a caller with a column for it reads the record.
    Interpolating it here would also have to handle a repository with no
    commit yet, which records an empty sha."""
    verdict = _classify(env_version="sha256:moved")
    assert verdict.why == "made under an earlier environment"


def test_stale_wins_over_behind() -> None:
    """Both moved. The artifact has to be remade either way, so the reason
    reported is the one that calls for the work — a `behind` verdict here
    would say "left alone" about something about to run."""
    verdict = _classify(definition_version="sha256:other", env_version="sha256:moved")
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)


def test_a_foreign_write_is_stale_and_the_prose_is_composed_here() -> None:
    """History enters the one rule as a value — the caller with git hands
    over the offending commit, and a hit is a contradiction: the manifest
    no longer describes the bytes. The sentence, like every other why,
    is this module's."""
    verdict = _classify(foreign=_TWEAK)
    assert verdict.status == "stale"
    assert verdict.calls_for_a_remake(refresh=False)
    assert "8d31f00" in verdict.why and "tweak colors" in verdict.why
    assert "git show 8d31f00" in verdict.why


def test_a_definition_stale_output_keeps_its_own_why_over_a_foreign_write() -> None:
    """Both call for the same remake, and the definition drift is the more
    actionable reason to report."""
    verdict = _classify(definition_version="sha256:other", foreign=_TWEAK)
    assert verdict.status == "stale"
    assert "tweak colors" not in verdict.why


def test_a_foreign_write_wins_over_behind() -> None:
    """A behind output is not wrong; a foreign-written one is — reporting
    "left alone" about something the run is about to remake is the one
    wrong answer, the same rule as stale-over-behind."""
    verdict = _classify(env_version="sha256:moved", foreign=_TWEAK)
    assert verdict.status == "stale"
    assert "tweak colors" in verdict.why


def test_an_environment_that_did_not_move_is_not_behind() -> None:
    """The negative half of the sensitivity pair: `behind` has to be off by
    default, or every output reports it forever and the signal is dead."""
    assert _classify(env_version=_ENV).status == "current"
