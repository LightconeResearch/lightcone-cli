"""Tests for SLURM discovery and QoS advisor."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from lightcone.engine.slurm_info import (
    ClusterInfo,
    PartitionInfo,
    QoSInfo,
    build_option_suggestions,
    check_qos_eligibility,
    cluster_info_from_dict,
    cluster_info_to_dict,
    discover_cluster,
    generate_use_for,
    infer_qos_constraint,
    is_user_facing_qos,
    parse_max_tres,
    parse_slurm_walltime,
    query_qos,
    query_user_associations,
    recommend_qos,
    strip_constraint_prefix,
)

# ---------------------------------------------------------------------------
# Fixtures: real sacctmgr output captured from Perlmutter
# ---------------------------------------------------------------------------

SAMPLE_SACCTMGR_QOS_OUTPUT = """\
Name|Priority|GraceTime|Preempt|PreemptExemptTime|PreemptMode|Flags|UsageThres|UsageFactor|GrpTRES|GrpTRESMins|GrpTRESRunMins|GrpJobs|GrpSubmit|GrpWall|MaxTRES|MaxTRESPerNode|MaxTRESMins|MaxWall|MaxTRESPU|MaxJobsPU|MaxSubmitPU|MaxTRESPA|MaxTRESRunMinsPA|MaxTRESRunMinsPU|MaxJobsPA|MaxSubmitPA|MinTRES
normal|0|00:00:00|||cluster|||1.000000|||||||||||||||||||
gpu_debug|69119|00:01:00|debug_preempt,sparewarmer||cluster|DenyOnLimit||1.000000|||||||node=8|||00:30:00||2|5||||||
gpu_regular|67679|00:01:00|debug_preempt,sparewarmer||cluster|DenyOnLimit||1.000000||||||||||2-00:00:00|||5000||||||
gpu_shared|67679|00:00:00|debug_preempt,sparewarmer||cluster|DenyOnLimit||1.000000|||||||gres/gpu=2,node=1|||2-00:00:00|||5000||||||
gpu_preempt|67679|00:01:00|debug_preempt,overrun,sparewarmer|02:00:00|cluster|DenyOnLimit||1.000000|||||||node=128|||2-00:00:00|||5000||||||
debug|69119|00:01:00|debug_preempt,sparewarmer||cluster|DenyOnLimit||1.000000|||||||node=8|||00:30:00||2|5||||||
regular_1|67679|00:00:00|debug_preempt,sparewarmer||cluster|DenyOnLimit||1.000000||||||||||2-00:00:00|||5000||||||
preempt|67679|00:01:00|debug_preempt,overrun,sparewarmer|02:00:00|cluster|DenyOnLimit||1.000000|||||||node=128|||2-00:00:00|||5000||||||
shared|67679|00:00:00|debug_preempt,sparewarmer||cluster|DenyOnLimit||1.000000|||||||node=1|||2-00:00:00|||5000||||||
"""

SAMPLE_SACCTMGR_ASSOC_OUTPUT = """\
Account|QOS
m4031|gpu_debug,gpu_regular,gpu_shared,gpu_preempt,debug,regular_1,preempt,shared
m4031_g|gpu_debug,gpu_regular,gpu_shared,gpu_preempt,debug,regular_1,preempt,shared
"""


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------

class TestParseMaxTres:
    def test_gpu_and_node(self):
        assert parse_max_tres("gres/gpu=2,node=1") == {"gres/gpu": 2, "node": 1}

    def test_node_only(self):
        assert parse_max_tres("node=8") == {"node": 8}

    def test_empty(self):
        assert parse_max_tres("") == {}

    def test_whitespace(self):
        assert parse_max_tres("  ") == {}

    def test_complex(self):
        result = parse_max_tres("cpu=128,mem=512000M,node=4,gres/gpu=16")
        assert result["node"] == 4
        assert result["gres/gpu"] == 16
        assert result["cpu"] == 128

    def test_non_numeric_value_skipped(self):
        result = parse_max_tres("mem=512000M,node=4")
        assert result == {"node": 4}


class TestParseSlurmWalltime:
    def test_days_hours(self):
        assert parse_slurm_walltime("2-00:00:00") == 2880

    def test_hours_minutes(self):
        assert parse_slurm_walltime("00:30:00") == 30

    def test_one_hour(self):
        assert parse_slurm_walltime("01:00:00") == 60

    def test_mixed(self):
        assert parse_slurm_walltime("1-12:30:00") == 2190

    def test_empty(self):
        assert parse_slurm_walltime("") is None

    def test_none(self):
        assert parse_slurm_walltime(None) is None

    def test_with_seconds_rounds_up(self):
        assert parse_slurm_walltime("00:30:01") == 31


class TestInferQosConstraint:
    def test_gpu_prefix(self):
        assert infer_qos_constraint("gpu_debug") == "gpu"
        assert infer_qos_constraint("gpu_regular") == "gpu"
        assert infer_qos_constraint("gpu_shared") == "gpu"

    def test_no_prefix(self):
        assert infer_qos_constraint("debug") == "cpu"
        assert infer_qos_constraint("regular_1") == "cpu"
        assert infer_qos_constraint("shared") == "cpu"

    def test_preempt(self):
        assert infer_qos_constraint("gpu_preempt") == "gpu"
        assert infer_qos_constraint("preempt") == "cpu"


# ---------------------------------------------------------------------------
# Discovery tests (mocked subprocess)
# ---------------------------------------------------------------------------

class TestQueryQos:
    @patch("lightcone.engine.slurm_info._run_command")
    @patch("lightcone.engine.slurm_info.shutil.which", return_value="/usr/bin/sacctmgr")
    def test_parses_real_output(self, _which, mock_cmd):
        mock_cmd.return_value = SAMPLE_SACCTMGR_QOS_OUTPUT
        result = query_qos()

        assert "gpu_debug" in result
        assert result["gpu_debug"].max_nodes == 8
        assert result["gpu_debug"].max_wall_minutes == 30
        assert result["gpu_debug"].max_jobs_per_user == 2
        assert result["gpu_debug"].max_submit_per_user == 5
        assert result["gpu_debug"].priority == 69119

        assert "gpu_regular" in result
        assert result["gpu_regular"].max_nodes is None
        assert result["gpu_regular"].max_wall_minutes == 2880

        assert "gpu_shared" in result
        assert result["gpu_shared"].max_gpus_total == 2
        assert result["gpu_shared"].max_nodes == 1

        assert "gpu_preempt" in result
        assert result["gpu_preempt"].max_nodes == 128

    @patch("lightcone.engine.slurm_info.shutil.which", return_value=None)
    def test_no_sacctmgr(self, _which):
        assert query_qos() == {}


class TestQueryUserAssociations:
    @patch("lightcone.engine.slurm_info._run_command")
    @patch("lightcone.engine.slurm_info.shutil.which", return_value="/usr/bin/sacctmgr")
    def test_parses_real_output(self, _which, mock_cmd):
        mock_cmd.return_value = SAMPLE_SACCTMGR_ASSOC_OUTPUT
        accounts, qos_names = query_user_associations("testuser")

        assert "m4031" in accounts
        assert "m4031_g" in accounts
        assert "gpu_debug" in qos_names
        assert "gpu_regular" in qos_names
        assert "debug" in qos_names

    @patch("lightcone.engine.slurm_info.shutil.which", return_value=None)
    def test_no_sacctmgr(self, _which):
        assert query_user_associations() == ([], [])


class TestDiscoverCluster:
    @patch("lightcone.engine.slurm_info.query_partitions", return_value={})
    @patch("lightcone.engine.slurm_info.query_user_associations", return_value=([], []))
    @patch("lightcone.engine.slurm_info.query_qos", return_value={})
    def test_assembles_cluster_info(self, _qos, _assoc, _part):
        info = discover_cluster()
        assert isinstance(info, ClusterInfo)
        assert info.timestamp  # non-empty


# ---------------------------------------------------------------------------
# QoS advisor tests
# ---------------------------------------------------------------------------

class TestCheckQosEligibility:
    def test_fits(self):
        qos = QoSInfo("gpu_debug", max_wall_minutes=30, max_nodes=8)
        rec = check_qos_eligibility(qos, {
            "nodes": 4, "gpus_per_node": 4, "time_limit_minutes": 20,
        })
        assert rec.eligible
        assert rec.violations == []

    def test_exceeds_nodes(self):
        qos = QoSInfo("gpu_debug", max_wall_minutes=30, max_nodes=8)
        rec = check_qos_eligibility(qos, {
            "nodes": 16, "gpus_per_node": 4, "time_limit_minutes": 20,
        })
        assert not rec.eligible
        assert any("nodes" in v for v in rec.violations)
        assert rec.clamped_resources["nodes"] == 8

    def test_exceeds_gpus(self):
        qos = QoSInfo("gpu_shared", max_nodes=1, max_gpus_total=2)
        rec = check_qos_eligibility(qos, {
            "nodes": 1, "gpus_per_node": 4, "time_limit_minutes": 60,
        })
        assert not rec.eligible
        assert any("GPU" in v for v in rec.violations)

    def test_exceeds_walltime(self):
        qos = QoSInfo("gpu_debug", max_wall_minutes=30, max_nodes=8)
        rec = check_qos_eligibility(qos, {
            "nodes": 1, "gpus_per_node": 1, "time_limit_minutes": 120,
        })
        assert not rec.eligible
        assert any("min" in v for v in rec.violations)

    def test_no_limits(self):
        qos = QoSInfo("gpu_regular", max_wall_minutes=2880)
        rec = check_qos_eligibility(qos, {
            "nodes": 100, "gpus_per_node": 4, "time_limit_minutes": 2000,
        })
        assert rec.eligible

    def test_no_time_in_resources(self):
        qos = QoSInfo("gpu_debug", max_wall_minutes=30, max_nodes=8)
        rec = check_qos_eligibility(qos, {"nodes": 4, "gpus_per_node": 4})
        assert rec.eligible


class TestRecommendQos:
    @pytest.fixture
    def cluster(self):
        return ClusterInfo(
            qos={
                "gpu_debug": QoSInfo("gpu_debug", max_wall_minutes=30,
                                     max_nodes=8, priority=69119),
                "gpu_regular": QoSInfo("gpu_regular", max_wall_minutes=2880,
                                       priority=67679),
                "gpu_preempt": QoSInfo("gpu_preempt", max_wall_minutes=2880,
                                       max_nodes=128, priority=67679),
                "debug": QoSInfo("debug", max_wall_minutes=30,
                                 max_nodes=8, priority=69119),
                "regular_1": QoSInfo("regular_1", max_wall_minutes=2880,
                                     priority=67679),
            },
            user_qos=["gpu_debug", "gpu_regular", "gpu_preempt",
                       "debug", "regular_1"],
            user_accounts=["m4031"],
            partitions={},
            timestamp="2026-03-28T00:00:00",
        )

    def test_preferred_eligible_first(self, cluster):
        recs = recommend_qos(
            cluster,
            {"nodes": 4, "gpus_per_node": 4, "time_limit_minutes": 20},
            qos_choices=["debug", "regular", "preempt"],
            constraint="gpu",
            preferred_qos="debug",
        )
        assert recs[0].qos == "debug"
        assert recs[0].eligible

    def test_falls_back_when_preferred_ineligible(self, cluster):
        recs = recommend_qos(
            cluster,
            {"nodes": 16, "gpus_per_node": 4, "time_limit_minutes": 20},
            qos_choices=["debug", "regular", "preempt"],
            constraint="gpu",
            preferred_qos="debug",
        )
        debug_rec = next(r for r in recs if r.qos == "debug")
        assert not debug_rec.eligible
        first_eligible = next(r for r in recs if r.eligible)
        assert first_eligible.qos in ("regular", "preempt")

    def test_constraint_holds_switch_within_family(self, cluster):
        """Switch never crosses hardware families."""
        recs = recommend_qos(
            cluster,
            {"nodes": 4, "gpus_per_node": 4, "time_limit_minutes": 20},
            qos_choices=["debug", "regular"],
            constraint="gpu",
        )
        for rec in recs:
            assert rec.qos in ("debug", "regular")

    def test_cache_key_override_used(self, cluster):
        """regular/cpu resolves via the override map to regular_1."""
        recs = recommend_qos(
            cluster,
            {"nodes": 4, "gpus_per_node": 0, "time_limit_minutes": 60},
            qos_choices=["debug", "regular"],
            constraint="cpu",
            cache_key_overrides={"regular/cpu": "regular_1"},
        )
        # Both resolve to something; regular_1 is eligible, debug isn't (30m max).
        assert any(r.qos == "regular" and r.eligible for r in recs)


class TestBuildOptionSuggestions:
    def test_returns_orthogonal_options(self):
        cluster = ClusterInfo(
            qos={
                "gpu_debug": QoSInfo("gpu_debug", max_wall_minutes=30,
                                     max_nodes=8, priority=69119),
                "gpu_regular": QoSInfo("gpu_regular", max_wall_minutes=2880,
                                       priority=67679),
                "debug": QoSInfo("debug", max_wall_minutes=30,
                                 max_nodes=8, priority=69119),
            },
            user_qos=["gpu_debug", "gpu_regular", "debug"],
            user_accounts=["m4031"],
            partitions={},
            timestamp="2026-03-28T00:00:00",
        )
        options, overrides = build_option_suggestions(cluster)
        qos_choices = options["qos"]["choices"]
        assert "debug" in qos_choices
        assert "regular" in qos_choices
        assert "gpu_debug" not in qos_choices  # stripped prefix
        assert options["constraint"]["choices"]
        assert "gpu" in options["constraint"]["choices"]
        # Debug appears under both gpu and cpu via convention; no override
        # needed since `gpu_debug` follows `{constraint}_{qos}`.
        assert overrides == {}

    def test_emits_override_for_non_conventional(self):
        cluster = ClusterInfo(
            qos={
                "regular_1": QoSInfo("regular_1", max_wall_minutes=2880,
                                     priority=67679),
            },
            user_qos=["regular_1"],
            user_accounts=["m4031"],
            partitions={},
            timestamp="2026-03-28T00:00:00",
        )
        options, overrides = build_option_suggestions(cluster)
        # "regular_1" gets recorded as-is (infer_qos_constraint → cpu; no prefix).
        assert "regular_1" in options["qos"]["choices"]


class TestStripConstraintPrefix:
    def test_strips_matching_prefix(self):
        assert strip_constraint_prefix("gpu_debug", "gpu") == "debug"

    def test_no_prefix(self):
        assert strip_constraint_prefix("debug", "gpu") == "debug"

    def test_empty_constraint(self):
        # Empty constraint -> prefix is "_" which rarely matches.
        assert strip_constraint_prefix("debug", "") == "debug"


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_roundtrip(self):
        original = ClusterInfo(
            qos={
                "gpu_debug": QoSInfo("gpu_debug", max_wall_minutes=30,
                                     max_nodes=8, priority=69119),
            },
            user_qos=["gpu_debug"],
            user_accounts=["m4031"],
            partitions={
                "gpu_ss11": PartitionInfo("gpu_ss11", total_nodes=1664,
                                          has_gpus=True, gpu_model="a100"),
            },
            timestamp="2026-03-28T00:00:00",
        )
        d = cluster_info_to_dict(original)
        restored = cluster_info_from_dict(d)

        assert restored.timestamp == original.timestamp
        assert restored.user_accounts == original.user_accounts
        assert restored.user_qos == original.user_qos
        assert restored.qos["gpu_debug"].max_nodes == 8
        assert restored.partitions["gpu_ss11"].has_gpus is True
        assert restored.partitions["gpu_ss11"].gpu_model == "a100"


# ---------------------------------------------------------------------------
# generate_use_for tests
# ---------------------------------------------------------------------------

class TestGenerateUseFor:
    def test_debug_qos(self):
        qos = QoSInfo("gpu_debug", max_wall_minutes=30, max_nodes=8, priority=69119)
        result = generate_use_for(qos)
        assert "quick tests" in result
        assert "8 nodes" in result
        assert "30 min" in result

    def test_regular_qos(self):
        qos = QoSInfo("gpu_regular", max_wall_minutes=2880, priority=67679)
        result = generate_use_for(qos)
        assert "long-running" in result

    def test_preempt_qos(self):
        qos = QoSInfo("gpu_preempt", max_wall_minutes=2880, max_nodes=128, priority=67679)
        result = generate_use_for(qos)
        assert "preempt" in result.lower()

    def test_shared_qos(self):
        qos = QoSInfo("gpu_shared", max_wall_minutes=2880, max_nodes=1,
                       max_gpus_total=2, priority=67679)
        result = generate_use_for(qos)
        assert "2 GPUs" in result

    def test_no_limits(self):
        qos = QoSInfo("custom")
        assert generate_use_for(qos) == "general purpose"


# ---------------------------------------------------------------------------
# is_user_facing_qos tests
# ---------------------------------------------------------------------------

class TestIsUserFacingQos:
    def test_standard_qos(self):
        assert is_user_facing_qos("gpu_debug")
        assert is_user_facing_qos("gpu_regular")
        assert is_user_facing_qos("debug")

    def test_internal_qos(self):
        assert not is_user_facing_qos("normal")
        assert not is_user_facing_qos("cron")
        assert not is_user_facing_qos("xfer")
        assert not is_user_facing_qos("batchdisable")

    def test_special_qos(self):
        assert not is_user_facing_qos("gpu_special_m1759")
        assert not is_user_facing_qos("gpu_special_nstaff")

    def test_interactive_hidden_by_default(self):
        assert not is_user_facing_qos("gpu_interactive")
        assert not is_user_facing_qos("gpu_shared_interactive")
        assert not is_user_facing_qos("gpu_jupyter")
        assert not is_user_facing_qos("gpu_shared_jupyter")

    def test_interactive_shown_with_flag(self):
        assert is_user_facing_qos("gpu_interactive", include_interactive=True)
        assert is_user_facing_qos("gpu_jupyter", include_interactive=True)
