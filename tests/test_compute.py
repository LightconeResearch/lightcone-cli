"""Common resource selection, native dispatch, and CLI contracts."""

from __future__ import annotations

import json
import subprocess
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

IDENTITY = Identity(provider="fake", native_id="1234", token="abc")


@pytest.fixture
def default_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    expanduser = Path.expanduser

    def expand(path: Path) -> Path:
        if str(path) == "~":
            return tmp_path
        if str(path).startswith("~/"):
            return tmp_path / str(path)[2:]
        return expanduser(path)

    monkeypatch.setattr(Path, "expanduser", expand)
    monkeypatch.delenv("LC_COMPUTE_CONFIG", raising=False)
    monkeypatch.delenv("NERSC_HOST", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    return tmp_path


@pytest.fixture
def catalog(
    default_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: MagicMock,
) -> Path:
    path = tmp_path / "compute.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "allow_local": False,
                "offers": [
                    {
                        "name": name,
                        "provider": "fake",
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
    monkeypatch.setitem(compute.PROVIDERS, "fake", lambda root: adapter)
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
    monkeypatch.setattr("dask.system.CPU_COUNT", 1)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", GIB)
    first, second = Catalog.load(), Catalog.load()
    assert first == second
    assert first.providers == ["local"]
    assert len(first.offers) == 1
    offer = first.offers[0]
    assert (offer.name, offer.provider) == ("local", "local")
    assert (offer.resources.cpus, offer.resources.memory_bytes, offer.max_nodes) == (1, GIB, 1)
    # No hard lifetime: the built-in offer ends after 30 minutes without task activity.
    assert (offer.time.default_seconds, offer.time.max_seconds, offer.time.idle_seconds) == (
        None, None, 1800,
    )
    assert offer.startup.class_ == "fast"
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
    idle = service.plan(Request.parse("1", "1", startup="fast"))
    assert (idle.seconds, idle.idle_seconds) == (None, 1800)
    # An explicit hard lifetime has no built-in maximum, and the idle timeout still applies.
    both = service.plan(Request.parse("1", "1", time="3h"))
    assert (both.seconds, both.idle_seconds) == (10800, 1800)
    for request in (
        Request.parse("2", "1"), Request.parse("1", "2"), Request.parse("1", "1", num_nodes=2),
    ):
        with pytest.raises(ComputeError, match="no configured offer"):
            service.plan(request)
    assert list(default_home.iterdir()) == []


def test_configured_catalogs_can_disable_local_and_obey_path_precedence(
    catalog: Path, default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LC_COMPUTE_CONFIG", raising=False)
    default = default_home / ".lightcone" / "compute.yaml"
    default.parent.mkdir()
    default.write_text(catalog.read_text())
    configured = Catalog.load()
    assert configured.providers == ["fake", "local"]
    assert [offer.name for offer in configured.offers] == ["quick", "large"]
    # Disabled local compute remains available for inspection and termination.
    default.write_text("allow_local: false\noffers: []\n")
    assert Catalog.load().offers == []
    assert Catalog.load().providers == ["local"]
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
    default.write_text("offers: [\n")
    with pytest.raises(ComputeError, match="cannot read compute catalog"):
        Catalog.load()
    default.write_text("offers: []\n")

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


@pytest.mark.parametrize("value", [{"count": 2}, {"name": 2}, "count:2", "name:2"])
def test_accelerator_field_names_are_not_misread_as_device_types(value: object) -> None:
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
    assert request.accelerators == Accelerator(name="A100", count=2)
    assert request.as_dict()["resources"]["accelerators"] == {"A100": 2}
    assert Request.parse("8", "16", gpus="A100").accelerators == Accelerator(name="A100", count=1)
    assert Request.parse("8", "16").accelerators is None


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


@pytest.mark.parametrize("mask, gpus", [
    (None, 0), ("", 0), ("-1", 0), ("0,1", 2), ("GPU-8932f937,MIG-1c2d", 2), ("3,-1,0", 1),
])
def test_builtin_offer_takes_the_gpus_its_mask_exposes_without_probing_hardware(
    default_home: Path, monkeypatch: pytest.MonkeyPatch, mask: str | None, gpus: int,
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    if mask is not None:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", mask)
    probe = MagicMock(side_effect=AssertionError("catalog loading must not probe GPU hardware"))
    monkeypatch.setattr(subprocess, "run", probe)
    (offer,) = Catalog.load().offers
    assert (offer.name, offer.resources.gpus) == ("local", gpus)
    assert offer.resources.accelerator_name == ("GPU" if gpus else None)
    probe.assert_not_called()
    assert not list(default_home.iterdir())


def test_builtin_offer_has_no_gpus_outside_linux(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    assert Catalog.load().offers[0].resources.gpus == 0


def test_local_shortcut_takes_the_offer_whole_unless_cpu_only_is_asked(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    service = compute.Compute()
    whole = service.plan_local()
    assert whole.resources.gpus == 2
    assert whole.details["cuda_visible_devices"] == "0,1"
    assert service.plan_local(gpus="GPU:2").resources.gpus == 2
    cpu = service.plan_local(gpus="0")
    assert cpu.resources.gpus == 0
    assert cpu.details["cuda_visible_devices"] == ""
    with pytest.raises(ComputeError, match="no local offer"):
        service.plan_local(gpus="GPU:1")
    result = CliRunner().invoke(main, ["compute", "launch", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["plan"]["resources"]["accelerators"] == {"GPU": 2}


def test_local_shortcut_uses_detected_capacity_without_writing_files(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dask.system.CPU_COUNT", 6)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", 7 * GIB + 123)
    result = CliRunner().invoke(main, ["compute", "launch", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    plan = compute.Compute().plan_local()
    assert plan.name == "local"
    assert plan.resources.cpus == 6
    assert plan.resources.memory_bytes == 7 * GIB + 123
    assert plan.details["task_slots_per_node"] == 6
    assert json.loads(result.stdout)["plan"] == plan.as_dict()
    assert not list(default_home.iterdir())


def test_nersc_login_nodes_block_first_launch_without_writing_a_catalog(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NERSC_HOST", raising=False)
    monkeypatch.setattr("dask.system.CPU_COUNT", 1)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", GIB)
    before = compute.Compute()
    plan = before.plan_local()
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setattr("socket.gethostname", lambda: "login07.nersc.gov")
    service = compute.Compute()
    assert not service.catalog.allow_local
    assert service.catalog.providers == ["local"]
    assert service.resources()["offers"] == []
    for flags in ([], ["--dry-run"]):
        result = CliRunner().invoke(main, ["compute", "launch", *flags, "--json"])
        assert result.exit_code == 1, result.output
        assert "disabled on NERSC login nodes" in json.loads(result.stdout)["error"]
    with pytest.raises(ComputeError, match="disabled on NERSC login nodes"):
        before.launch(plan)
    assert not list(default_home.iterdir())


@pytest.mark.parametrize("site, hostname", [
    ("perlmutter", "nid005678"), ("perlmutter", "workstation"), ("", "login07"),
])
def test_local_compute_remains_available_outside_identified_nersc_login_nodes(
    default_home: Path, monkeypatch: pytest.MonkeyPatch, site: str, hostname: str,
) -> None:
    monkeypatch.setenv("NERSC_HOST", site)
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setattr("socket.gethostname", lambda: hostname)
    monkeypatch.setattr("dask.system.CPU_COUNT", 1)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", GIB)
    result = CliRunner().invoke(main, ["compute", "launch", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["plan"]["offer"] == "local"
    assert not list(default_home.iterdir())


def test_nersc_login_guard_keeps_remote_compute_and_local_inspection_available(
    catalog: Path, provider: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = IDENTITY.replace(provider="local", native_id="5678")
    data = yaml.safe_load(catalog.read_text())
    data["allow_local"] = True
    data["offers"].insert(0, {**data["offers"][0], "name": "workstation", "provider": "local"})
    catalog.write_text(yaml.safe_dump(data))
    local = MagicMock()
    local.discover.return_value = []
    local.inspect.return_value = Snapshot(identity=identity, phase="active", num_nodes=1)
    local.connect.return_value.__enter__.return_value.scheduler_info.return_value = {
        "workers": {"one": {}},
    }
    monkeypatch.setitem(compute.PROVIDERS, "local", lambda root: local)
    monkeypatch.setenv("NERSC_HOST", "perlmutter")
    monkeypatch.setattr("socket.gethostname", lambda: "login07")
    service = compute.Compute()
    assert not service.catalog.allow_local
    assert [offer.name for offer in service.catalog.offers] == ["quick", "large"]
    plan = service.plan(Request.parse("4", "8"))
    assert service.launch(plan) == IDENTITY
    assert service.status(IDENTITY.encode()).ready
    with compute.connect(IDENTITY.encode()) as client:
        assert client is provider.connect.return_value.__enter__.return_value
    with pytest.raises(ComputeError, match="disabled on NERSC login nodes"):
        with compute.connect(identity.encode()):
            pytest.fail("borrowed local compute on a login node")
    local.connect.assert_not_called()
    assert service.status(identity.encode()).ready
    assert service.down(identity.encode()) == identity
    local.terminate.assert_called_once_with(identity)
    local.plan.assert_not_called()
    local.launch.assert_not_called()


def test_an_explicit_local_offer_replaces_the_builtin_and_allow_local_disables_both(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dask.system.CPU_COUNT", 8)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", 16 * GIB)
    path = default_home / "compute.yaml"
    offer = "{name: local, provider: local, resources: {cpus: 2, memory: 3}, max_nodes: 1, "
    offer += "time: {idle: 30m}}"
    path.write_text(f"offers:\n  - {offer}\n")
    monkeypatch.setenv("LC_COMPUTE_CONFIG", str(path))
    service = compute.Compute()
    assert [offer.name for offer in service.catalog.offers] == ["local"]
    plan = service.plan_local(name="sandbox", time="1h")
    assert (plan.name, plan.resources.cpus, plan.resources.memory_bytes) == ("sandbox", 2, 3 * GIB)
    assert plan.seconds == 3600
    path.write_text(f"allow_local: false\noffers:\n  - {offer}\n")
    disabled = compute.Compute()
    assert not disabled.resources()["offers"]
    assert disabled.catalog.providers == ["local"]
    with pytest.raises(ComputeError, match="disabled"):
        disabled.plan_local()
    with pytest.raises(ComputeError, match="disabled"):
        disabled.launch(plan)
    with pytest.raises(ComputeError, match="no configured offer"):
        disabled.plan(Request.parse("2", "3"))


def test_catalog_adds_local_after_remote_offers_and_shortcut_never_selects_remote(
    catalog: Path, provider: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    data.pop("allow_local")
    catalog.write_text(yaml.safe_dump(data))
    monkeypatch.setattr("dask.system.CPU_COUNT", 4)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", 8 * GIB)
    service = compute.Compute()
    assert [offer.name for offer in service.catalog.offers] == ["quick", "large", "local"]
    assert service.plan(Request.parse("4", "8")).offer.name == "quick"
    assert service.plan_local().offer.provider == "local"
    with pytest.raises(ComputeError, match="no local offer"):
        service.plan_local(num_nodes=2)
    provider.launch.assert_not_called()


def test_builtin_name_conflict_identifies_the_catalog_and_remedy(catalog: Path) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["allow_local"] = True
    data["offers"][0]["name"] = "local"
    catalog.write_text(yaml.safe_dump(data))
    with pytest.raises(ComputeError) as error:
        Catalog.load()
    assert f"invalid compute catalog {catalog}" in str(error.value)
    assert "offer name 'local' is reserved for the built-in local backend" in str(error.value)
    assert "rename the configured offer" in str(error.value)


def test_an_explicit_local_offer_sets_its_own_time_limits(
    default_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dask.system.CPU_COUNT", 8)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", 16 * GIB)
    path = default_home / "compute.yaml"
    path.write_text(
        "offers:\n  - {name: local, provider: local, resources: {cpus: 8, memory: 16}, "
        "max_nodes: 1, time: {idle: 1h, max: 8h}}\n"
    )
    monkeypatch.setenv("LC_COMPUTE_CONFIG", str(path))
    service = compute.Compute()
    plan = service.plan_local()
    assert (plan.seconds, plan.idle_seconds) == (None, 3600)
    assert plan.as_dict()["idle_seconds"] == 3600
    assert service.plan_local(time="8h").seconds == 8 * 3600
    with pytest.raises(ComputeError, match="local shapes and time limits"):
        service.plan_local(time="9h")
    listed = CliRunner().invoke(main, ["compute", "resources"])
    assert listed.exit_code == 0, listed.output
    assert "IDLE" in listed.output and "60m" in listed.output


def test_disabled_builtin_does_not_reserve_remote_offer_names(catalog: Path) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["offers"][0]["name"] = "local"
    catalog.write_text(yaml.safe_dump(data))
    loaded = Catalog.load()
    assert [offer.name for offer in loaded.offers] == ["local", "large"]
    assert loaded.offers[0].provider == "fake"


def test_explicit_local_offers_keep_their_sizes_and_replace_the_implicit_offer(
    catalog: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["allow_local"] = True
    for offer in data["offers"]:
        offer["provider"] = "local"
    catalog.write_text(yaml.safe_dump(data))
    monkeypatch.setattr("dask.system.CPU_COUNT", 8)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", 16 * GIB)
    service = compute.Compute()
    assert [offer.name for offer in service.catalog.offers] == ["quick", "large"]
    plan = service.plan_local()
    assert (plan.offer.name, plan.name, plan.resources.cpus) == ("quick", "local", 4)
    assert plan.resources.memory_bytes == 8 * GIB


def test_configured_local_budget_still_must_fit_host_capacity(
    catalog: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["allow_local"] = True
    data["offers"] = [{
        "name": "local", "provider": "local", "resources": {"cpus": 8, "memory": 16},
        "max_nodes": 1, "time": {"idle": "30m"},
    }]
    catalog.write_text(yaml.safe_dump(data))
    monkeypatch.setattr("dask.system.CPU_COUNT", 4)
    monkeypatch.setattr("distributed.system.MEMORY_LIMIT", 8 * GIB)
    result = CliRunner().invoke(main, ["compute", "launch", "--dry-run", "--json"])
    assert result.exit_code == 1
    assert "exceeds this host's CPU or RAM capacity" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("value", ["false", 0, None])
def test_allow_local_must_be_a_boolean(catalog: Path, value: object) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["allow_local"] = value
    catalog.write_text(yaml.safe_dump(data))
    with pytest.raises(ComputeError, match="allow_local"):
        Catalog.load()


def test_disabled_policy_blocks_local_execution_but_allows_status_and_down(
    catalog: Path, provider: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    for offer in data["offers"]:
        offer["provider"] = "local"
    catalog.write_text(yaml.safe_dump(data))
    monkeypatch.setitem(compute.PROVIDERS, "local", lambda root: provider)
    identity = IDENTITY.replace(provider="local")
    service = compute.Compute()
    assert service.catalog.offers == []
    with pytest.raises(ComputeError, match="disabled"):
        with compute.connect(identity.encode()):
            pytest.fail("borrowed disabled local compute")
    provider.connect.assert_not_called()
    assert service.status(identity.encode()).ready
    service.down(identity.encode())
    provider.terminate.assert_called_once_with(identity)


def test_configured_catalogs_do_not_probe_local_gpus(
    catalog: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = MagicMock(side_effect=AssertionError("catalog loading must not probe GPU hardware"))
    monkeypatch.setattr(subprocess, "run", probe)
    assert Catalog.load().offers
    probe.assert_not_called()


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
    with pytest.raises(ValueError):
        duration(value)
    with pytest.raises(ValidationError):
        TimeLimits(default=value, max="3d")
    with pytest.raises(ComputeError):
        Request.parse("1", "1", time=value)


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
        (offer, Offer),
        (offer.resources, Resources),
        (offer.time, TimeLimits),
        (offer.startup, Startup),
    ):
        assert isinstance(instance, BaseModel)
        assert type(instance) is model
    dumped = loaded.model_dump(by_alias=True)
    assert dumped["offers"][0]["resources"] == {
        "cpus": 4, "memory": Decimal(8), "accelerators": None,
    }
    assert dumped["offers"][0]["time"] == {"default": "30m", "max": "2h", "idle": None}
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
        loaded.replace(offers=[offer, offer])
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
        offer=service.catalog.offers[1], request=Request.parse("1+", "1+"), seconds=1800,
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
    assert errors == {"fake": "native service unavailable"}


def test_live_allocation_is_not_automatically_ready(catalog: Path, provider: MagicMock) -> None:
    provider.connect.side_effect = ComputeError("scheduler unavailable")
    result = compute.Compute().status(IDENTITY.encode())
    assert result.phase == "active"
    assert result.ready is False
    assert result.observation == "unreachable"
    # A wait that runs out names why the scheduler could not be reached.
    with pytest.raises(ComputeError, match="last connection attempt: scheduler unavailable"):
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
    client = provider.connect.return_value.__enter__.return_value
    with compute.connect(IDENTITY.encode()) as borrowed:
        assert borrowed is client
    provider.connect.return_value.__exit__.assert_called_once()
    client.shutdown.assert_not_called()
    provider.terminate.assert_not_called()
    # An execution connection restarts the idle countdown; status polling does not.
    client.submit.assert_called_once_with(int, pure=False)
    compute.Compute().status(IDENTITY.encode())
    client.submit.assert_called_once()


@pytest.mark.parametrize("mutation", [
    "provider_missing", "provider", "offer", "limits", "unbounded",
    "unknown", "resources_extra", "time_extra", "startup_extra", "catalog_extra",
])
def test_invalid_catalog_is_rejected(catalog: Path, mutation: str) -> None:
    data = yaml.safe_load(catalog.read_text())
    if mutation == "provider_missing":
        data["offers"][0].pop("provider")
    elif mutation == "provider":
        data["offers"][0]["provider"] = "Not a provider"
    elif mutation == "offer":
        data["offers"][1]["name"] = "quick"
    elif mutation == "limits":
        data["offers"][0]["time"]["default"] = "3h"
    elif mutation == "unbounded":
        data["offers"][0]["time"] = {"max": "2h"}
    elif mutation == "catalog_extra":
        data["connections"] = {}
    elif mutation.endswith("_extra"):
        data["offers"][0][mutation.removesuffix("_extra")]["extra"] = 1
    else:
        data["offers"][0]["gpus"] = 1
    catalog.write_text(yaml.safe_dump(data))
    with pytest.raises(ComputeError):
        Catalog.load(catalog)


@pytest.mark.parametrize("field,value", [
    (("connection_root",), 1),
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
    config = {"future-option": ["native", {"enabled": False, "nested": [True, 2, None]}]}
    data["offers"][0].update(config=config)
    catalog.write_text(yaml.safe_dump(data))
    assert Catalog.load(catalog).offers[0].config == config


def test_a_misspelled_provider_is_a_catalog_error_not_a_discovery_failure(
    catalog: Path,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["offers"][1]["provider"] = "slurmm"
    catalog.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(main, ["compute", "status", "--json"])
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert "invalid compute catalog" in error
    assert "offers.1.provider: Value error, must name a supported provider" in error


@pytest.mark.parametrize("root, message", [
    ("relative/compute", "compute root must be an absolute path"),
    ("/tmp/../compute", "compute root must be an absolute path"),
    ("~nosuchuser42/compute", "cannot resolve compute root"),
])
def test_an_unusable_connection_root_is_a_catalog_error(
    catalog: Path, root: str, message: str,
) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["connection_root"] = root
    catalog.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(main, ["compute", "status", "--json"])
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert "invalid compute catalog" in error
    assert f"connection_root: Value error, {message}" in error


def test_the_connection_root_is_resolved_once_and_handed_to_one_provider_per_name(
    catalog: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    (tmp_path / "alias").symlink_to(physical, target_is_directory=True)
    data = yaml.safe_load(catalog.read_text())
    data["connection_root"] = str(tmp_path / "alias" / "compute")
    catalog.write_text(yaml.safe_dump(data))
    roots: list[Path] = []
    factory = compute.PROVIDERS["fake"]
    monkeypatch.setitem(
        compute.PROVIDERS, "fake", lambda root: roots.append(root) or factory(root),
    )
    service = compute.Compute()
    assert service.catalog.connection_root == str(physical / "compute")
    service.discover()
    service.launch(service.plan(Request.parse("4", "8")))
    assert roots == [physical / "compute"]


def test_catalog_validation_errors_do_not_echo_provider_values(catalog: Path) -> None:
    data = yaml.safe_load(catalog.read_text())
    secret = "private-provider-credential"
    data["offers"][0]["config"] = secret
    catalog.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(main, ["compute", "resources", "--json"])
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert "offers.0.config" in error
    assert secret not in error


@pytest.mark.parametrize("document", [
    "offers: []\noffers: []\n",
    "offers: [{config: {option: 1, option: 2}}]\n",
    "offers: [{config: {1: value}}]\n",
])
def test_duplicate_or_nonstring_yaml_keys_are_rejected(catalog: Path, document: str) -> None:
    catalog.write_text(document)
    with pytest.raises(ComputeError, match="unique"):
        Catalog.load(catalog)


def test_invalid_catalog_encoding_is_a_structured_error(catalog: Path) -> None:
    catalog.write_bytes(b"offers: []\n\xff")
    with pytest.raises(ComputeError, match="cannot read compute catalog"):
        Catalog.load(catalog)
    result = CliRunner().invoke(main, ["compute", "resources", "--json"])
    assert result.exit_code == 1
    assert "cannot read compute catalog" in json.loads(result.output)["error"]


@pytest.mark.parametrize("setting", ["scratch_root", "python"])
def test_local_offer_path_types_fail_without_a_traceback(catalog: Path, setting: str) -> None:
    data = yaml.safe_load(catalog.read_text())
    data["allow_local"] = True
    for offer in data["offers"]:
        offer["provider"] = "local"
    data["offers"][0]["config"] = {setting: None}
    catalog.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(
        main, ["compute", "launch", "--cpus", "4", "--memory", "8", "--dry-run", "--json"]
    )
    assert result.exit_code == 1
    assert f"local {setting} must be a nonempty string" in json.loads(result.output)["error"]


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
    assert plan["provider"] == "fake"
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


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_launch_wait_returns_only_after_ready(
    catalog: Path, provider: MagicMock, as_json: bool,
) -> None:
    result = CliRunner().invoke(main, [
        "compute", "launch", "--cpus", "4", "--memory", "8", "--wait",
        *(["--json"] if as_json else []),
    ])
    assert result.exit_code == 0, result.output
    provider.launch.assert_called_once()
    provider.inspect.assert_called_once_with(IDENTITY)
    provider.connect.assert_called_once()
    if as_json:
        data = json.loads(result.stdout)
        assert data["accepted"] and data["ready"]
        assert data["id"] == IDENTITY.encode()
    else:
        assert result.stdout == IDENTITY.name + "\n"
        assert "is ready" in result.stderr


@pytest.mark.parametrize("phase", ["pending", "stopping", "ended"])
def test_cli_launch_wait_failure_keeps_accepted_id_and_never_resubmits(
    catalog: Path, provider: MagicMock, phase: str,
) -> None:
    provider.inspect.return_value.phase = phase
    result = CliRunner().invoke(main, [
        "compute", "launch", "--cpus", "4", "--memory", "8",
        "--wait", "--timeout", "0.01", "--json",
    ])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["id"] == IDENTITY.encode()
    assert "error" in data
    provider.launch.assert_called_once()
    provider.terminate.assert_not_called()


@pytest.mark.parametrize("flags, message", [
    (["--timeout", "1"], "requires --wait"),
    (["--wait", "--timeout", "inf"], "finite"),
    (["--wait", "--timeout", "nan"], "finite"),
    (["--wait", "--dry-run"], "cannot be combined"),
    (["--cpus", "1"], "both --cpus and --memory"),
    (["--memory", "1"], "both --cpus and --memory"),
])
def test_cli_launch_rejects_invalid_flags_before_submission(
    catalog: Path, provider: MagicMock, flags: list[str], message: str,
) -> None:
    result = CliRunner().invoke(main, ["compute", "launch", *flags, "--json"])
    assert result.exit_code == 1, result.output
    assert message in json.loads(result.stdout)["error"]
    provider.launch.assert_not_called()


def test_cli_partial_failure_and_ambiguous_submit(catalog: Path, provider: MagicMock) -> None:
    provider.discover.side_effect = ComputeError("unavailable")
    runner = CliRunner()
    result = runner.invoke(main, ["compute", "status", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["errors"] == {"fake": "unavailable"}
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
