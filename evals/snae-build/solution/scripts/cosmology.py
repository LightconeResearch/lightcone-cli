"""Shared numerical helpers for the Union2.1 flat-LCDM analysis."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.integrate import quad

C_KM_S = 299_792.458
SYSTEMATIC_FLOOR_MAG = 0.03


def load_union21(path: str | Path) -> tuple[np.ndarray, ...]:
    """Load redshift, distance modulus, statistical error, and host probability."""
    redshift, distance_modulus, statistical_error, host_probability = np.loadtxt(
        path,
        comments="#",
        usecols=(1, 2, 3, 4),
        unpack=True,
    )
    return redshift, distance_modulus, statistical_error, host_probability


def selection_mask(redshift: np.ndarray, redshift_cut: str) -> np.ndarray:
    if redshift_cut == "none":
        return np.ones(redshift.shape, dtype=bool)
    if redshift_cut == "z_min_001":
        return redshift > 0.01
    raise ValueError(f"Unknown redshift cut: {redshift_cut}")


def effective_error(statistical_error: np.ndarray, error_model: str) -> np.ndarray:
    if error_model == "stat_only":
        return statistical_error
    if error_model == "stat_and_sys":
        # The supplied table contains statistical errors and host-mass
        # probabilities, but no systematic covariance matrix. A 0.03 mag
        # irreducible floor is therefore used as a conservative approximation.
        return np.hypot(statistical_error, SYSTEMATIC_FLOOR_MAG)
    raise ValueError(f"Unknown error model: {error_model}")


def distance_modulus(redshift: np.ndarray, h0: float, omega_l: float) -> np.ndarray:
    """Distance modulus for a spatially flat matter-plus-Lambda cosmology."""
    z = np.asarray(redshift, dtype=float)
    omega_m = 1.0 - omega_l

    def inv_expansion_rate(z_value: float) -> float:
        return 1.0 / np.sqrt(omega_m * (1.0 + z_value) ** 3 + omega_l)

    comoving_integral = np.fromiter(
        (quad(inv_expansion_rate, 0.0, value, epsabs=1e-10, epsrel=1e-10)[0] for value in z),
        dtype=float,
        count=z.size,
    )
    luminosity_distance_mpc = (C_KM_S / h0) * (1.0 + z) * comoving_integral
    return 5.0 * np.log10(luminosity_distance_mpc) + 25.0


def load_fit(path: str | Path) -> dict[str, object]:
    fit_path = Path(path)
    if fit_path.is_dir():
        fit_path = fit_path / "best_fit.json"
    with fit_path.open(encoding="utf-8") as stream:
        return json.load(stream)
