"""Tests for `lightcone.engine.assets` — an output's bytes, record, and
whether it is still current.

The staleness table is the important part of this file. It is driven the
way both callers drive it — the worker with live content identities, and
`--check` with `None` for anything it has already decided will be rebuilt
— because the entire justification for one predicate is that those two
cannot disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightcone.engine import assets
from lightcone.engine.assets import Manifest, Reason, data_version, output_dir, staleness


def _manifest(**overrides: object) -> Manifest:
    base: dict[str, object] = {
        "output_id": "best_fit",
        "universe_id": "baseline",
        "recipe": "python src/fit.py {output}",
        "code_version": "sha256:code",
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


# ---- staleness -------------------------------------------------------------


def test_a_never_materialized_output_is_stale() -> None:
    assert staleness(code_version="sha256:code", manifest=None, inputs={}) == Reason("missing")


def test_an_unchanged_output_is_not_stale() -> None:
    assert (
        staleness(
            code_version="sha256:code",
            manifest=_manifest(),
            inputs={"catalog": "sha256:cat"},
        )
        is None
    )


def test_drifted_code_is_stale() -> None:
    """One reason covers recipe, decisions, and environment, because
    `code_version` is what all three feed."""
    assert staleness(
        code_version="sha256:other", manifest=_manifest(), inputs={"catalog": "sha256:cat"}
    ) == Reason("code")


def test_a_drifted_input_is_stale_and_says_which() -> None:
    assert staleness(
        code_version="sha256:code", manifest=_manifest(), inputs={"catalog": "sha256:new"}
    ) == Reason("input", "catalog")


def test_a_newly_declared_input_is_stale() -> None:
    """The manifest has no version recorded for it, so the sets differ."""
    assert staleness(
        code_version="sha256:code",
        manifest=_manifest(),
        inputs={"catalog": "sha256:cat", "mask": "sha256:mask"},
    ) == Reason("declaration", "mask")


def test_an_input_the_output_no_longer_declares_is_stale() -> None:
    """`code_version` hashes the recipe, the decisions and the environment —
    none of which a dropped input moves — so nothing else would catch it and
    the output would stay "up to date" with a dependency set that changed."""
    assert staleness(
        code_version="sha256:code", manifest=_manifest(), inputs={}
    ) == Reason("declaration", "catalog")


def test_an_input_that_will_be_rebuilt_is_stale() -> None:
    """`--check`'s sentinel. It cannot know whether a rebuild comes out
    byte-identical, so `None` means "this is going to change" — deliberate
    over-approximation, and the only difference between the two callers."""
    assert staleness(
        code_version="sha256:code", manifest=_manifest(), inputs={"catalog": None}
    ) == Reason("input", "catalog")


def test_the_sentinel_and_a_real_drift_give_the_same_reason() -> None:
    """The property that justifies one predicate with two callers: for the
    same state they agree, and `--check` differs from the worker only in
    what it is able to know."""
    checked = staleness(
        code_version="sha256:code", manifest=_manifest(), inputs={"catalog": None}
    )
    executed = staleness(
        code_version="sha256:code", manifest=_manifest(), inputs={"catalog": "sha256:rebuilt"}
    )
    assert checked == executed


def test_a_byte_identical_rebuild_stops_the_cascade() -> None:
    """What content hashing buys, and what the sentinel deliberately gives
    up: the worker sees the upstream came out the same and does not rerun,
    where `--check` had to assume it would."""
    assert (
        staleness(
            code_version="sha256:code",
            manifest=_manifest(),
            inputs={"catalog": "sha256:cat"},
        )
        is None
    )
