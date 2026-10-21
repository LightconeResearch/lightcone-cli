#!/usr/bin/env python3
"""Plot Union2.1 distance-modulus residuals around the fitted model."""

from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from cosmology import distance_modulus, effective_error, load_fit, load_union21, selection_mask


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--fit", required=True, help="the best_fit output file (json)")
    parser.add_argument("--output", required=True, help="the output file, results/<universe>/residuals.png")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fit = load_fit(args.fit)
    redshift, observed_mu, statistical_error, _ = load_union21(args.data)
    keep = selection_mask(redshift, str(fit["redshift_cut"]))
    sigma_mu = effective_error(statistical_error, str(fit["error_model"]))
    model_mu = distance_modulus(
        redshift[keep],
        float(fit["H0_km_s_Mpc"]),
        float(fit["Omega_L"]),
    )
    residual = observed_mu[keep] - model_mu

    figure, axis = plt.subplots(figsize=(8.2, 4.6), constrained_layout=True)
    axis.errorbar(
        redshift[keep],
        residual,
        yerr=sigma_mu[keep],
        fmt=".",
        color="#4daf4a",
        alpha=0.5,
        markersize=3,
        elinewidth=0.5,
    )
    axis.axhline(0.0, color="#222222", linewidth=1.3)
    axis.set_xlabel("Redshift z")
    axis.set_ylabel("Observed − model μ")
    axis.set_title("Union2.1 flat-ΛCDM residuals")
    axis.grid(alpha=0.2)

    figure.savefig(args.output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    main()
