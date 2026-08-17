"""Engine constants.

Constants ship *inside* the locked engine, never resolved at run time:
a new value reaches a project only through an engine release plus a
relock, which is what keeps environment identity a pure function of the
repo plus the engine (spec §3).

The image layer's own pinned digests (default base, uv distribution)
arrive with the container hatch; layer 1 needs only the interpreter pin.
"""

from __future__ import annotations

#: Exact interpreter patch scaffolded into ``.python-version`` by
#: ``lc init``. Projects may pin a different one — environment identity
#: follows the file, not this constant.
DEFAULT_PYTHON = "3.12.12"

#: The floor written into the scaffolded ``requires-python``, derived
#: from :data:`DEFAULT_PYTHON` so the two can never drift.
DEFAULT_PYTHON_FLOOR = ".".join(DEFAULT_PYTHON.split(".")[:2])

#: Minimum uv scaffolded into ``[tool.uv] required-version``. 0.12 is
#: the release the spec's empirical evidence pass was run against
#: (spec §13).
MIN_UV_VERSION = "0.12"
