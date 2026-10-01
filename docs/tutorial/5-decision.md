# 5. Add a decision

Find out why our error bars are three times tighter than the paper's, and
record the answer as a decision.

!!! abstract "Planned changes"
    - Re-run the prompt with the `lightcone` plugin and capture the real decision
      and universe files it writes.
    - Add the `lc` beats, with real output: the decision changes the fit's recipe,
      so `lc status` shows the chapter 3 fit `stale`; `lc materialize` makes both
      universes; the table below is read from `results/`.
    - Settle which option the `baseline` universe takes (statistical only, as
      before, or statistical + systematic) and what the second universe is called.

Ours are three times tighter — 0.722 ± 0.013 against their 0.705 ± 0.04, on the
same 580 supernovae. Find out why:

```text
Let's understand why our error bars are so much tighter than theirs. Download
the two Union2.1 covariance files:

  https://supernova.lbl.gov/Union/figures/SCPUnion2.1_covmat_nosys.txt
  https://supernova.lbl.gov/Union/figures/SCPUnion2.1_covmat_sys.txt

Work out which one the main data file's error column is using, then add a
decision to astra.yaml to switch to the other, and see what impact it has on
our constraint.
```

The error column matches the no-systematics file: our fit has been statistical
only, and everything the systematic covariance knows was sitting in a file we
had not opened.

## One decision, two universes

A decision is a methodological choice with its options named and its reason
written down. Each universe is one choice of option for every decision, and
`lc` makes every output once per universe, under `results/<universe>/`. See
[decisions and universes](../concepts/universes.md).

!!! note "To capture"
    The decision as the agent writes it in `astra.yaml`, and the universe files.

## What changed

The decision reaches the fit's recipe, so the fit made in
[chapter 3](3-reproduce.md) no longer matches the analysis file. `lc status`
says so:

```bash
lc status
```

!!! note "To capture"
    The real `lc status` output, with the reason `lc` gives for what is stale.

Commit, and materialize both universes:

```text
Commit the decision, then materialize every universe and compare the fits.
```

!!! note "To capture"
    The real `lc materialize` output, and the comparison read from `results/`.

## The comparison

Switching to the systematic covariance gives:

| | Ω_Λ |
|---|---|
| ours, statistical | 0.722 ± 0.013 |
| ours, statistical + systematic | **0.714 ± 0.030** |
| Suzuki et al., published | 0.705 (+0.040, −0.043) |

The value barely moves. The uncertainty more than doubles — and only then is it
comparable with the paper's, which includes systematics too. Our second row
agrees with theirs to 0.2σ: their data, their systematics, their method.

A comparison of best-fit numbers would have ranked this decision as nearly
irrelevant. It is the difference between a number that resembles theirs and a
number you can put beside theirs.

## You now have

- Two universes, statistical and statistical + systematic, each with its own
  materialized fit and Hubble diagram.
- A number you can put beside the paper's, and the decision that made it so,
  written down.
