"""Build checkout assets; consume bundled assets when installing the sdist."""

import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendBuildHook(BuildHookInterface[Any]):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return
        root = Path(self.root)
        ui = root / "ui"
        if ui.is_dir():
            # An unconditional build includes styles, assets, dependency locks,
            # and compiler/bundler configuration, even after a git checkout.
            subprocess.run(["npm", "ci"], cwd=ui, check=True)
            subprocess.run(["npm", "run", "build"], cwd=ui, check=True)
        for name in ("bundle.js", "favicon.svg"):
            if not (root / "src/lightcone/app/static" / name).is_file():
                raise RuntimeError(f"Missing frontend asset: {name}")
