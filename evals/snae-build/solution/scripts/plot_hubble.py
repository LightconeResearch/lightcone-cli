#!/usr/bin/env python3
"""Plot the Union2.1 Hubble diagram and the fitted flat-LCDM relation."""

from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from cosmology import distance_modulus, effective_error, load_fit, load_union21, selection_mask


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--fit", required=True, help="the best_fit output file (json)")
    parser.add_argument("--output", required=True, help="the output file, results/<universe>/hubble_diagram.png")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fit = load_fit(args.fit)
    redshift, observed_mu, statistical_error, _ = load_union21(args.data)
    keep = selection_mask(redshift, str(fit["redshift_cut"]))
    sigma_mu = effective_error(statistical_error, str(fit["error_model"]))

    model_redshift = np.linspace(float(redshift[keep].min()), float(redshift[keep].max()), 800)
    model_mu = distance_modulus(
        model_redshift,
        float(fit["H0_km_s_Mpc"]),
        float(fit["Omega_L"]),
    )

    figure, axis = plt.subplots(figsize=(8.2, 5.4), constrained_layout=True)
    axis.errorbar(
        redshift[keep],
        observed_mu[keep],
        yerr=sigma_mu[keep],
        fmt=".",
        color="#377eb8",
        alpha=0.45,
        markersize=3,
        elinewidth=0.5,
        label=f"Union2.1 ({keep.sum()} SNe Ia)",
    )
    axis.plot(
        model_redshift,
        model_mu,
        color="#e41a1c",
        linewidth=2,
        label=(
            rf"Flat $\Lambda$CDM: $H_0={float(fit['H0_km_s_Mpc']):.2f}$ "
            r"km s$^{-1}$ Mpc$^{-1}$, "
            rf"$\Omega_\Lambda={float(fit['Omega_L']):.3f}$"
        ),
    )
    axis.set_xlabel("Redshift z")
    axis.set_ylabel("Distance modulus μ")
    axis.set_title("Union2.1 Hubble diagram")
    axis.grid(alpha=0.2)
    axis.legend(frameon=False)

    figure.savefig(args.output, dpi=180)
    plt.close(figure)


if __name__ == "__main__":
    main()
