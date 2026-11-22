"""The environments a run's recipes run in, shared by every run of an identity.

Real uv, git and git-annex: whether an environment is built once, what a
recipe runs in, and what is left of the researcher's ``.venv`` are questions
no stub answers.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import CLUSTER_ID, _Inline

from lightcone.engine import (
    checkout,
    container,
    dataset,
    environments,
    identity,
    project,
    worker,
)
from lightcone.engine import materialize as engine

#: Two results that each say which environment their recipe ran in.
_PREFIX = """
spec_version: "0.1.0"
outputs:
  prefix:
    type: metric
    format: txt
    recipe:
      command: python -c 'import sys; print(sys.prefix)' > {output}
  again:
    type: metric
    format: txt
    recipe:
      command: python -c 'import sys; print(sys.prefix)' > {output}
"""


@pytest.fixture
def root(analysis: Callable[..., Path]) -> Path:
    return analysis(_PREFIX)


def _ran_in(root: Path, name: str) -> Path:
    return Path((root / "results" / f"{name}.txt").read_text().strip())


def _envs(root: Path) -> Path:
    return (root / environments.ENVS).resolve()


def _named(root: Path) -> str:
    """What the project's environment is called among them: its hash."""
    return identity.environment(root).rsplit(":", 1)[-1]


def _syncs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every uv sync the engine runs from here on, checks included."""
    run = project._run
    seen: list[list[str]] = []

    def recorded(argv: list[str], **kwargs: Any) -> Any:
        if argv[:2] == ["uv", "sync"]:
            seen.append(list(argv))
        return run(argv, **kwargs)

    monkeypatch.setattr(project, "_run", recorded)
    return seen


def test_runs_of_one_identity_share_one_environment_built_once(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Built for every run, an environment is tens of thousands of files,
    and on a cluster every byte copied. Built once for its identity, the
    next run asks uv whether it is still what the lock says, which writes
    nothing, and runs in it. The researcher's `.venv` is not touched."""
    venv = root / ".venv" / "pyvenv.cfg"
    before = venv.stat().st_mtime_ns
    syncs = _syncs(monkeypatch)
    linked: list[Path] = []

    class InFlight(_Inline):
        def completed(self, handles: list[object]) -> Iterator[object]:
            (top,) = checkout.held(root)
            linked.append((top / ".lightcone" / "env").resolve())
            yield from handles

    @contextmanager
    def cluster(cluster_id: str) -> Iterator[_Inline]:
        yield InFlight()

    monkeypatch.setattr(engine, "cluster_for_run", cluster)

    assert engine.materialize(root, ["prefix"], cluster_id=CLUSTER_ID).made == ["prefix"]
    built = [argv for argv in syncs if "--check" not in argv]
    syncs.clear()
    assert engine.materialize(root, ["again"], cluster_id=CLUSTER_ID).made == ["again"]

    env = _ran_in(root, "prefix")
    assert env.parent == _envs(root)
    assert env.name.startswith(f"{_named(root)}.")
    assert (_envs(root) / _named(root)).resolve() == env
    assert _ran_in(root, "again") == env
    # Linked from the run's checkout while it ran: what tells another run
    # that sweeps that it is in use.
    assert linked == [env, env]
    assert len(built) == 1 and all("--check" in argv for argv in syncs) and syncs
    assert venv.stat().st_mtime_ns == before
    assert not dataset.status(root)


def test_an_environment_changed_since_it_was_built_is_built_again(
    root: Path, inline: None
) -> None:
    """A published environment is never synced again, since a repair would
    rewrite it under another run's recipes. One someone installed into is
    no longer what the lock says: a run builds another, and says so."""
    engine.materialize(root, ["prefix"], cluster_id=CLUSTER_ID)
    first = _ran_in(root, "prefix")
    extra = next(first.glob("lib/python*/site-packages")) / "extra-1.0.dist-info"
    extra.mkdir()
    (extra / "METADATA").write_text("Metadata-Version: 2.1\nName: extra\nVersion: 1.0\n")
    (extra / "INSTALLER").write_text("uv\n")
    (extra / "RECORD").write_text("")

    report = engine.materialize(root, ["again"], cluster_id=CLUSTER_ID)

    assert report.made == ["again"]
    assert _ran_in(root, "again") not in (first, Path())
    assert any("had changed since it was built" in w for w in report.warnings)
    assert extra.is_dir()


def test_a_package_gets_an_environment_of_its_own_in_each_run(
    root: Path, inline: None
) -> None:
    """uv installs a package into its environment from the tree that
    synced it, so one shared would run another tree's code: a packaged
    project's environment is built in each run's checkout, as before."""
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text()
        + '\n[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        + '\n[tool.hatch.build.targets.wheel]\npackages = ["src/analysis"]\n'
    )
    (root / "src" / "analysis").mkdir(parents=True)
    (root / "src" / "analysis" / "__init__.py").write_text("")
    project._run(["uv", "lock", "-q", "--project", str(root)], cwd=root)
    dataset.save(root, [root], "make the project a package")
    assert not environments.shared(root)

    assert engine.materialize(root, ["prefix"], cluster_id=CLUSTER_ID).made == ["prefix"]

    ran_in = _ran_in(root, "prefix")
    assert ran_in.name == ".venv"
    assert ran_in.parent.parent == (root / checkout.RUNS).resolve()
    assert not (root / environments.ENVS).exists() or not list(_envs(root).iterdir())


@pytest.mark.parametrize(
    ("declared", "shared"),
    [
        ("", True),
        ('[build-system]\nrequires = ["hatchling"]\n', False),
        ('[build-system]\nrequires = ["hatchling"]\n[tool.uv]\npackage = false\n', True),
        ("[tool.uv]\npackage = true\n", False),
    ],
)
def test_what_is_shared_is_what_holds_nothing_of_a_tree(
    tmp_path: Path, declared: str, shared: bool
) -> None:
    """uv installs the project itself where it is a package: as `[tool.uv]
    package` says, else where it declares a build system."""
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "p"\n{declared}')

    assert environments.shared(tmp_path) is shared


def test_the_rerun_entry_point_shares_it_and_leaves_the_researchers_venv(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It runs in the project's own tree, whose `.venv` is the researcher's:
    uv is told the shared environment by name."""
    venv = root / ".venv" / "pyvenv.cfg"
    before = venv.stat().st_mtime_ns
    monkeypatch.chdir(root)

    assert worker.main(["prefix"]) == 0

    assert _ran_in(root, "prefix").parent == _envs(root)
    assert venv.stat().st_mtime_ns == before


def test_an_environment_nobody_uses_any_more_is_removed(
    root: Path, inline: None
) -> None:
    """Kept: the one in use for the run's identity, one a held checkout
    links, one used in the last day. Removed: any other, and a link that
    then leads nowhere."""
    engine.materialize(root, ["prefix"], cluster_id=CLUSTER_ID)
    current = _ran_in(root, "prefix")
    envs = _envs(root)

    def made(name: str, age: float) -> Path:
        folder = envs / f"{name}.00000000"
        folder.mkdir()
        os.utime(folder, (age, age))
        return folder

    gone = made("0" * 64, 0)
    (envs / ("0" * 64)).symlink_to(gone.name)
    held = made("1" * 64, 0)
    tree = checkout.take(root, dataset.head(root), CLUSTER_ID, [])
    (tree.root / ".lightcone").mkdir(exist_ok=True)
    (tree.root / ".lightcone" / "env").symlink_to(held)
    recent = made("2" * 64, os.path.getmtime(current))
    os.utime(current, (0, 0))

    engine.materialize(root, ["again"], cluster_id=CLUSTER_ID)

    assert sorted(entry.name for entry in envs.iterdir()) == sorted(
        [current.name, _named(root), held.name, recent.name]
    )
    checkout.leave(tree)


def test_two_runs_that_build_one_environment_at_once_each_finish_theirs(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither waits for the other, and neither is handed one half built:
    each builds at a path of its own, and the name leads to whichever was
    whole last."""
    runtime = container.runtime_for_run(root, build=False)
    name = identity.environment(root)
    converge = container.converge
    inner: list[Path] = []

    def and_another_meanwhile(world: container.Runtime) -> list[str]:
        if not inner:
            inner.append(Path())
            inner[0] = environments.prepare(root, runtime, name)[0].env_dir
        return converge(world)

    monkeypatch.setattr(container, "converge", and_another_meanwhile)

    outer = environments.prepare(root, runtime, name)[0].env_dir

    assert outer != inner[0]
    assert (outer / "pyvenv.cfg").is_file() and (inner[0] / "pyvenv.cfg").is_file()
    assert (_envs(root) / _named(root)).resolve() == outer


def test_a_build_cut_short_leaves_nothing_to_be_taken_for_an_environment(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupt too: the name leads to a whole environment, or to none."""
    runtime = container.runtime_for_run(root, build=False)

    def interrupted(world: container.Runtime) -> list[str]:
        (world.env_dir / "bin").mkdir(parents=True)
        raise KeyboardInterrupt

    monkeypatch.setattr(container, "converge", interrupted)

    with pytest.raises(KeyboardInterrupt):
        environments.prepare(root, runtime, identity.environment(root))

    assert list(_envs(root).iterdir()) == []
