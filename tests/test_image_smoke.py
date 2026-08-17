"""Real podman build smoke (opt-in: -m podman; needs network + rootless
podman)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import make_project

from lightcone.engine.environment import load_environment
from lightcone.engine.image import constants, ensure_image, read_record

pytestmark = [
    pytest.mark.podman,
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("podman") is None, reason="podman not installed"),
]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """Build the minimal containerized project image once per module."""
    project = make_project(
        tmp_path_factory.mktemp("smoke") / "proj",
        extra_pyproject='\n[tool.lightcone.image]\nsystem-packages = ["bc"]\n',
    )
    env = load_environment(project)
    record = ensure_image(project, env)
    return project, env, record


class TestRealBuild:
    def test_record_written(self, built) -> None:  # type: ignore[no-untyped-def]
        project, env, record = built
        assert read_record(project) == record
        assert record.env_version == env.env_version
        assert record.image_id.startswith("sha256:")

    def test_tag_hit_is_noop(self, built) -> None:  # type: ignore[no-untyped-def]
        project, env, record = built
        again = ensure_image(project, env)
        assert again == record

    def test_baked_identity_and_env(self, built) -> None:  # type: ignore[no-untyped-def]
        project, env, record = built
        out = subprocess.run(
            [
                "podman", "run", "--rm", "--pull=never", "--net=none",
                "--entrypoint=", record.image_id,
                "cat", constants.IDENTITY_PATH,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        identity = json.loads(out.stdout)
        assert identity["env_version"] == env.env_version
        assert identity["python_version"] == "3.12.12"

    def test_interpreter_and_venv_baked(self, built) -> None:  # type: ignore[no-untyped-def]
        _, _, record = built
        out = subprocess.run(
            [
                "podman", "run", "--rm", "--pull=never", "--net=none",
                "--entrypoint=", record.image_id,
                f"{constants.OPT_VENV}/bin/python", "-c",
                "import sys; print(sys.version.split()[0])",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == "3.12.12"

    def test_system_package_installed(self, built) -> None:  # type: ignore[no-untyped-def]
        _, _, record = built
        out = subprocess.run(
            [
                "podman", "run", "--rm", "--pull=never", "--net=none",
                "--entrypoint=", record.image_id,
                "sh", "-c", "echo '2+40' | bc",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == "42"

    def test_offline_env_baked(self, built) -> None:  # type: ignore[no-untyped-def]
        _, _, record = built
        out = subprocess.run(
            [
                "podman", "run", "--rm", "--pull=never", "--net=none",
                "--entrypoint=", record.image_id,
                "sh", "-c", "echo $UV_OFFLINE $UV_PYTHON_DOWNLOADS",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.split() == ["1", "never"]

    def test_dpkg_snapshot_attests_bc(self, built) -> None:  # type: ignore[no-untyped-def]
        project, _, record = built
        snapshot = (
            project / ".lightcone/image" / f"dpkg-snapshot-{record.tag}.txt"
        ).read_text()
        assert "bc" in snapshot


class TestAptErrorEndToEnd:
    def test_unlocatable_package_pointed_error(
        self, tmp_path: Path
    ) -> None:
        from lightcone.engine.image.errors import AptPackageNotFoundError

        project = make_project(
            tmp_path / "proj",
            extra_pyproject=(
                "\n[tool.lightcone.image]\n"
                'system-packages = ["lc-no-such-package-zz"]\n'
            ),
        )
        env = load_environment(project)
        with pytest.raises(
            AptPackageNotFoundError,
            match="no apt package named `lc-no-such-package-zz`",
        ):
            ensure_image(project, env)
