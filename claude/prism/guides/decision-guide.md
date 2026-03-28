# Decision Guide

## What Is a Decision?

A decision is a methodological choice where a different defensible option could plausibly produce a different numerical result. Include it if changing the choice could shift a quantitative outcome — even modestly. Many small decisions can compound.

**Not decisions — skip these:**

- **Tooling choices** that produce identical numerical results: programming language, library/framework (PyTorch vs TensorFlow), file format, parallelization strategy, plotting style.
- **Fixed constraints** with no degrees of freedom: "use the data that exists," "satisfy the grant requirements."
- **What to produce** — decisions control *how* something is computed, not *what* outputs exist. Outputs are fixed by the analysis structure.

**These ARE decisions — do not skip:**

- Algorithmic choices (MCMC vs optimization, KDE vs histogram, smoothing method)
- Numerical parameters and thresholds (sigma clipping level, bin width, convergence criterion, iteration count)
- Statistical method choices (bootstrap vs analytic errors, Bayesian vs frequentist)
- Data selection criteria (quality cuts, magnitude limits, spatial boundaries)
- Correction and calibration choices (which reddening law, which zero-point, which prior)

When in doubt, include it. A multiverse with a few extra decisions is more informative than one that silently bakes in unjustified choices.

---

## Decision Prioritization

Evaluate every candidate decision before adding it to the spec.

**Flowchart:**
1. Does domain knowledge clearly favor one option? --> Prefer that option as `default`, but still include alternatives if a different choice could shift results.
2. Are the options expected to give similar results? --> Include as a robustness check — even small effects compound across decisions.
3. Neither clear? --> Include alternatives and record why uncertainty remains in `rationale`.

**Rationale guidance:** Document why options are included and what supports them, e.g.: `"Literature uses both 2.5 and 3 SD cutoffs with no consensus"`.

---

## Constraint Patterns

Use constraints when decisions are not independent.

- **Conditional existence** (`when` on decision) -- downstream decision only exists given an upstream choice. E.g., `svm_kernel` only exists `when: model.svm`.
- **Incompatibility** (`incompatible_with` on option) -- two options cannot coexist in a universe.
- **Requirement** (`requires` on option) -- selecting one option forces another.
