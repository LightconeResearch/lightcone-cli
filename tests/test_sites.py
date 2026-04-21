"""Tests for HPC site defaults."""
from __future__ import annotations

from lightcone.engine.site_registry import (
    SITE_DEFAULTS,
    detect_site,
    get_site_defaults,
    list_known_sites,
)


class TestDetectSite:
    def test_exact_match(self):
        assert detect_site("perlmutter") == "perlmutter"

    def test_hostname_match(self):
        assert detect_site("perlmutter.nersc.gov") == "perlmutter"

    def test_partial_match(self):
        assert detect_site("my-perlmutter-target") == "perlmutter"

    def test_case_insensitive(self):
        assert detect_site("Perlmutter") == "perlmutter"
        assert detect_site("PERLMUTTER") == "perlmutter"

    def test_saul_matches_perlmutter(self):
        assert detect_site("saul.nersc.gov") == "perlmutter"

    def test_unknown(self):
        assert detect_site("my-cluster") is None

    def test_empty(self):
        assert detect_site("") is None


class TestGetSiteDefaults:
    def test_perlmutter(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        assert site["backend"] == "slurm"
        assert site["scheduler"]["container_runtime"] == "podman-hpc"
        assert "gpu" in site["node_types"]
        assert site["connection"]["hostname"] == "perlmutter.nersc.gov"

    def test_perlmutter_gpu_node_type(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        gpu = site["node_types"]["gpu"]
        assert gpu["constraint"] == "gpu"
        assert "--gpu" in gpu["container_flags"]
        assert "description" in gpu

    def test_perlmutter_gpu_hbm80_node_type(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        gpu80 = site["node_types"]["gpu_hbm80"]
        assert gpu80["constraint"] == "gpu&hbm80g"
        assert "--gpu" in gpu80["container_flags"]

    def test_perlmutter_cpu_node_type(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        cpu = site["node_types"]["cpu"]
        assert cpu["constraint"] == "cpu"
        assert cpu["container_flags"] == []

    def test_perlmutter_qos_options(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        qos = site["qos_options"]
        assert "regular" in qos
        assert "debug" in qos
        assert qos["regular"].get("default") is True

    def test_perlmutter_container_runtimes(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        assert "podman-hpc" in site["container_runtimes"]
        assert "shifter" not in site["container_runtimes"]

    def test_local_site_exists(self):
        defaults = get_site_defaults("local")
        assert defaults is not None
        assert defaults["backend"] == "local"

    def test_local_site_display_name(self):
        defaults = get_site_defaults("local")
        assert defaults["display_name"] == "Local"

    def test_unknown(self):
        assert get_site_defaults("nonexistent") is None


class TestListKnownSites:
    def test_returns_all_sites(self):
        sites = list_known_sites()
        keys = [s[0] for s in sites]
        assert "perlmutter" in keys

    def test_local_in_known_sites(self):
        sites = list_known_sites()
        keys = [s[0] for s in sites]
        assert "local" in keys

    def test_has_display_names(self):
        sites = list_known_sites()
        for key, display in sites:
            assert len(display) > 0

    def test_matches_site_defaults(self):
        sites = list_known_sites()
        assert len(sites) == len(SITE_DEFAULTS)


class TestSiteDefaultsSchema:
    """Ensure all site entries have the required fields."""

    def test_all_sites_have_required_fields(self):
        required = {"hostname_patterns", "backend", "connection", "scheduler",
                     "node_types", "qos_options", "container_runtimes",
                     "resource_limits"}
        for key, site in SITE_DEFAULTS.items():
            missing = required - set(site.keys())
            assert not missing, f"Site '{key}' missing fields: {missing}"

    def test_all_sites_have_container_runtime(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") == "local":
                continue
            assert "container_runtime" in site["scheduler"], \
                f"Site '{key}' missing scheduler.container_runtime"

    def test_all_node_types_have_required_fields(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") == "local":
                continue
            for nname, ninfo in site["node_types"].items():
                assert "constraint" in ninfo, \
                    f"Site '{key}' node_type '{nname}' missing constraint"
                assert "container_flags" in ninfo, \
                    f"Site '{key}' node_type '{nname}' missing container_flags"
                assert "description" in ninfo, \
                    f"Site '{key}' node_type '{nname}' missing description"

    def test_all_qos_options_have_description(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") == "local":
                continue
            for qname, qinfo in site["qos_options"].items():
                assert "description" in qinfo, \
                    f"Site '{key}' qos_option '{qname}' missing description"

    def test_exactly_one_default_qos(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") == "local":
                continue
            defaults = [q for q, info in site["qos_options"].items()
                        if info.get("default")]
            assert len(defaults) == 1, \
                f"Site '{key}' has {len(defaults)} default QOS options (expected 1)"
