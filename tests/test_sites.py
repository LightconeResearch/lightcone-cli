"""Tests for known-site defaults."""
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
    def test_perlmutter_core_fields(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        assert site["backend"] == "slurm"
        assert site["container_runtime"] == "podman-hpc"
        assert site["connection"]["hostname"] == "perlmutter.nersc.gov"

    def test_perlmutter_has_suggested_options(self):
        site = get_site_defaults("perlmutter")
        assert site is not None
        suggested = site["suggested_options"]
        assert "qos" in suggested
        assert "constraint" in suggested
        assert suggested["qos"]["default"] == "debug"
        assert suggested["constraint"]["default"] == "gpu"

    def test_perlmutter_qos_choices_are_orthogonal(self):
        """User-facing qos choices must not carry constraint prefixes."""
        site = get_site_defaults("perlmutter")
        choices = site["suggested_options"]["qos"]["choices"]
        # Plain `debug`, `regular` — no `gpu_debug`.
        assert "debug" in choices
        assert "regular" in choices
        assert not any(c.startswith("gpu_") for c in choices)

    def test_perlmutter_constraint_choices_have_guidance(self):
        site = get_site_defaults("perlmutter")
        constraints = site["suggested_options"]["constraint"]["choices"]
        assert "gpu" in constraints
        assert "cpu" in constraints
        assert "A100" in constraints["gpu"]

    def test_perlmutter_cache_key_override_for_regular_cpu(self):
        site = get_site_defaults("perlmutter")
        overrides = site["cache_key_overrides"]
        assert overrides["regular/cpu"] == "regular_1"

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
        keys = [s[0] for s in list_known_sites()]
        assert "perlmutter" in keys

    def test_local_in_known_sites(self):
        keys = [s[0] for s in list_known_sites()]
        assert "local" in keys

    def test_has_display_names(self):
        for _, display in list_known_sites():
            assert display

    def test_matches_site_defaults(self):
        assert len(list_known_sites()) == len(SITE_DEFAULTS)


class TestSiteDefaultsSchema:
    """Basic shape checks on every registered site."""

    def test_required_fields(self):
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

    def test_slurm_sites_have_suggested_options(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") != "slurm":
                continue
            assert "suggested_options" in site, \
                f"SLURM site '{key}' missing suggested_options"
            options = site["suggested_options"]
            assert "qos" in options, f"Site '{key}' missing qos option"
            qos = options["qos"]
            assert "default" in qos and "choices" in qos
            assert qos["default"] in qos["choices"]

    def test_slurm_sites_have_scratch_paths(self):
        for key, site in SITE_DEFAULTS.items():
            if site.get("backend") != "slurm":
                continue
            assert "scratch_paths" in site, \
                f"SLURM site '{key}' missing scratch_paths"
