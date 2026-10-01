# 2. Scope the analysis

Before any code: say what the analysis is, and let the agent write it down.

!!! abstract "Planned changes"
    - Re-run the prompt with the `lightcone` plugin and capture the real
      `astra.yaml`. The `lightcone` skill scopes by interview (research question,
      structure, an optional literature pass, finalize), so it may ask questions
      the `astra`-only run did not.
    - Give both outputs a `format:`, from that re-run rather than by hand: `lc`
      refuses an output without one, because the format names its file. `format:`
      arrived in spec version 0.0.14, so check it validates under the `version:` below.
    - The skill's finalize step also writes `AGENTS.md` and a `baseline` universe,
      and repoints the scaffolded `index.md`. Decide how much of that to show here.

Say what the analysis is:

```text
Use the lightcone skill to set up an astra.yaml for this. I want to fit a flat
LCDM model to this data to recover Omega_Lambda, varying only Omega_Lambda
and using the metadata in the file's header.

Two outputs: the best fit, and a downstream best-fit Hubble diagram figure
with an absolute panel and a residuals panel.

In this first pass, let's only use inputs, outputs, and recipes. Please walk
me through each element that you add.
```

## Your analysis file

The agent writes the analysis down in `astra.yaml`: your
[analysis file](../concepts/analysis-file.md), and the one place the analysis
is described. Everything that follows reads from it.

When the agent writes `astra.yaml`, a hook fires and validates it immediately.
You should see something like this:

```yaml
version: "0.0.12"
name: "Cosmic expansion from Type Ia supernovae"

inputs:
  - id: union21
    type: data
    source: data/SCPUnion2.1_mu_vs_z.txt
    description: >
      Union2.1 SN Ia compilation. 580 rows spanning z = 0.015 to 1.414. The
      header gives the assumed absolute magnitude, M(h=0.7), so the distance
      moduli carry an h = 0.7 calibration.

outputs:
  - id: best_fit
    type: metric
    description: >
      Best-fit Omega_Lambda with its uncertainty, chi-squared, dof. H0 is held
      at 70 km/s/Mpc to match the calibration in the data file's header, not
      fitted.
    inputs: [union21]
    recipe:
      command: python src/fit.py --data {inputs.union21} --out {output}

  - id: hubble_diagram
    type: figure
    description: >
      Two panels sharing a redshift axis: distance modulus with the best-fit
      curve, and residuals below it.
    inputs: [union21, best_fit]
    recipe:
      command: >
        python src/plot_hubble.py --data {inputs.union21} --fit
        {inputs.best_fit} --out {output}
```

Each output names what it depends on and a recipe: the command that makes it.
`{inputs.union21}` and `{output}` are placeholders, filled in when the recipe
runs. `hubble_diagram` takes `best_fit` as an input, so the figure is always
drawn from the fit it sits beside.

## You now have

- An `astra.yaml` declaring the data, two outputs, and the recipe that makes each.
- No code yet. The scripts the recipes call come in the next chapter.
