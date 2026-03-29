"""Tests for HPC site defaults."""
from __future__ import annotations

from prism.dagster.site_registry import (
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
        assert site["container_runtime"] == "podman-hpc"
        assert site["connection"]["hostname"] == "perlmutter.nersc.gov"

    def test_perlmutter_constraint_guidance(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        guidance = site["constraint_guidance"]
        assert "gpu" in guidance
        assert "cpu" in guidance
        assert "gpu&hbm80g" in guidance
        assert "A100" in guidance["gpu"]

    def test_perlmutter_suggested_qos(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        suggested = site["suggested_qos"]
        names = [s["name"] for s in suggested]
        assert "gpu_debug" in names
        assert "gpu_regular" in names
        # Each entry has constraint and use_for
        for entry in suggested:
            assert "constraint" in entry
            assert "use_for" in entry

    def test_perlmutter_has_gpu_and_cpu_qos(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        constraints = {s["constraint"] for s in site["suggested_qos"]}
        assert "gpu" in constraints
        assert "cpu" in constraints

    def test_perlmutter_safe_defaults(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        defaults = site["safe_defaults"]
        assert defaults["qos"] == "gpu_debug"
        assert defaults["constraint"] == "gpu"

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
        required = {"hostname_patterns", "backend", "connection", "display_name"}
        for key, site in SITE_DEFAULTS.items():
            missing = required - set(site.keys())
            assert not missing, f"Site '{key}' missing fields: {missing}"

    def test_slurm_sites_have_container_runtime(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") != "slurm":
                continue
            assert "container_runtime" in site, \
                f"SLURM site '{key}' missing container_runtime"

    def test_slurm_sites_have_suggested_qos(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") != "slurm":
                continue
            assert "suggested_qos" in site, \
                f"SLURM site '{key}' missing suggested_qos"
            for entry in site["suggested_qos"]:
                assert "name" in entry, \
                    f"Site '{key}' suggested_qos entry missing 'name'"
                assert "constraint" in entry, \
                    f"Site '{key}' suggested_qos entry missing 'constraint'"
                assert "use_for" in entry, \
                    f"Site '{key}' suggested_qos entry missing 'use_for'"

    def test_slurm_sites_have_constraint_guidance(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") != "slurm":
                continue
            assert "constraint_guidance" in site, \
                f"SLURM site '{key}' missing constraint_guidance"

    def test_slurm_sites_have_safe_defaults(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") != "slurm":
                continue
            assert "safe_defaults" in site, \
                f"SLURM site '{key}' missing safe_defaults"
            defaults = site["safe_defaults"]
            assert "qos" in defaults
            assert "constraint" in defaults

    def test_slurm_sites_have_account_suffixes(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") != "slurm":
                continue
            assert "account_suffixes" in site, \
                f"SLURM site '{key}' missing account_suffixes"
