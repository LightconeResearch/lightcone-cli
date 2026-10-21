#!/usr/bin/env python3
"""Fit Union2.1 distance moduli with a two-parameter flat-LCDM model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from cosmology import (
    SYSTEMATIC_FLOOR_MAG,
    distance_modulus,
    effective_error,
    load_union21,
    selection_mask,
)
from scipy.optimize import minimize


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True, help="the output file, results/<universe>/best_fit.json")
    parser.add_argument("--optimizer", choices=("nelder_mead", "l_bfgs_b"), required=True)
    parser.add_argument("--error-model", choices=("stat_only", "stat_and_sys"), required=True)
    parser.add_argument("--redshift-cut", choices=("none", "z_min_001"), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    redshift, observed_mu, statistical_error, _ = load_union21(args.data)
    keep = selection_mask(redshift, args.redshift_cut)
    redshift = redshift[keep]
    observed_mu = observed_mu[keep]
    sigma_mu = effective_error(statistical_error[keep], args.error_model)

    def chi_squared(parameters: np.ndarray) -> float:
        h0, omega_l = parameters
        if not 40.0 <= h0 <= 100.0 or not 0.001 <= omega_l <= 0.999:
            return float("inf")
        model_mu = distance_modulus(redshift, h0, omega_l)
        return float(np.sum(np.square((observed_mu - model_mu) / sigma_mu)))

    method = "Nelder-Mead" if args.optimizer == "nelder_mead" else "L-BFGS-B"
    options: dict[str, float | int]
    if method == "Nelder-Mead":
        options = {"maxiter": 2000, "xatol": 1e-9, "fatol": 1e-7}
    else:
        options = {"maxiter": 2000, "ftol": 1e-12}

    result = minimize(
        chi_squared,
        x0=np.array([70.0, 0.7]),
        method=method,
        bounds=((40.0, 100.0), (0.001, 0.999)),
        options=options,
    )
    if not result.success:
        raise RuntimeError(f"Optimization failed: {result.message}")

    h0, omega_l = (float(value) for value in result.x)
    chi2 = float(result.fun)
    degrees_of_freedom = int(redshift.size - 2)
    summary = {
        "H0_km_s_Mpc": h0,
        "Omega_L": omega_l,
        "Omega_m": 1.0 - omega_l,
        "chi_squared": chi2,
        "degrees_of_freedom": degrees_of_freedom,
        "reduced_chi_squared": chi2 / degrees_of_freedom,
        "n_supernovae": int(redshift.size),
        "optimizer": args.optimizer,
        "error_model": args.error_model,
        "redshift_cut": args.redshift_cut,
        "systematic_floor_mag": SYSTEMATIC_FLOOR_MAG if args.error_model == "stat_and_sys" else 0.0,
        "optimization_evaluations": int(result.nfev),
        "optimization_message": str(result.message),
    }

    # An output is exactly one file — the path the engine passed as {output}.
    output = Path(args.output)
    with output.open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
