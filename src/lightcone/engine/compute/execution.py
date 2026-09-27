"""Validate a borrowed cluster and submit ordinary Lightcone task functions."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

from lightcone.engine import container, project, venue
from lightcone.engine.project import ProjectError


def _signature() -> tuple[str, tuple[int, int], str]:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    digest.update((sys.platform + platform.machine() + sys.implementation.name).encode())
    digest.update(version("dask").encode())
    digest.update((root.parent / "_sandbox_exec.py").read_bytes())
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest(), sys.version_info[:2], version("distributed")


@dataclass(frozen=True)
class _Environment:
    root: Path
    marker: Path
    token: str
    signature: tuple[str, tuple[int, int], str]
    runtime: container.Runtime | None = None
    inputs: tuple[Path, ...] = ()


def _validate(environment: _Environment) -> str:
    """Check the actual worker, without touching git or converging its environment."""
    venue.require_compute_node("cluster execution")
    if _signature() != environment.signature:
        raise ProjectError("cluster worker's Lightcone, Python, or Dask does not match the driver")
    if environment.marker.read_text() != environment.token:
        raise ProjectError("cluster worker does not see the same project storage as the driver")
    for path in environment.inputs:
        if not path.exists() or not os.access(path, os.R_OK):
            raise ProjectError(f"cluster worker cannot read declared input `{path}`")
    runtime = environment.runtime
    if runtime is not None:
        project.require_uv()
        if not runtime.env_dir.is_dir():
            raise ProjectError(
                f"cluster worker cannot access project environment `{runtime.env_dir}`"
            )
        if runtime.mode == "containerized":
            if not shutil.which(runtime.runtime) or not container._loaded(
                runtime.root, runtime.runtime, runtime.image_id
            ):
                raise ProjectError(
                    f"cluster worker cannot use `{runtime.runtime}` image `{runtime.image_id}`; "
                    "make the prepared image available on every selected worker"
                )
        container.backend(runtime)
    acknowledgement = environment.marker.parent / uuid4().hex
    acknowledgement.write_text(environment.token)
    return str(acknowledgement)


def _execute(environment: _Environment, function: Callable[..., Any], *args: Any) -> Any:
    acknowledgement = Path(_validate(environment))
    acknowledgement.unlink()
    return function(*args)


@dataclass
class Execution:
    """Submit work only to the workers whose environment this invocation validated."""

    client: Any
    environment: _Environment
    addresses: tuple[str, ...]
    invocation: str = field(default_factory=lambda: uuid4().hex)

    def prepare(self, runtime: container.Runtime, inputs: Sequence[Path] = ()) -> None:
        """Validate the prepared runtime and input paths on every selected worker."""
        from dataclasses import replace

        self.environment = replace(self.environment, runtime=runtime, inputs=tuple(inputs))
        self.validate()

    def validate(self) -> None:
        """Prove worker compatibility and shared project reads and writes."""
        futures = [
            self.client.submit(
                _validate, self.environment, workers=[address], allow_other_workers=False,
                pure=False,
            )
            for address in self.addresses
        ]
        try:
            for future in futures:
                acknowledgement = Path(future.result(timeout=30))
                if acknowledgement.read_text() != self.environment.token:
                    raise ProjectError(
                        "cluster worker's project writes are not visible to the driver"
                    )
                acknowledgement.unlink()
        except Exception as exc:
            self.client.cancel(futures)
            raise ProjectError(f"cluster execution validation failed: {exc}") from exc

    def submit(self, function: Callable[..., Any], *args: Any, key: str) -> Any:
        """Submit a task with a unique invocation key and worker placement checks."""
        return self.client.submit(
            _execute, self.environment, function, *args,
            key=f"lc-{self.invocation}-{key}", workers=self.addresses,
            allow_other_workers=False, pure=False,
        )


@contextmanager
def workers(client: Any, root: Path) -> Iterator[Execution]:
    """Check a borrowed client's workers and keep a shared-storage challenge alive.

    Args:
        client: A connected Dask client, owned by the caller.
        root: The project at the same absolute path on every worker.

    Yields:
        The validated execution context; closing it does not close the cluster.
    """
    addresses = tuple(client.scheduler_info()["workers"])
    if not addresses:
        raise ProjectError("the selected cluster has no connected workers")
    private = root / ".lightcone"
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="execution-", dir=private) as directory:
        marker = Path(directory) / "shared-storage"
        token = uuid4().hex
        marker.write_text(token)
        execution = Execution(client, _Environment(root, marker, token, _signature()), addresses)
        execution.validate()
        yield execution
