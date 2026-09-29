"""Common resource selection, native dispatch, and CLI contracts."""

from __future__ import annotations

import json
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import yaml
from click.testing import CliRunner
from pydantic import BaseModel, ValidationError

from lightcone.cli.commands import main
from lightcone.engine import compute
from lightcone.engine.compute.catalog import Catalog
from lightcone.engine.compute.model import (
    GIB,
    Accelerator,
    ComputeError,
    Connection,
    Identity,
    LaunchPlan,
    Offer,
    Request,
    Resources,
    Snapshot,
    Startup,
    TimeLimits,
    UnavailableOfferError,
    duration,
    memory_bytes,
    validate_name,
)
from lightcone.engine.gpu import Device

NAMESPACE = "5a9d058c-7c6e-4e2a-919b-786f1148536c"
IDENTITY = Identity(namespace=NAMESPACE, native_id="1234", token="abc")


@pytest.fixture
def default_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("lightcone.engine.gpu.inventory", lambda: ())
    expanduser = Path.expanduser

    def expand(path: Path) -> Path:
        if str(path) == "~":
            return tmp_path
        if str(path).startswith("~/"):
            return tmp_path / str(path)[2:]
        return expanduser(path)

    monkeypatch.setattr(Path, "expanduser", expand)
    monkeypatch.delenv("LC_COMPUTE_CONFIG", raising=False)
    return tmp_path


@pytest.fixture
def catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "compute.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "connections": {"test": {"namespace": NAMESPACE, "provider": "fake"}},
                "offers": [
                    {
                        "name": name,
                        "connection": "test",
                        "resources": {"cpus": cpus, "memory": memory},
                        "max_nodes": nodes,
                        "time": {"default": "30m", "max": "2h"},
                        "startup": {"class": startup},
                    }
                    for name, cpus, memory, nodes, startup in [
                        ("quick", 4, 8, 1, "fast"),
                        ("large", 16, 32, 4, "batch"),
                    ]
                ],
            }
        )
    )
    monkeypatch.setenv("LC_COMPUTE_CONFIG", str(path))
    return path


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    adapter = MagicMock()
    adapter.plan.side_effect = lambda offer, request: LaunchPlan(
        connection=compute.Compute().catalog.connections["test"],
        offer=offer,
        request=request,
        seconds=request.seconds or offer.time.default_seconds,
    )
    adapter.launch.return_value = IDENTITY
    adapter.inspect.return_value = Snapshot(identity=IDENTITY, phase="active", num_nodes=1)
    adapter.discover.return_value = [Snapshot(identity=IDENTITY, phase="pending")]
    client = MagicMock()
    client.scheduler_info.return_value = {"workers": {"one": {}}}
    adapter.connect.return_value.__enter__.return_value = client
    monkeypatch.setitem(compute.PROVIDERS, "fake", lambda connection: adapter)
    return adapter


def test_identity_is_self_contained_and_canonical() -> None:
    assert Identity.decode(IDENTITY.encode()) == IDENTITY
    for value in ("slurm:1234", "local", IDENTITY.encode() + "=", "clu_A", "clu_eyJ2IjoxfQ"):
        with pytest.raises(ComputeError):
            Identity.decode(value)


def test_only_immutable_cluster_identities_are_hashable() -> None:
    decoded = Identity.decode(IDENTITY.encode())
    assert {IDENTITY, decoded} == {IDENTITY}
    snapshot = Snapshot(identity=IDENTITY, phase="pending")
    with pytest.raises(TypeError, match="unhashable"):
        hash(snapshot)
    snapshot.phase = "active"
    assert snapshot.phase == "active"


@pytest.mark.parametrize("name", ["analysis", "a", "run-2", "lc-7f3a92c810bd", "a" * 63])
def test_cluster_names_and_named_identities(name: str) -> None:
    validate_name(name)
    compute.validate_id(name)
    identity = IDENTITY.replace(name=name)
    assert Identity.decode(identity.encode()) == identity
    assert Snapshot(identity=identity, phase="pending").as_dict()["name"] == name


@pytest.mark.parametrize("name", ["", "A", "2a", "-a", "a-", "a_b", "a.b", "a b", "a\n",
                                  "a" * 64, "clu_foo", "a/../../b"])
def test_invalid_cluster_names_are_rejected_before_planning(
    catalog: Path, provider: MagicMock, name: str,
) -> None:
    with pytest.raises(ComputeError, match="cluster names"):
        compute.Compute().plan(Request.parse("4", "8"), name=name)
    provider.plan.assert_not_called()
    provider.launch.assert_not_called()


@pytest.mark.parametrize("phase", ["pending", "active", "stopping", "unknown"])
def test_launch_refuses_names_in_use_without_submitting(
    catalog: Path, provider: MagicMock, phase: str,
) -> None:
    provider.discover.return_value = [Snapshot(identity=IDENTITY, phase=phase)]
    service = compute.Compute()
    plan = service.plan(Request.parse("4", "8"), name=IDENTITY.name)
    with pytest.raises(ComputeError, match="already in use"):
        service.launch(plan)
    provider.launch.assert_not_called()


def test_generated_name_collision_retries_only_the_name(
    catalog: Path, provider: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = [UUID("12345678-1234-4234-8234-123456789abc"),
              UUID("87654321-4321-4321-8321-123456789abc")]
    provider.discover.return_value = [
        Snapshot(identity=IDENTITY.replace(name=f"lc-{values[0].hex[:12]}"), phase="pending")
    ]
    generator = MagicMock(side_effect=values)
    monkeypatch.setattr(compute, "uuid4", generator)
    service = compute.Compute()
    service.launch(service.plan(Request.parse("4", "8")))
    assert generator.call_count == 2
    assert provider.launch.call_count == 1
    assert provider.launch.call_args.args[0].name == "lc-876543214321"


def test_names_resolve_freshly_to_native_identities_for_every_operation(
    catalog: Path, provider: MagicMock,
) -> None:
    service = compute.Compute()
    assert service.status(IDENTITY.name).identity == IDENTITY
    with compute.connect(IDENTITY.name) as client:
        assert client is provider.connect.return_value.__enter__.return_value
    assert service.down(IDENTITY.name) == IDENTITY
    assert provider.discover.call_count == 3
    provider.terminate.assert_called_once_with(IDENTITY)


def test_named_cluster_errors_retain_the_immutable_id(
    catalog: Path, provider: MagicMock,
) -> None:
    provider.connect.return_value.__enter__.return_value.scheduler_info.return_value = {
        "workers": {}
    }
    with pytest.raises(ComputeError, match="expected workers") as error:
        with compute.connect(IDENTITY.name):
            pytest.fail("borrowed a degraded allocation")
    assert error.value.cluster_id == IDENTITY.encode()
    result = CliRunner().invoke(main, [
        "compute", "status", IDENTITY.name, "--wait", "--timeout", "0.01", "--json",
    ])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["id"] == IDENTITY.encode()
    provider.inspect.return_value.phase = "pending"
    with pytest.raises(ComputeError, match="pending") as error:
        with compute.connect(IDENTITY.name):
            pytest.fail("borrowed a pending allocation")
    assert error.value.cluster_id == IDENTITY.encode()


@pytest.mark.parametrize("observations", [[], [Snapshot(identity=IDENTITY, phase="ended")]])
def test_missing_and_ended_names_do_not_select_an_allocation(
    catalog: Path, provider: MagicMock, observations: list[Snapshot],
) -> None:
    provider.discover.return_value = observations
    with pytest.raises(ComputeError, match="no current cluster"):
        compute.Compute().down(IDENTITY.name)
    provider.terminate.assert_not_called()


def test_name_races_are_ambiguous_but_full_ids_still_work(
    catalog: Path, provider: MagicMock,
) -> None:
    other = IDENTITY.replace(native_id="5678", token="different")
    provider.discover.return_value = [
        Snapshot(identity=IDENTITY, phase="active"), Snapshot(identity=other, phase="pending"),
    ]
    service = compute.Compute()
    with pytest.raises(ComputeError, match="ambiguous") as error:
        service.down(IDENTITY.name)
    assert IDENTITY.encode() in str(error.value)
    assert other.encode() in str(error.value)
    provider.terminate.assert_not_called()
    service.down(IDENTITY.encode())
    provider.terminate.assert_called_once_with(IDENTITY)


def test_incomplete_discovery_cannot_establish_names_but_full_ids_still_work(
    catalog: Path, provider: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compute.Compute, "discover", lambda self: (
        [Snapshot(identity=IDENTITY, phase="active")], {"other": "native service unavailable"}
    ))
    service = compute.Compute()
    with pytest.raises(ComputeError, match="incomplete"):
        service.down(IDENTITY.name)
    with pytest.raises(ComputeError, match="incomplete"):
        service.launch(service.plan(Request.parse("4", "8"), name="analysis"))
    provider.terminate.assert_not_called()
    provider.launch.assert_not_called()
    service.down(IDENTITY.encode())
    provider.terminate.assert_called_once_with(IDENTITY)


def test_missing_default_catalog_exposes_stable_local_resources_without_writing_files(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = Catalog.load(), Catalog.load()
    assert first == second
    assert set(first.connections) == {"local"}
    connection = first.connections["local"]
    assert connection.provider == "local"
    assert str(UUID(connection.namespace)) == connection.namespace
    with monkeypatch.context() as patch:
        patch.setattr("socket.gethostname", lambda: "other-host")
        assert Catalog.load().connections["local"].namespace == connection.namespace
    assert Catalog.load().connections["local"].namespace == connection.namespace
    assert len(first.offers) == 1
    offer = first.offers[0]
    assert (offer.name, offer.connection) == ("local", "local")
    assert (offer.resources.cpus, offer.resources.memory_bytes, offer.max_nodes) == (1, GIB, 1)
    assert (offer.time.default_seconds, offer.time.max_seconds, offer.startup.class_) == (
        1800, 7200, "fast",
    )
    monkeypatch.setattr("dask.system.CPU_COUNT", 1)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", GIB)
    runner = CliRunner()
    resources = runner.invoke(main, ["compute", "resources", "--json"])
    assert resources.exit_code == 0, resources.output
    assert [item["name"] for item in json.loads(resources.output)["offers"]] == ["local"]
    planned = runner.invoke(
        main, ["compute", "launch", "--cpus", "1", "--memory", "1", "--dry-run", "--json"],
    )
    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.output)["plan"]["offer"] == "local"
    assert list(default_home.iterdir()) == []
    service = compute.Compute()
    assert service.plan(Request.parse("1", "1", time="2h", startup="fast")).seconds == 7200
    for request in (
        Request.parse("2", "1"), Request.parse("1", "2"),
        Request.parse("1", "1", num_nodes=2), Request.parse("1", "1", time="3h"),
    ):
        with pytest.raises(ComputeError, match="no configured offer"):
            service.plan(request)
    assert list(default_home.iterdir()) == []


def test_configured_catalogs_replace_the_builtin_and_obey_path_precedence(
    catalog: Path, default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LC_COMPUTE_CONFIG", raising=False)
    default = default_home / ".lightcone" / "compute.yaml"
    default.parent.mkdir()
    default.write_text(catalog.read_text())
    configured = Catalog.load()
    assert set(configured.connections) == {"test"}
    assert [offer.name for offer in configured.offers] == ["quick", "large"]
    # An empty configured catalog explicitly exposes nothing; the builtin is
    # never merged into it, whether selected by default, environment, or option.
    default.write_text("version: 1\nconnections: {}\noffers: []\n")
    assert Catalog.load().offers == []
    monkeypatch.setenv("LC_COMPUTE_CONFIG", str(catalog))
    assert Catalog.load() == configured
    assert Catalog.load(default).offers == []
    default.unlink()
    assert Catalog.load() == configured


def test_only_an_absent_implicit_catalog_uses_the_builtin(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = default_home / ".lightcone" / "compute.yaml"
    with pytest.raises(ComputeError, match="cannot read compute catalog"):
        Catalog.load(default)
    monkeypatch.setenv("LC_COMPUTE_CONFIG", str(default))
    with pytest.raises(ComputeError, match="cannot read compute catalog"):
        Catalog.load()
    monkeypatch.delenv("LC_COMPUTE_CONFIG")
    default.parent.mkdir()
    default.symlink_to(default_home / "absent.yaml")
    with pytest.raises(ComputeError, match="cannot read compute catalog"):
        Catalog.load()
    default.unlink()
    default.write_text("version: [\n")
    with pytest.raises(ComputeError, match="cannot read compute catalog"):
        Catalog.load()
    default.write_text("version: 1\nconnections: {}\noffers: []\n")

    def unreadable(_path: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError("catalog is not readable")

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(ComputeError, match="not readable"):
        Catalog.load()


@pytest.mark.parametrize(
    "cpus,memory", [("0", "2"), ("1.5", "2"), ("1++", "2"), ("2", "nan"), ("2", "0"), ("2", "-1")]
)
def test_request_rejects_invalid_resources(cpus: str, memory: str) -> None:
    with pytest.raises(ComputeError):
        Request.parse(cpus, memory)


@pytest.mark.parametrize(
    "override",
    [
        {"cpus": 0}, {"cpus": True}, {"memory_bytes": -1}, {"num_nodes": 0},
        {"num_nodes": 1.5}, {"seconds": 0}, {"seconds": float("inf")},
        {"min_cpus": "yes"}, {"startup": "batch"},
    ],
)
def test_programmatic_requests_validate_the_same_resource_contract(override: dict) -> None:
    with pytest.raises(ValidationError):
        Request(**{"cpus": 1, "memory_bytes": GIB, **override})


def test_memory_conversion_does_not_round_fractional_bytes() -> None:
    assert memory_bytes("0.000000000931322574615478515625") == 1
    with pytest.raises(ComputeError, match="exactly representable"):
        memory_bytes("0.000000000931322574615478515625000000000000000001")


@pytest.mark.parametrize("memory", ["8", "8GB", "8gb", "8192MB", "8GiB", "0.0078125TB"])
def test_compute_memory_uses_skypilot_binary_units(memory: str) -> None:
    assert Request.parse("1", memory + "+").memory_bytes == 8 * GIB
    assert Request.parse("1", memory + "+").min_memory
    assert Resources.model_validate({"cpus": 1, "memory": memory}).memory_bytes == 8 * GIB


def test_recipe_and_compute_memory_keep_their_specification_units() -> None:
    from lightcone.engine.execution_resources import TaskResources

    assert Request.parse("1", "8GB").memory_bytes == 8 * GIB
    assert TaskResources.parse({"memory": "8GB"}).memory_bytes == 8_000_000_000


def test_compute_memory_rejects_unit_suffix_without_a_size_prefix() -> None:
    with pytest.raises(ComputeError, match="memory"):
        Request.parse("1", "1IB")


@pytest.mark.parametrize("value", ["A100:4", {"A100": 4}])
def test_accelerator_sky_notations_share_one_model(value: object) -> None:
    resource = Resources.model_validate({"cpus": 1, "memory": 1, "accelerators": value})
    assert resource.accelerators == Accelerator(name="A100", count=4)
    assert resource.as_dict()["accelerators"] == {"A100": 4}


@pytest.mark.parametrize("value", [{"A100": 1, "H100": 1}, ["A100:1", "H100:1"], {"A100:1"}])
def test_accelerator_alternatives_are_not_silently_treated_as_capacity(value: object) -> None:
    with pytest.raises(ValidationError):
        Resources.model_validate({"cpus": 1, "memory": 1, "accelerators": value})


@pytest.mark.parametrize("count", [-1, 0, True, 0.5, "1", None])
def test_accelerator_envelopes_require_positive_integer_counts(count: object) -> None:
    with pytest.raises(ValidationError):
        Resources.model_validate({"cpus": 1, "memory": 1, "accelerators": {"A100": count}})
    with pytest.raises(ValidationError):
        Request.model_validate({"cpus": 1, "memory_bytes": GIB, "accelerators": {"A100": count}})


@pytest.mark.parametrize("gpus", ["-1", "1++", "", "A100:0.5", "A100:2+"])
def test_cli_gpu_requests_reject_invalid_accelerator_specifications(gpus: str) -> None:
    with pytest.raises(ComputeError, match="accelerators"):
        Request.parse("1", "1", gpus=gpus)


def test_accelerator_types_and_counts_survive_native_and_request_roundtrips() -> None:
    resource = Resources.from_bytes(cpus=8, memory_bytes=16 * GIB, gpus=4, accelerator_name="A100")
    assert resource.as_dict() == {"cpus": 8, "memory": 16, "accelerators": {"A100": 4}}
    assert Resources.model_validate_json(resource.model_dump_json(by_alias=True)) == resource
    assert resource.replace(cpus=4).accelerators == Accelerator(name="A100", count=4)
    request = Request.parse("8", "16", gpus="A100:2")
    assert request.gpus == 2 and request.accelerator_name == "A100"
    assert request.as_dict()["resources"]["accelerators"] == {"A100": 2}
    assert Request.parse("8", "16", gpus="A100").gpus == 1
    assert Request.parse("8", "16").gpus == 0


def test_numeric_native_accelerator_names_remain_types() -> None:
    resource = Resources.from_bytes(cpus=1, memory_bytes=GIB, gpus=2, accelerator_name="4090")
    request = Request.parse("1", "1", gpus="4090:2")
    assert request.accelerators is not None
    assert request.accelerators.matches(resource.accelerators)
    assert Request.parse("1", "1", gpus="4090").accelerators == Accelerator(name="4090", count=1)


def test_accelerator_selection_honors_type_and_exact_count(
    catalog: Path, provider: MagicMock,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    cpu = data["offers"][0]
    data["offers"].insert(0, {
        **cpu, "name": "gpu", "resources": {**cpu["resources"], "accelerators": "A100:4"},
    })
    catalog.write_text(yaml.safe_dump(data))
    service = compute.Compute()
    assert service.plan(Request.parse("4", "8")).offer.name == "quick"
    assert service.plan(Request.parse("4", "8", gpus="a100:4")).offer.name == "gpu"
    assert service.plan(Request.parse("4", "8", gpus="GPU:4")).offer.name == "gpu"
    for gpus in ("A100", "H100:4"):
        with pytest.raises(ComputeError, match="no configured offer"):
            service.plan(Request.parse("4", "8", gpus=gpus))
    data["offers"][0]["resources"]["accelerators"] = "GPU:4"
    catalog.write_text(yaml.safe_dump(data))
    with pytest.raises(ComputeError, match="no configured offer"):
        compute.Compute().plan(Request.parse("4", "8", gpus="A100:4"))


def test_builtin_gpu_offer_is_optional_and_does_not_replace_cpu_offer(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lightcone.engine.gpu.inventory", lambda: (
        Device("GPU-one", "A100"), Device("GPU-two", "A100"),
    ))
    loaded = Catalog.load()
    assert [(offer.name, offer.resources.gpus) for offer in loaded.offers] == [
        ("local", 0), ("local-gpu", 2),
    ]
    assert loaded.offers[1].connection == loaded.offers[0].connection
    assert loaded.offers[1].resources.accelerator_name == "A100"
    assert not list(default_home.iterdir())


def test_failed_optional_gpu_discovery_keeps_builtin_cpu_offer(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed() -> tuple[Device, ...]:
        raise ComputeError("CUDA driver could not enumerate visible devices")

    monkeypatch.setattr("lightcone.engine.gpu.inventory", failed)
    assert [(offer.name, offer.resources.gpus) for offer in Catalog.load().offers] == [("local", 0)]


def test_builtin_mixed_gpu_inventory_exposes_each_model_separately(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lightcone.engine.gpu.inventory", lambda: (
        Device("GPU-one", "H100"), Device("GPU-two", "A100"), Device("GPU-three", "H100"),
    ))
    loaded = Catalog.load()
    assert [(offer.name, offer.resources.as_dict()["accelerators"]) for offer in loaded.offers] == [
        ("local", None), ("local-gpu-1", {"A100": 1}), ("local-gpu-2", {"H100": 2}),
    ]


def test_configured_catalogs_do_not_probe_local_gpus(
    catalog: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected() -> tuple[Device, ...]:
        pytest.fail("configured catalogs must not discover ambient local GPUs")

    monkeypatch.setattr("lightcone.engine.gpu.inventory", unexpected)
    assert Catalog.load().offers


def test_cli_gpu_request_and_resource_output(catalog: Path, provider: MagicMock) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["offers"][0]["resources"]["accelerators"] = {"A100": 2}
    catalog.write_text(yaml.safe_dump(data))
    runner = CliRunner()
    result = runner.invoke(main, [
        "compute", "launch", "--cpus", "4", "--memory", "8", "--gpus", "A100:2",
        "--dry-run", "--json",
    ])
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)["plan"]
    assert plan["request"]["resources"]["accelerators"] == {"A100": 2}
    assert plan["resources"]["accelerators"] == {"A100": 2}
    resources = runner.invoke(main, ["compute", "resources", "--json"])
    assert json.loads(resources.output)["units"]["accelerators"] == "type and count per node"
    rendered = runner.invoke(main, ["compute", "resources"]).output
    assert "GPUS" in rendered and "A100:2" in rendered


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("1h30m", 5400), ("45s", 45), ("2d3h4m5s", 183845)],
)
def test_allocation_durations_accept_compound_units(value: str, seconds: int) -> None:
    assert duration(value) == seconds
    assert TimeLimits(default=value, max="3d").default_seconds == seconds
    assert Request.parse("1", "1", time=value).seconds == seconds


@pytest.mark.parametrize("value", ["", "0s", "1.5h", "30m1h", "1h30", 60, True])
def test_allocation_duration_refuses_ambiguous_or_zero_values(value: object) -> None:
    with pytest.raises(ComputeError):
        duration(value)


@pytest.mark.parametrize("size", [1, GIB // 2, 8 * GIB, 2**80 + 1])
def test_resource_units_survive_construction_serialization_and_updates(size: int) -> None:
    whole, fraction = divmod(size, GIB)
    expected_gib = Decimal(f"{whole}.{fraction * 5**30:030d}")
    resource = Resources.from_bytes(cpus=4, memory_bytes=size)
    assert resource.memory_gib == expected_gib
    assert Resources(cpus=4, memory_gib=expected_gib) == resource
    assert Resources.model_validate({"cpus": 4, "memory": format(expected_gib, "f")}) == resource
    for restored in (
        Resources.model_validate(resource.model_dump()),
        Resources.model_validate(resource.model_dump(by_alias=True)),
        Resources.model_validate_json(resource.model_dump_json(by_alias=True)),
        resource.replace(cpus=8),
    ):
        assert restored.memory_gib == expected_gib
        assert restored.memory_bytes == size
    assert Request(cpus=4, memory_bytes=size).memory_bytes == size


@pytest.mark.parametrize("size", [1, 2**80 + 1])
def test_request_quantities_roundtrip_under_low_decimal_precision(size: int) -> None:
    request = Request(cpus=4, memory_bytes=size, min_cpus=True, min_memory=True)
    with localcontext() as context:
        context.prec = 4
        quantities = request.as_dict()["resources"]
        restored = Request.parse(quantities["cpus"], quantities["memory"])
    assert quantities["cpus"] == "4+"
    assert quantities["memory"].endswith("+")
    assert restored == request


def test_catalog_uses_the_public_models_and_roundtrips_without_an_adapter(catalog: Path) -> None:
    loaded = Catalog.load(catalog)
    offer = loaded.offers[0]
    for instance, model in (
        (loaded, Catalog),
        (loaded.connections["test"], Connection),
        (offer, Offer),
        (offer.resources, Resources),
        (offer.time, TimeLimits),
        (offer.startup, Startup),
    ):
        assert isinstance(instance, BaseModel)
        assert type(instance) is model
    assert "name" not in loaded.connections["test"].model_dump()
    dumped = loaded.model_dump(by_alias=True)
    assert dumped["offers"][0]["resources"] == {
        "cpus": 4, "memory": Decimal(8), "accelerators": None,
    }
    assert dumped["offers"][0]["time"] == {"default": "30m", "max": "2h"}
    assert Catalog.model_validate(dumped) == loaded
    assert Catalog.model_validate_json(loaded.model_dump_json(by_alias=True)) == loaded


def test_model_updates_revalidate_fields_and_catalog_relationships(catalog: Path) -> None:
    loaded = Catalog.load(catalog)
    offer = loaded.offers[0]
    updated = offer.replace(resources=offer.resources.replace(cpus=8))
    assert updated.resources.cpus == 8
    assert offer.resources.cpus == 4
    assert updated.resources.memory_bytes == 8 * GIB
    with pytest.raises(ValidationError):
        offer.resources.replace(cpus=True)
    with pytest.raises(ValidationError):
        offer.time.replace(default="3h")
    with pytest.raises(ValidationError):
        loaded.replace(offers=[offer.replace(connection="missing")])
    with pytest.raises(ValidationError):
        Request(cpus=1, memory_bytes=GIB).replace(memory_bytes=0)


def test_exact_minimum_selection_and_limits(catalog: Path, provider: MagicMock) -> None:
    service = compute.Compute()
    assert service.plan(Request.parse("4", "8")).offer.name == "quick"
    assert service.plan(Request.parse("4+", "8+", num_nodes=2)).offer.name == "large"
    assert service.plan(Request.parse("5+", "9+")).resources.memory_bytes == 32 * GIB
    for request in [
        Request.parse("4", "9"),
        Request.parse("16", "32", startup="fast"),
        Request.parse("4+", "8+", time="3h"),
        Request.parse("1+", "1+", num_nodes=5),
    ]:
        with pytest.raises(ComputeError, match="no configured offer"):
            service.plan(request)
    provider.launch.assert_not_called()


def test_submit_failure_is_not_retried(catalog: Path, provider: MagicMock) -> None:
    provider.launch.side_effect = ComputeError("uncertain", submission_token="abc")
    service = compute.Compute()
    with pytest.raises(ComputeError, match="uncertain"):
        service.launch(service.plan(Request.parse("1+", "1+")))
    assert provider.launch.call_count == 1


def test_selection_skips_known_ineligibility_but_stops_on_an_unknown_authority(
    catalog: Path, provider: MagicMock,
) -> None:
    service = compute.Compute()
    selected = LaunchPlan(
        connection=service.catalog.connections["test"], offer=service.catalog.offers[1],
        request=Request.parse("1+", "1+"), seconds=1800,
    )
    provider.plan.side_effect = [UnavailableOfferError("login host"), selected]
    assert service.plan(Request.parse("1+", "1+")).offer.name == "large"
    provider.plan.reset_mock()
    provider.plan.side_effect = ComputeError("native authority unavailable")
    with pytest.raises(ComputeError, match="authority unavailable"):
        service.plan(Request.parse("1+", "1+"))
    provider.plan.assert_called_once()


def test_identity_survives_offer_removal(catalog: Path, provider: MagicMock) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["offers"] = []
    catalog.write_text(yaml.safe_dump(data))
    service = compute.Compute()
    assert service.status(IDENTITY.encode()).ready
    service.down(IDENTITY.encode())
    provider.terminate.assert_called_once_with(IDENTITY)


def test_discovery_preserves_unknown_authority(catalog: Path, provider: MagicMock) -> None:
    provider.discover.side_effect = ComputeError("native service unavailable")
    clusters, errors = compute.Compute().discover()
    assert clusters == []
    assert errors == {"test": "native service unavailable"}


def test_live_allocation_is_not_automatically_ready(catalog: Path, provider: MagicMock) -> None:
    provider.connect.side_effect = ComputeError("scheduler unavailable")
    result = compute.Compute().status(IDENTITY.encode())
    assert result.phase == "active"
    assert result.ready is False
    assert result.observation == "unreachable"
    with pytest.raises(ComputeError, match="allocation is unchanged"):
        compute.Compute().status(IDENTITY.encode(), wait=True, timeout=0.01)
    provider.terminate.assert_not_called()


def test_waiting_for_readiness_backs_off_between_native_queries(
    catalog: Path, provider: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider.inspect.return_value.phase = "pending"
    clock = [0.0]
    naps: list[float] = []

    def nap(seconds: float) -> None:
        naps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(compute.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(compute.time, "sleep", nap)
    with pytest.raises(ComputeError, match="did not become ready"):
        compute.Compute().status(IDENTITY.encode(), wait=True, timeout=300)
    assert naps[:6] == [1, 2, 4, 8, 16, 30]
    assert max(naps) == 30
    assert provider.inspect.call_count < 20

@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf"), 0])
def test_readiness_deadline_must_be_finite(
    catalog: Path, provider: MagicMock, timeout: float,
) -> None:
    with pytest.raises(ComputeError, match="finite and positive"):
        compute.Compute().status(IDENTITY.encode(), wait=True, timeout=timeout)
    with pytest.raises(ComputeError, match="finite and positive"):
        with compute.connect(IDENTITY.encode(), timeout=timeout):
            pytest.fail("an invalid deadline must not connect")
    provider.inspect.assert_not_called()
    provider.connect.assert_not_called()


def test_expired_readiness_deadline_does_not_begin_another_connection(
    catalog: Path, provider: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compute.time, "monotonic", MagicMock(side_effect=[10, 11]))
    with pytest.raises(ComputeError, match="did not become ready"):
        compute.Compute().status(IDENTITY.encode(), wait=True, timeout=0.5)
    provider.connect.assert_not_called()
    provider.terminate.assert_not_called()


def test_ended_allocation_cannot_be_borrowed(catalog: Path, provider: MagicMock) -> None:
    provider.inspect.return_value.phase = "ended"
    with pytest.raises(ComputeError, match="ended"), compute.connect(IDENTITY.encode()):
        pytest.fail("borrowed an ended allocation")
    provider.connect.assert_not_called()


def test_borrowed_client_only_detaches(catalog: Path, provider: MagicMock) -> None:
    with compute.connect(IDENTITY.encode()) as client:
        assert client is provider.connect.return_value.__enter__.return_value
    provider.connect.return_value.__exit__.assert_called_once()
    client.shutdown.assert_not_called()
    provider.terminate.assert_not_called()


@pytest.mark.parametrize("mutation", [
    "version_missing", "namespace", "context", "offer", "limits", "reference", "unknown",
    "resources_extra", "time_extra", "startup_extra", "connection_extra",
])
def test_invalid_catalog_is_rejected(catalog: Path, mutation: str) -> None:
    data = yaml.safe_load(catalog.read_text())
    if mutation == "version_missing":
        data.pop("version")
    elif mutation == "namespace":
        data["connections"]["other"] = {"namespace": NAMESPACE, "provider": "other"}
    elif mutation == "context":
        data["connections"]["other"] = {
            "namespace": "8613532d-378c-43f0-bf3a-132895093d6e",
            "provider": "fake",
        }
    elif mutation == "offer":
        data["offers"][1]["name"] = "quick"
    elif mutation == "limits":
        data["offers"][0]["time"]["default"] = "3h"
    elif mutation == "reference":
        data["offers"][0]["connection"] = "missing"
    elif mutation == "connection_extra":
        data["connections"]["test"]["extra"] = 1
    elif mutation.endswith("_extra"):
        data["offers"][0][mutation.removesuffix("_extra")]["extra"] = 1
    else:
        data["offers"][0]["gpus"] = 1
    catalog.write_text(yaml.safe_dump(data))
    with pytest.raises(ComputeError):
        Catalog.load(catalog)


@pytest.mark.parametrize("field,value", [
    (("version",), True),
    (("version",), 1.0),
    (("version",), "1"),
    (("connections", "test", "namespace"), NAMESPACE.upper()),
    (("offers", 0, "resources", "cpus"), True),
    (("offers", 0, "resources", "cpus"), 4.0),
    (("offers", 0, "max_nodes"), 1.0),
    (("offers", 0, "resources", "memory"), True),
    (("offers", 0, "resources", "memory"), "0.000000000931322574615478515626"),
    (("offers", 0, "time", "default"), 1800),
    (("offers", 0, "startup"), {}),
    (("offers",), None),
])
def test_catalog_rejects_coercion_with_the_field_location(
    catalog: Path, field: tuple[str | int, ...], value: object,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    parent = data
    for key in field[:-1]:
        parent = parent[key]
    parent[field[-1]] = value
    catalog.write_text(yaml.safe_dump(data))
    with pytest.raises(ComputeError) as error:
        Catalog.load(catalog)
    assert ".".join(map(str, field)) in str(error.value)


@pytest.mark.parametrize("startup", [
    "fast", {"class": "fast", "source": {"operator": [True, None]}}, None,
])
def test_catalog_normalizes_units_and_startup_without_changing_offer_order(
    catalog: Path, startup: object,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    offer = data["offers"][0]
    offer["resources"] = {"cpus": "4", "memory": "0.000000000931322574615478515625"}
    offer["max_nodes"] = "2"
    if startup is None:
        offer.pop("startup")
    else:
        offer["startup"] = startup
    catalog.write_text(yaml.safe_dump(data))
    loaded = Catalog.load(catalog)
    assert [entry.name for entry in loaded.offers] == ["quick", "large"]
    first = loaded.offers[0]
    assert (first.resources.cpus, first.resources.memory_bytes, first.max_nodes) == (4, 1, 2)
    assert (first.time.default_seconds, first.time.max_seconds) == (1800, 7200)
    assert first.startup.class_ == ("unknown" if startup is None else "fast")


def test_catalog_keeps_provider_payloads_opaque(catalog: Path) -> None:
    data = yaml.safe_load(catalog.read_text())
    launch = {"future-setting": {"nested": [True, 2, None, "value"]}}
    config = {"future-option": ["native", {"enabled": False}]}
    data["connections"]["test"].update(provider="future-provider", launch=launch)
    data["offers"][0]["config"] = config
    catalog.write_text(yaml.safe_dump(data))
    loaded = Catalog.load(catalog)
    assert loaded.connections["test"].provider == "future-provider"
    assert loaded.connections["test"].launch == launch
    assert loaded.offers[0].config == config


def test_catalog_validation_errors_do_not_echo_provider_values(catalog: Path) -> None:
    data = yaml.safe_load(catalog.read_text())
    secret = "private-provider-credential"
    data["connections"]["test"]["launch"] = secret
    catalog.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(main, ["compute", "resources", "--json"])
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert "connections.test.launch" in error
    assert secret not in error


@pytest.mark.parametrize("document", [
    "version: 1\nversion: 1\nconnections: {}\noffers: []\n",
    "connections: {test: {launch: {option: 1, option: 2}}}\n",
    "connections: {test: {launch: {1: value}}}\n",
])
def test_duplicate_or_nonstring_yaml_keys_are_rejected(catalog: Path, document: str) -> None:
    catalog.write_text(document)
    with pytest.raises(ComputeError, match="unique"):
        Catalog.load(catalog)


def test_invalid_catalog_encoding_is_a_structured_error(catalog: Path) -> None:
    catalog.write_bytes(b"version: 1\n\xff")
    with pytest.raises(ComputeError, match="cannot read compute catalog"):
        Catalog.load(catalog)
    result = CliRunner().invoke(main, ["compute", "resources", "--json"])
    assert result.exit_code == 1
    assert "cannot read compute catalog" in json.loads(result.output)["error"]


@pytest.mark.parametrize("setting", ["connection_root", "scratch_root", "python"])
def test_local_catalog_path_types_fail_without_a_traceback(catalog: Path, setting: str) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["connections"]["test"]["provider"] = "local"
    data["connections"]["test"]["launch"] = {setting: None}
    catalog.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(
        main, ["compute", "launch", "--cpus", "4", "--memory", "8", "--dry-run", "--json"]
    )
    assert result.exit_code == 1
    assert "path string" in json.loads(result.output)["error"]


@pytest.mark.parametrize("timeout", ["nan", "inf"])
def test_cli_rejects_nonfinite_wait_deadlines(
    catalog: Path, provider: MagicMock, timeout: str,
) -> None:
    result = CliRunner().invoke(
        main, ["compute", "status", IDENTITY.encode(), "--wait", "--timeout", timeout, "--json"]
    )
    assert result.exit_code == 1
    assert "finite" in json.loads(result.output)["error"]


def test_cli_resources_dry_run_launch_down(catalog: Path, provider: MagicMock) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["compute", "resources", "--json"])
    assert result.exit_code == 0, result.output
    resources = json.loads(result.output)
    assert [item["name"] for item in resources["offers"]] == ["quick", "large"]
    assert resources["offers"][0]["resources"] == {"cpus": 4, "memory": 8, "accelerators": None}
    args = ["compute", "launch", "--cpus", "4", "--memory", "8", "--json"]
    result = runner.invoke(main, [*args, "--dry-run"])
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)["plan"]
    assert plan["offer"] == "quick"
    assert plan["connection"] == "test"
    assert plan["resources"] == {"cpus": 4, "memory": 8, "accelerators": None}
    assert plan["time_seconds"] == 1800
    assert plan["startup"] == "fast"
    assert "memory_gib" not in result.output
    assert "class_" not in result.output
    provider.launch.assert_not_called()
    result = runner.invoke(main, args)
    assert json.loads(result.output)["id"] == IDENTITY.encode()
    result = runner.invoke(main, ["compute", "down", IDENTITY.encode(), "--json"])
    assert result.exit_code == 0, result.output
    provider.terminate.assert_called_once_with(IDENTITY)


def test_cli_resources_preserves_seconds(catalog: Path) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["offers"][0]["time"] = {"default": "45s", "max": "1m30s"}
    catalog.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(main, ["compute", "resources"])
    assert result.exit_code == 0, result.output
    assert "45s" in result.output
    assert "1m30s" in result.output


def test_cli_launch_name_output_can_be_captured_without_json(
    catalog: Path, provider: MagicMock,
) -> None:
    identity = IDENTITY.replace(name="analysis")
    provider.launch.return_value = identity
    runner = CliRunner()
    args = ["compute", "launch", "--name", "analysis", "--cpus", "4", "--memory", "8"]
    dry_run = runner.invoke(main, [*args, "--dry-run", "--json"])
    assert dry_run.exit_code == 0, dry_run.output
    assert json.loads(dry_run.stdout)["plan"]["name"] == "analysis"
    provider.discover.assert_not_called()
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    assert result.stdout == "analysis\n"
    assert "lc compute status analysis --wait" in result.stderr
    assert provider.launch.call_args.args[0].name == "analysis"
    result = runner.invoke(main, [*args, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["name"] == "analysis"
    assert json.loads(result.stdout)["id"] == identity.encode()
    provider.discover.return_value = [Snapshot(identity=identity, phase="pending")]
    result = runner.invoke(main, ["compute", "status"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "analysis: pending\n"
    result = runner.invoke(main, ["compute", "down", "analysis", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["id"] == identity.encode()
    assert json.loads(result.stdout)["name"] == "analysis"
    provider.terminate.assert_called_once_with(identity)


def test_cli_partial_failure_and_ambiguous_submit(catalog: Path, provider: MagicMock) -> None:
    provider.discover.side_effect = ComputeError("unavailable")
    runner = CliRunner()
    result = runner.invoke(main, ["compute", "status", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["errors"] == {"test": "unavailable"}
    provider.discover.side_effect = None
    provider.launch.side_effect = ComputeError("uncertain", submission_token="token")
    result = runner.invoke(main, ["compute", "launch", "--cpus", "4", "--memory", "8", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["submission_token"] == "token"


def test_degraded_cluster_is_observable_but_cannot_execute(
    catalog: Path, provider: MagicMock,
) -> None:
    client = provider.connect.return_value.__enter__.return_value
    client.scheduler_info.return_value = {"workers": {}}
    snapshot = compute.Compute().status(IDENTITY.encode())
    assert snapshot.phase == "active"
    assert snapshot.observation == "reachable"
    assert snapshot.workers == 0
    assert snapshot.ready is False
    with pytest.raises(ComputeError, match="expected workers"), compute.connect(IDENTITY.encode()):
        pytest.fail("borrowed a degraded cluster for execution")
    provider.terminate.assert_not_called()
