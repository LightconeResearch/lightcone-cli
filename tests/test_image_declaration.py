"""Tests for the [tool.lightcone.image] declaration surface."""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_project

from lightcone.engine.image.declaration import (
    EMPTY_CANONICAL_JSON,
    BaseRef,
    load_image_declaration,
)
from lightcone.engine.image.errors import DeclarationError

_DIGEST = "sha256:" + "9f" * 32


class TestBaseRef:
    def test_parse_digest_pinned(self) -> None:
        ref = BaseRef.parse(f"nvcr.io/nvidia/cuda:12.4.1-runtime@{_DIGEST}")
        assert ref.name == "nvcr.io/nvidia/cuda:12.4.1-runtime"
        assert ref.digest == _DIGEST
        assert str(ref) == f"nvcr.io/nvidia/cuda:12.4.1-runtime@{_DIGEST}"

    def test_tag_only_refused(self) -> None:
        with pytest.raises(DeclarationError, match="pin the digest"):
            BaseRef.parse("nvcr.io/nvidia/cuda:12.4.1-runtime")

    def test_bad_digest_refused(self) -> None:
        with pytest.raises(DeclarationError, match="not.*valid digest"):
            BaseRef.parse("debian:bookworm@sha256:nothex")

    def test_short_digest_refused(self) -> None:
        with pytest.raises(DeclarationError, match="not.*valid digest"):
            BaseRef.parse("debian:bookworm@sha256:abcd")


class TestLoadImageDeclaration:
    def test_none_without_table(self, direct_project: Path) -> None:
        assert load_image_declaration(direct_project) is None

    def test_packages_sorted_and_deduped(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject=(
                "\n[tool.lightcone.image]\n"
                'system-packages = ["zlib1g", "r-base-core", "zlib1g"]\n'
            ),
        )
        decl = load_image_declaration(project)
        assert decl is not None
        assert decl.system_packages == ("r-base-core", "zlib1g")

    def test_unknown_key_refused(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject='\n[tool.lightcone.image]\npackages = ["r"]\n',
        )
        with pytest.raises(DeclarationError, match="unknown key"):
            load_image_declaration(project)

    def test_bad_apt_name_refused(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject=(
                '\n[tool.lightcone.image]\nsystem-packages = ["R Base!"]\n'
            ),
        )
        with pytest.raises(DeclarationError, match="not a valid apt package name"):
            load_image_declaration(project)

    def test_non_list_packages_refused(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject='\n[tool.lightcone.image]\nsystem-packages = "r"\n',
        )
        with pytest.raises(DeclarationError, match="list of"):
            load_image_declaration(project)

    def test_base_parsed(self, tmp_path: Path) -> None:
        project = make_project(
            tmp_path / "p",
            extra_pyproject=(
                f'\n[tool.lightcone.image]\nbase = "docker.io/x/y:1@{_DIGEST}"\n'
            ),
        )
        decl = load_image_declaration(project)
        assert decl is not None and decl.base is not None
        assert decl.base.digest == _DIGEST

    def test_extra_from_refused(self, direct_project: Path) -> None:
        (direct_project / "Containerfile.extra").write_text(
            "FROM debian:bookworm\nRUN echo hi\n"
        )
        with pytest.raises(DeclarationError, match="FROM"):
            load_image_declaration(direct_project)

    def test_extra_content_and_sha(self, direct_project: Path) -> None:
        (direct_project / "Containerfile.extra").write_text("RUN echo hi\n")
        decl = load_image_declaration(direct_project)
        assert decl is not None
        assert decl.extra == "RUN echo hi\n"
        assert decl.extra_sha256 is not None and len(decl.extra_sha256) == 64

    def test_canonical_json_stable(self, containerized_project: Path) -> None:
        decl = load_image_declaration(containerized_project)
        assert decl is not None
        assert decl.canonical_json() == (
            '{"base":null,"system-packages":["libhdf5-dev","r-base-core"]}'
        )

    def test_empty_canonical_shape_matches(self) -> None:
        """The direct-mode empty shape and a declaration's shape share
        the same key structure — one env_version formula."""
        assert EMPTY_CANONICAL_JSON == '{"base":null,"system-packages":[]}'
