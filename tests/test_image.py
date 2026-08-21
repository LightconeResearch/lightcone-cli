"""Tests for `lightcone.engine.image` — the declaration and the identity.

Containerfile assertions are about structure and ordering, never bytes:
this repo keeps no golden fixtures, and a byte-level test would pin the
render's prose rather than the properties that matter — what layers
exist, in what order, derived from which declaration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightcone.engine import identity, image, project
from lightcone.engine.project import ProjectError

_PIN = "3.12.11\n"


def _project(root: Path, table: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "analysis"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n' + table
    )
    (root / ".python-version").write_text(_PIN)
    (root / "uv.lock").write_text("version = 1\n")
    return root


_DECLARED = """
[tool.lightcone.image]
apt-install = ["r-base-core", "bc"]
run-commands = ["curl -fsSL https://example.org/tool.tar | tar x -C /opt/tool"]
env = { R_LIBS_SITE = "/opt/rlibs" }
"""


# ---- the declaration --------------------------------------------------------


def test_no_table_is_direct_mode(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    assert image.declaration(root) is None
    assert image.identity_document(root) is None
    assert project.mode(root) == "direct"


def test_an_empty_table_is_the_escalation(tmp_path: Path) -> None:
    """The table's presence is the whole trigger — a project may
    containerize purely for the default system layer."""
    root = _project(tmp_path / "p", "[tool.lightcone.image]\n")
    declared = image.declaration(root)
    assert declared is not None
    assert declared.base == image.DEFAULT_BASE
    assert declared.apt_install == ()
    assert project.mode(root) == "containerized"


def test_the_key_surface_is_closed(tmp_path: Path) -> None:
    """Every key is hashed, so a key nothing implements cannot ride along."""
    root = _project(tmp_path / "p", "[tool.lightcone.image]\npip-install = [\"numpy\"]\n")
    with pytest.raises(ProjectError, match="pip-install"):
        image.declaration(root)


def test_a_tag_only_base_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path / "p", '[tool.lightcone.image]\nbase = "debian:bookworm-slim"\n')
    with pytest.raises(ProjectError, match="digest"):
        image.declaration(root)


@pytest.mark.parametrize(
    "table",
    [
        '[tool.lightcone.image]\napt-install = "bc"\n',
        "[tool.lightcone.image]\nrun-commands = [1]\n",
        "[tool.lightcone.image]\nenv = { K = 1 }\n",
        "[tool.lightcone.image]\nbase = 3\n",
    ],
)
def test_a_wrong_type_is_refused(tmp_path: Path, table: str) -> None:
    with pytest.raises(ProjectError):
        image.declaration(_project(tmp_path / "p", table))


@pytest.mark.parametrize(
    "table",
    [
        # An apt "name" that is really a command reaches a raw RUN line.
        '[tool.lightcone.image]\napt-install = ["foo; rm -rf /"]\n',
        # A key with a space hits Docker's legacy `ENV key value` parse
        # and silently defines the wrong variable.
        '[tool.lightcone.image]\nenv = { "A B" = "v" }\n',
        # A newline in a value splices a Containerfile instruction of its
        # own into an identity-hashed surface.
        '[tool.lightcone.image]\nenv = { K = "a\\nUSER root" }\n',
        '[tool.lightcone.image]\nrun-commands = ["true\\nUSER root"]\n',
        '[tool.lightcone.image]\nbase = "a b@sha256:0000"\n',
    ],
)
def test_values_that_cannot_render_as_one_line_are_refused(tmp_path: Path, table: str) -> None:
    """Everything in the declaration is interpolated into Containerfile
    lines, so structural validation is what keeps the closed surface
    closed — a value that smuggles an instruction is not a value."""
    with pytest.raises(ProjectError):
        image.declaration(_project(tmp_path / "p", table))


def test_a_multiline_interpreter_pin_is_refused(tmp_path: Path) -> None:
    """The pin splices into the install layer's RUN line."""
    root = _project(tmp_path / "p", "[tool.lightcone.image]\n")
    (root / ".python-version").write_text("3.12.11\nUSER root\n")
    with pytest.raises(ProjectError, match="single interpreter"):
        image.containerfile(root)


def test_reads_pyproject_only_never_the_uv_config(tmp_path: Path) -> None:
    """A `uv.toml` replaces `[tool.uv]` — uv's rule about uv's own
    settings, which must not reach this table."""
    root = _project(tmp_path / "p", _DECLARED)
    (root / "uv.toml").write_text("no-build = true\n")
    declared = image.declaration(root)
    assert declared is not None and declared.apt_install == ("bc", "r-base-core")


# ---- the identity document --------------------------------------------------


def test_the_document_is_canonical(tmp_path: Path) -> None:
    root = _project(tmp_path / "p", _DECLARED)
    document = image.identity_document(root)
    assert document is not None
    parsed = json.loads(document)
    assert parsed["apt"] == ["bc", "r-base-core"]  # sorted into the identity
    assert parsed["base"] == image.DEFAULT_BASE
    assert parsed["env"] == {"R_LIBS_SITE": "/opt/rlibs"}
    assert parsed["uv"] == image.UV_IMAGE
    # One spelling: re-serializing canonically reproduces the text.
    assert document == json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def test_absent_keys_hash_as_their_empty_shape(tmp_path: Path) -> None:
    """A project relying on a default and one spelling it out are the same
    environment only until the default changes — so the keys are always
    emitted, the install-settings discipline."""
    spelled = _project(
        tmp_path / "a",
        f'[tool.lightcone.image]\nbase = "{image.DEFAULT_BASE}"\n'
        "apt-install = []\nrun-commands = []\nenv = {}\n",
    )
    bare = _project(tmp_path / "b", "[tool.lightcone.image]\n")
    assert image.identity_document(spelled) == image.identity_document(bare)


# ---- the Containerfile ------------------------------------------------------


def test_the_render_layers_in_the_generator_order(tmp_path: Path) -> None:
    root = _project(tmp_path / "p", _DECLARED)
    lines = image.containerfile(root).splitlines()

    assert lines[0] == f"FROM {image.DEFAULT_BASE}"
    order = [
        next(i for i, line in enumerate(lines) if marker in line)
        for marker in (
            "exit 43",  # the glibc contract
            "exit 44",  # the bash contract
            "exit 45",  # the apt contract, present because apt-install is
            "apt-get install",
            f"COPY --from={image.UV_IMAGE}",
            "uv python install 3.12.11",
            "ENV R_LIBS_SITE=",
            "RUN curl -fsSL",
            "UV_PYTHON_DOWNLOADS=never",
            "LABEL io.lightcone.image=",
        )
    ]
    assert order == sorted(order), "the fixed layering moved"
    # The readability chmod rides inside the layers that write /opt — a
    # layer of its own would copy-on-write the whole interpreter tree
    # into every archive.
    assert "uv python install 3.12.11 && chmod -R a+rX /opt" in image.containerfile(root)


def test_apt_layers_exist_only_when_packages_are_declared(tmp_path: Path) -> None:
    """apt is required iff `apt-install` is nonempty — the engine and the
    dataset stay on the host, so lc itself needs nothing from apt."""
    root = _project(tmp_path / "p", "[tool.lightcone.image]\n")
    text = image.containerfile(root)
    assert "apt-get" not in text
    assert "exit 45" not in text


def test_the_render_needs_the_interpreter_pin(tmp_path: Path) -> None:
    root = _project(tmp_path / "p", _DECLARED)
    (root / ".python-version").unlink()
    with pytest.raises(ProjectError, match=".python-version"):
        image.containerfile(root)


def test_the_render_refuses_a_direct_project(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="lightcone.image"):
        image.containerfile(_project(tmp_path / "p"))


def test_env_values_are_quoted_and_dollar_is_literal(tmp_path: Path) -> None:
    """The label is JSON full of double quotes, an env value may hold
    spaces, and `$` undergoes build-time expansion — measured, an
    unescaped `cost$5` bakes as `cost`. Declared values are literals."""
    root = _project(
        tmp_path / "p",
        '[tool.lightcone.image]\nenv = { OPTS = "-a \\"b\\" c", PRICE = "cost$5" }\n',
    )
    text = image.containerfile(root)
    assert 'ENV OPTS="-a \\"b\\" c"' in text
    assert 'ENV PRICE="cost\\$5"' in text
    assert 'LABEL io.lightcone.image="{' in text


# ---- the tag ----------------------------------------------------------------


def test_every_declared_key_moves_the_tag(tmp_path: Path) -> None:
    tags = {
        name: image.tag(_project(tmp_path / name, table))
        for name, table in {
            "bare": "[tool.lightcone.image]\n",
            "apt": '[tool.lightcone.image]\napt-install = ["bc"]\n',
            "run": '[tool.lightcone.image]\nrun-commands = ["true"]\n',
            "env": '[tool.lightcone.image]\nenv = { K = "v" }\n',
        }.items()
    }
    assert len(set(tags.values())) == len(tags)


def test_the_interpreter_pin_moves_the_tag(tmp_path: Path) -> None:
    """The pin is baked into the image, so it is an input to the tag even
    though the identity document deliberately omits it."""
    a = _project(tmp_path / "a", "[tool.lightcone.image]\n")
    b = _project(tmp_path / "b", "[tool.lightcone.image]\n")
    (b / ".python-version").write_text("3.13.1\n")
    assert image.tag(a) != image.tag(b)


def test_identical_declarations_are_one_tag_wherever_they_live(tmp_path: Path) -> None:
    a = _project(tmp_path / "somewhere" / "a", _DECLARED)
    b = _project(tmp_path / "elsewhere" / "b", _DECLARED)
    assert image.tag(a) == image.tag(b)
    assert image.archive_path(a, image.tag(a)) == (
        a / ".datalad" / "environments" / image.tag(a) / "image"
    )


# ---- env_version integration ------------------------------------------------


def test_declaring_an_image_moves_env_version(tmp_path: Path) -> None:
    """The system layer is part of what a recipe ran under, so declaring
    one puts every output behind — the model working, not a bug."""
    direct = _project(tmp_path / "a")
    containerized = _project(tmp_path / "b", "[tool.lightcone.image]\n")
    assert identity.env_version(direct) != identity.env_version(containerized)


def test_every_image_key_moves_env_version(tmp_path: Path) -> None:
    bare = identity.env_version(_project(tmp_path / "a", "[tool.lightcone.image]\n"))
    with_apt = identity.env_version(
        _project(tmp_path / "b", '[tool.lightcone.image]\napt-install = ["bc"]\n')
    )
    assert bare != with_apt


def test_other_lightcone_tables_do_not_move_env_version(tmp_path: Path) -> None:
    """Only the image table is the environment; a sibling table under
    `[tool.lightcone]` is somebody else's future."""
    plain = identity.env_version(_project(tmp_path / "a"))
    with_sibling = identity.env_version(
        _project(tmp_path / "b", '[tool.lightcone.something-else]\nkey = "v"\n')
    )
    assert plain == with_sibling
