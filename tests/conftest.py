"""Shared test fixtures for lightcone-cli tests."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from lightcone.engine import dataset, project, templates
from lightcone.engine.project import _run as _real_run


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def venue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the host's venue out of the suite's environment.

    On a known center's login node every materialize test would otherwise
    meet the login guard, and inside an allocation the real-cluster test
    would launch srun across it. The site markers come from the guard's
    own table, so a center added there is scrubbed here for free; the
    venue tests set these back deliberately.
    """
    from lightcone.engine import venue

    for name in (
        *(site.marker for site in venue._SITES),
        "SLURM_JOB_ID",
        "SLURMD_NODENAME",
        "SLURM_JOB_NUM_NODES",
        "SLURM_NNODES",
        "SLURM_CPUS_ON_NODE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def ambient_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip scrubbable variables out of the suite's environment.

    CI pins its matrix interpreter through an ambient ``UV_PYTHON``,
    which the scrub correctly drops and reports — so without this every
    converge in the suite carries the warning and every ``warnings ==
    []`` assertion depends on the host; a dev shell exporting a site's
    ``MOUNT_*`` gates is the same shape. Derived from the scrub's own
    predicates, so a variable the allowlist later admits stops being
    stripped here for free; the scrub tests set their own variables
    back deliberately.
    """
    import os

    from lightcone.engine.project import _mount_scrubbed, _uv_scrubbed

    for name in [k for k in os.environ if _uv_scrubbed(k) or _mount_scrubbed(k)]:
        monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def machine_uv_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the suite to the host's machine-level uv configuration.

    The advisory probe reads ``~/.config/uv/uv.toml`` and
    ``/etc/uv/uv.toml`` — host state a fixture cannot scrub through the
    environment (``/etc`` has no variable), so a developer's own config
    would add a warning to every scan. The probe's own tests monkeypatch
    the paths back to fixtures deliberately.
    """
    from lightcone.engine import identity

    monkeypatch.setattr(identity, "_machine_config_paths", tuple)


@pytest.fixture(autouse=True)
def tools(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fake every external tool convergence shells out to, so the suite is
    hermetic — no network, no real resolution, no subprocesses.

    Models each tool's observable effect: ``uv lock`` writes ``uv.lock``,
    ``uv sync`` materializes ``.venv``, ``git init`` makes ``.git``,
    ``git annex init`` marks the repository annexed. The ``--check``
    probes and ``git config --get annex.uuid`` answer from what those
    left behind — enough for the convergence tests; drift is exercised by
    tests that stub ``_run`` themselves, since only uv can really tell a
    stale lock from a current one, and only git can really answer an
    ignore rule.

    Returns the recorded argv lists, so a test can assert on *which* tools
    ran with *which* flags. :func:`uv_calls` narrows that to uv.
    """
    calls: list[list[str]] = []
    # Repositories `git annex init` has been run in. Kept in memory
    # rather than on disk because `.git` is a *file* in a linked
    # worktree, so there is nowhere inside it to leave a marker.
    annexed: set[Path] = set()
    # What `git config` has been told, per repository — the annex filter
    # converges through a config write and a config read, and a fake that
    # forgot the write would report the same item repaired forever.
    config: dict[tuple[Path, str], str] = {}

    def fake_run(argv: list[str], *, cwd: Path) -> MagicMock:
        calls.append(list(argv))
        project = _project(argv) if "--project" in argv else cwd
        if argv[0] == "uv" and "--check" in argv:
            artifact = "uv.lock" if argv[1] == "lock" else ".venv"
            return MagicMock(returncode=0 if (project / artifact).exists() else 1)
        if argv[:2] == ["uv", "lock"]:
            # Only has to exist and be non-empty — layer 1 parses no lock.
            (project / "uv.lock").write_text("version = 1\n")
        elif argv[:2] == ["uv", "sync"]:
            (project / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        elif argv[:2] == ["git", "init"]:
            (cwd / ".git").mkdir(exist_ok=True)
        elif argv[:3] == ["git", "annex", "init"]:
            annexed.add(_repo(cwd))
        elif argv[:2] == ["git", "config"] and argv[-1] == "annex.uuid":
            return MagicMock(returncode=0 if _repo(cwd) in annexed else 1)
        elif argv[:3] == ["git", "config", "--local"] and "--get" in argv:
            # The key is last; the flags in between are git's own reading
            # conventions (`--type=bool`), which the store does not model
            # because the engine only ever writes one value.
            value = config.get((_repo(cwd), argv[-1]))
            code = 0 if value is not None else 1
            return MagicMock(returncode=code, stdout=(value or "") + "\n", stderr="")
        elif argv[:2] == ["git", "config"] and len(argv) == 4 and not argv[2].startswith("-"):
            config[(_repo(cwd), argv[2])] = argv[3]
        elif argv[:2] == ["git", "check-ignore"]:
            return _fake_check_ignore(cwd, argv[-1])
        return MagicMock(returncode=0, stdout="", stderr="")

    from lightcone.engine import project

    monkeypatch.setattr(project, "_run", fake_run)
    # Narrowly, for two reasons. `project.shutil` *is* the global
    # `shutil` module, so a blanket fake here patches `shutil.which` for
    # the whole suite — it did, and the sandbox tests built their exec
    # set from it, every tool resolving to a path that exists on Linux
    # and not on macOS, so the enforcement suite tested a policy no user
    # would ever get. And the answer is never invented: convergence asks
    # only *whether* uv, git and git-annex exist, and `_run` is faked
    # too, so nothing ever execs what comes back. Where the tool is
    # really installed, that is what is returned; where it is not, the
    # stub says so rather than naming a plausible path that isn't there.
    real_which = shutil.which

    def fake_which(name: str, path: str | None = None) -> str | None:
        if name in ("uv", "git", "git-annex"):
            return real_which(name) or f"/stub/{name}"
        return real_which(name, path=path)

    monkeypatch.setattr(project.shutil, "which", fake_which)
    return calls


class _Inline:
    """Run a graph in this thread, in the order it was submitted.

    Submission is topological, so a dependent is submitted only after its
    upstreams have already run — which means the "handles" it is passed
    are the upstream results themselves, exactly what the worker expects.
    """

    def submit(self, fn: Callable[..., object], *args: object, key: str) -> object:
        return fn(*args)

    def completed(self, handles: list[object]) -> Iterator[object]:
        yield from handles


@pytest.fixture
def inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the Dask cluster with an in-thread scheduler — the one
    monkeypatch point `cluster_for_run` exists to be."""
    from lightcone.engine import materialize

    @contextmanager
    def fake() -> Iterator[_Inline]:
        yield _Inline()

    monkeypatch.setattr(materialize, "cluster_for_run", fake)


@pytest.fixture
def real_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt out of :func:`tools`, and run the real git and git-annex.

    The hermetic default is right for convergence, which needs only each
    tool's observable effect. Storage is not: what ``git annex add`` does
    to a working tree *is* the thing under test, and no fake can tell you
    whether a file ended up as an annex symlink or as a blob in git.
    """
    from lightcone.engine import project

    monkeypatch.setattr(project, "_run", _real_run)


@pytest.fixture
def analysis(tmp_path: Path, real_tools: None) -> Callable[..., Path]:
    """Build a real, committed lc project — the one fixture that runs a graph.

    Everything is genuine: a uv environment, a git repository with an
    annex, an ``astra.yaml``, and a first commit. That is the price of
    testing execution at all — the questions are whether a recipe runs
    under the boundary, whether bytes land in the annex, and whether the
    tree is clean afterwards, and no stub answers any of them.

    It is cheap anyway: the project declares no dependencies, so `uv lock`
    and `uv sync` together cost milliseconds.
    """

    def build(
        spec: str,
        files: dict[str, str] | None = None,
        universes: dict[str, str] | None = None,
    ) -> Path:
        root = tmp_path / "analysis"
        for name in ("universes", "results", "data"):
            (root / name).mkdir(parents=True, exist_ok=True)

        (root / "pyproject.toml").write_text(
            '[project]\nname = "analysis"\nversion = "0.1.0"\n'
            'requires-python = ">=3.11"\ndependencies = []\n'
        )
        (root / ".python-version").write_text(templates.python_version())
        (root / ".gitattributes").write_text(templates.read("gitattributes.tmpl"))
        (root / ".gitignore").write_text(templates.read("gitignore.tmpl"))
        (root / "astra.yaml").write_text(textwrap.dedent(spec))
        for name, text in (universes or {"baseline": "id: baseline\ndecisions: {}\n"}).items():
            (root / "universes" / f"{name}.yaml").write_text(textwrap.dedent(text))
        for name, text in (files or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text))

        for argv in (["uv", "lock", "-q"], ["uv", "sync", "-q", "--locked", "--exact"]):
            _must(project._run([*argv, "--project", str(root)], cwd=root), argv)
        dataset.init_git(root)
        for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
            dataset._git(["config", key, value], cwd=root)
        dataset.init_annex(root)
        (root / ".datalad").mkdir(exist_ok=True)
        (root / ".datalad" / "config").write_text(
            templates.datalad_config(dataset_id="4b7b5c1e-0000-4000-8000-000000000000")
        )
        dataset.save(root, [root], "scaffold")
        return root

    return build


def _must(proc: object, argv: list[str]) -> None:
    if getattr(proc, "returncode", 1) != 0:
        raise AssertionError(f"{' '.join(argv)} failed:\n{getattr(proc, 'stderr', '')}")


def _repo(cwd: Path) -> Path:
    """The work tree *cwd* is in — an annex belongs to the repository, not
    to the directory the command happened to run from."""
    return next((p for p in [cwd, *cwd.parents] if (p / ".git").exists()), cwd)


def _fake_check_ignore(cwd: Path, path: str) -> MagicMock:
    """A deliberately literal stand-in for ``git check-ignore -v``.

    Not gitignore semantics — those belong to git, and the tests that need
    them use a real repository. This recognises exactly the shapes that put
    ``results/`` out of reach: the ``results/*`` an older lc scaffold wrote,
    and the ``results/`` or ``results`` someone writes by hand.
    """
    ignore = cwd / ".gitignore"
    if not ignore.exists():
        return MagicMock(returncode=1, stdout="", stderr="")
    wanted = path.rstrip("/")
    for number, line in enumerate(ignore.read_text().splitlines(), start=1):
        if (pattern := line.strip()) and pattern.rstrip("*").rstrip("/") == wanted:
            return MagicMock(
                returncode=0, stdout=f".gitignore:{number}:{pattern}\t{path}\n", stderr=""
            )
    return MagicMock(returncode=1, stdout="", stderr="")


def _project(argv: list[str]) -> Path:
    """The project root a uv invocation was pointed at."""
    return Path(argv[argv.index("--project") + 1])


def uv_calls(calls: list[list[str]]) -> list[list[str]]:
    """Just the uv invocations, with the leading ``uv`` stripped."""
    return [c[1:] for c in calls if c[0] == "uv"]


def probes(calls: list[list[str]]) -> list[list[str]]:
    """Just the read-only ``--check`` probes."""
    return [c for c in uv_calls(calls) if "--check" in c]


@pytest.fixture(scope="session")
def engine_dist(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    """Build the engine under test into a wheel the pinned record can find.

    The record pins ``lightcone-cli==<v>`` and the suite's build is not
    published, so the rerun's ephemeral environment is pointed here via
    ``UV_FIND_LINKS`` — which is what lets the suite execute the same
    record shape every real commit carries, rather than a test-only one.

    Returns:
        The wheel's exact version, and the directory serving it.
    """
    dist = tmp_path_factory.mktemp("engine-dist")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=Path(__file__).parent.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("*.whl"))
    return wheel.name.split("-")[1], dist
