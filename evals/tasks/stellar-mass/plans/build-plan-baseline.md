# Build Plan: Stellar Mass Function (Baseline)

## Outputs to build

1. **stellar_mass_function** — Write `scripts/compute_smf.py` to compute the stellar mass function
2. **model_fit** — Write `scripts/fit_model.py` to fit a model to the SMF output

## Steps

1. Read `astra.yaml` to understand outputs and dependencies
2. Write `scripts/compute_smf.py` with the Kroupa IMF
3. Run `prism run --universe baseline` to materialize `stellar_mass_function`
4. Write `scripts/fit_model.py` using the SMF output
5. Run `prism run --universe baseline` to materialize `model_fit`
6. Validate with `astra validate astra.yaml` and `prism status`
