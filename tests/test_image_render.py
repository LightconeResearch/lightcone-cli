"""Golden tests for the generated Containerfile.

The rendered text is half of the image tag's identity — these goldens
pin it byte-for-byte. Regenerate deliberately with:

    uv run python -m pytest tests/test_image_render.py --regen-goldens
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lightcone.engine.image import constants
from lightcone.engine.image.declaration import BaseRef, ImageDeclaration
from lightcone.engine.image.definition import ImageDefinition
from lightcone.engine.image.render import render

GOLDENS = Path(__file__).parent / "goldens"

_ENV_VERSION = "sha256:" + "ab" * 32
_BASE = BaseRef.parse(
    "nvcr.io/nvidia/cuda:12.4.1-runtime-ubuntu22.04@sha256:" + "9f" * 32
)


def _decl(
    *,
    base: BaseRef | None = None,
    packages: tuple[str, ...] = (),
    extra: str | None = None,
) -> ImageDeclaration:
    import hashlib

    return ImageDeclaration(
        base=base,
        system_packages=packages,
        extra=extra,
        extra_sha256=(
            hashlib.sha256(extra.encode()).hexdigest() if extra else None
        ),
    )


def _definition(decl: ImageDeclaration) -> ImageDefinition:
    from lightcone.engine.image.definition import DEFAULT_BASE, UV_DIST

    return ImageDefinition(
        base=decl.base or DEFAULT_BASE,
        system_packages=decl.system_packages,
        python_version="3.12.12",
        uv=UV_DIST,
        extra_stage=decl.extra,
        env_version=_ENV_VERSION,
    )


CASES = {
    "minimal": _decl(),
    "packages": _decl(packages=("libhdf5-dev", "r-base-core")),
    "custom-base": _decl(base=_BASE, packages=("texlive-latex-base",)),
    "extra-stage": _decl(
        packages=("r-base-core",),
        extra="RUN Rscript -e 'install.packages(\"cmdstanr\")'\n",
    ),
}


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "case_name" in metafunc.fixturenames:
        metafunc.parametrize("case_name", sorted(CASES))


class TestGolden:
    def test_matches_golden(self, case_name: str, request: pytest.FixtureRequest) -> None:
        rendered = render(_definition(CASES[case_name])).text
        golden = GOLDENS / f"{case_name}.Containerfile"
        if request.config.getoption("--regen-goldens"):
            golden.parent.mkdir(exist_ok=True)
            golden.write_text(rendered)
            pytest.skip("golden regenerated")
        assert golden.is_file(), f"missing golden {golden}; run --regen-goldens"
        assert rendered == golden.read_text()


class TestInvariants:
    def test_deterministic(self) -> None:
        d = _definition(CASES["packages"])
        assert render(d).text == render(d).text

    def test_package_order_is_canonical(self) -> None:
        """Shuffled declaration input renders identically (packages are
        sorted into the declaration, not at render time — but pin the
        composed behaviour end to end)."""
        a = render(_definition(_decl(packages=("a-pkg", "b-pkg")))).text
        b = render(_definition(_decl(packages=("a-pkg", "b-pkg")))).text
        assert a == b

    def test_offline_env_only_in_final_stage(self) -> None:
        """THE ordering invariant (spec §11 step 6): no offline key may
        appear textually before the final stage — the build's own sync
        layer must keep network."""
        for case in CASES.values():
            text = render(_definition(case)).text
            final_at = text.index("AS final")
            for key in constants.OFFLINE_ENV:
                assert key not in text[:final_at], (
                    f"{key} leaked above the final stage"
                )
                assert key in text[final_at:]

    def test_apt_layer_iff_packages(self) -> None:
        assert "apt-get install" not in render(_definition(CASES["minimal"])).text
        assert "apt-get install" in render(_definition(CASES["packages"])).text

    def test_apt_contract_check_iff_packages(self) -> None:
        assert (
            f"exit {constants.EXIT_NO_APT}"
            not in render(_definition(CASES["minimal"])).text
        )
        assert (
            f"exit {constants.EXIT_NO_APT}"
            in render(_definition(CASES["packages"])).text
        )

    def test_snapshot_after_extra_stage(self) -> None:
        """The dpkg snapshot runs in the final stage, after the extra
        stage — packages an extra stage installs are attested too."""
        text = render(_definition(CASES["extra-stage"])).text
        assert text.index("FROM env AS extra") < text.index(
            constants.DPKG_SNAPSHOT_PATH
        )
        assert "FROM extra AS final" in text

    def test_no_extra_stage_finals_from_env(self) -> None:
        assert "FROM env AS final" in render(_definition(CASES["minimal"])).text

    def test_no_project_code_enters_context(self) -> None:
        """G5 structural check: the only COPY from the build context is
        the two environment files."""
        for case in CASES.values():
            text = render(_definition(case)).text
            copies = [
                line
                for line in text.splitlines()
                if line.startswith("COPY") and "--from=" not in line
            ]
            assert copies == ["COPY pyproject.toml uv.lock ./"]

    def test_sync_flags(self) -> None:
        text = render(_definition(CASES["minimal"])).text
        assert "--locked --exact --no-install-project --compile-bytecode" in text

    def test_env_version_label(self) -> None:
        text = render(_definition(CASES["minimal"])).text
        assert f'LABEL io.lightcone.env-version="{_ENV_VERSION}"' in text
