"""Tests for the run-scoped LocalCluster lifecycle."""
from __future__ import annotations

import pytest

from lightcone.engine.dask_cluster import (
    RESOURCE_CPUS,
    RESOURCE_GPUS,
    RESOURCE_MEMORY,
    _detect_node_shape,
    _NodeShape,
    _resource_dict,
    cluster_for_run,
)


class TestNodeShape:
    def test_detects_cpus(self) -> None:
        shape = _detect_node_shape()
        assert shape.cpus >= 1

    def test_resource_dict_always_advertises_cpus(self) -> None:
        res = _resource_dict(_NodeShape(cpus=4, mem_bytes=0, gpus=0))
        assert res == {RESOURCE_CPUS: 4.0}

    def test_resource_dict_includes_memory_when_known(self) -> None:
        res = _resource_dict(_NodeShape(cpus=2, mem_bytes=8_000_000_000, gpus=0))
        assert res[RESOURCE_MEMORY] == 8_000_000_000.0

    def test_resource_dict_includes_gpus_when_present(self) -> None:
        res = _resource_dict(_NodeShape(cpus=2, mem_bytes=0, gpus=1))
        assert res[RESOURCE_GPUS] == 1.0


@pytest.mark.slow
class TestLocalCluster:
    def test_yields_scheduler_address(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        with cluster_for_run(local_directory=str(tmp_path)) as env:
            addr = env["DASK_SCHEDULER_ADDRESS"]
            assert addr.startswith("tcp://")

            from dask.distributed import Client

            client = Client(addr)
            try:
                workers = client.scheduler_info()["workers"]
                assert len(workers) == 1
                resources = next(iter(workers.values()))["resources"]
                assert RESOURCE_CPUS in resources
            finally:
                client.close()
