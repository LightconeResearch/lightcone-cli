"""Common resource selection, native dispatch, and CLI contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import yaml
from click.testing import CliRunner

from lightcone.cli.commands import main
from lightcone.engine import compute
from lightcone.engine.compute.catalog import Catalog
from lightcone.engine.compute.model import (
    GIB,
    ComputeError,
    Identity,
    LaunchPlan,
    Request,
    Snapshot,
    UnavailableOfferError,
    memory_bytes,
    validate_name,
)

NAMESPACE = "5a9d058c-7c6e-4e2a-919b-786f1148536c"
IDENTITY = Identity(NAMESPACE, "1234", "abc")


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
        compute.Compute().catalog.connections["test"],
        offer,
        request,
        request.seconds or offer.default_seconds,
    )
    adapter.launch.return_value = IDENTITY
    adapter.inspect.return_value = Snapshot(IDENTITY, "active", num_nodes=1)
    adapter.discover.return_value = [Snapshot(IDENTITY, "pending")]
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


@pytest.mark.parametrize("name", ["analysis", "a", "run-2", "lc-7f3a92c810bd", "a" * 63])
def test_cluster_names_and_named_identities(name: str) -> None:
    validate_name(name)
    compute.validate_id(name)
    identity = replace(IDENTITY, name=name)
    assert Identity.decode(identity.encode()) == identity
    assert Snapshot(identity, "pending").as_dict()["name"] == name


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
    provider.discover.return_value = [Snapshot(IDENTITY, phase)]
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
        Snapshot(replace(IDENTITY, name=f"lc-{values[0].hex[:12]}"), "pending")
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


@pytest.mark.parametrize("observations", [[], [Snapshot(IDENTITY, "ended")]])
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
    other = replace(IDENTITY, native_id="5678", token="different")
    provider.discover.return_value = [Snapshot(IDENTITY, "active"), Snapshot(other, "pending")]
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
        [Snapshot(IDENTITY, "active")], {"other": "native service unavailable"}
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
    assert (offer.resources.cpus, offer.resources.memory, offer.max_nodes) == (1, GIB, 1)
    assert (offer.default_seconds, offer.max_seconds, offer.startup) == (1800, 7200, "fast")
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
    assert Catalog.load().offers == ()
    monkeypatch.setenv("LC_COMPUTE_CONFIG", str(catalog))
    assert Catalog.load() == configured
    assert Catalog.load(default).offers == ()
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
        {"cpus": 0}, {"cpus": True}, {"memory": -1}, {"num_nodes": 0},
        {"num_nodes": 1.5}, {"seconds": 0}, {"seconds": float("inf")},
        {"min_cpus": "yes"}, {"startup": "batch"},
    ],
)
def test_programmatic_requests_validate_the_same_resource_contract(override: dict) -> None:
    with pytest.raises(ComputeError):
        Request(**{"cpus": 1, "memory": GIB, **override})


def test_memory_conversion_does_not_round_fractional_bytes() -> None:
    assert memory_bytes("0.000000000931322574615478515625") == 1
    with pytest.raises(ComputeError, match="exactly representable"):
        memory_bytes("0.000000000931322574615478515625000000000000000001")


def test_exact_minimum_selection_and_limits(catalog: Path, provider: MagicMock) -> None:
    service = compute.Compute()
    assert service.plan(Request.parse("4", "8")).offer.name == "quick"
    assert service.plan(Request.parse("4+", "8+", num_nodes=2)).offer.name == "large"
    assert service.plan(Request.parse("5+", "9+")).resources.memory == 32 * GIB
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
        service.catalog.connections["test"], service.catalog.offers[1],
        Request.parse("1+", "1+"), 1800,
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


@pytest.mark.parametrize("mutation", ["namespace", "context", "offer", "limits", "unknown"])
def test_invalid_catalog_is_rejected(catalog: Path, mutation: str) -> None:
    data = yaml.safe_load(catalog.read_text())
    if mutation == "namespace":
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
    else:
        data["offers"][0]["gpus"] = 1
    catalog.write_text(yaml.safe_dump(data))
    with pytest.raises(ComputeError):
        Catalog.load(catalog)


def test_duplicate_yaml_keys_are_rejected(catalog: Path) -> None:
    catalog.write_text("version: 1\nversion: 1\nconnections: {}\noffers: []\n")
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
    assert [item["name"] for item in json.loads(result.output)["offers"]] == ["quick", "large"]
    args = ["compute", "launch", "--cpus", "4", "--memory", "8", "--json"]
    result = runner.invoke(main, [*args, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["plan"]["offer"] == "quick"
    provider.launch.assert_not_called()
    result = runner.invoke(main, args)
    assert json.loads(result.output)["id"] == IDENTITY.encode()
    result = runner.invoke(main, ["compute", "down", IDENTITY.encode(), "--json"])
    assert result.exit_code == 0, result.output
    provider.terminate.assert_called_once_with(IDENTITY)


def test_cli_launch_name_output_can_be_captured_without_json(
    catalog: Path, provider: MagicMock,
) -> None:
    identity = replace(IDENTITY, name="analysis")
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
    provider.discover.return_value = [Snapshot(identity, "pending")]
    result = runner.invoke(main, ["compute", "status"])
    assert result.exit_code == 0, result.output
    assert "analysis" in result.stdout
    assert "clu_" not in result.stdout
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
