# 4. Check it against the paper

Put the paper's own number in the analysis file, with a quote checked against
the PDF.

!!! abstract "Planned changes"
    - Re-run the prompt with the `lightcone` plugin and capture the real output.
    - Check the command name: this chapter says `astra paper verify-quote`, the
      pinned `astra` skill lists `astra paper verify-quotes`.
    - Add an `lc` beat: commit the spec change, and confirm with `lc status` that
      a prior insight leaves both outputs `current` (it changes no output's
      recipe or decisions).
    - Decide whether the paper belongs here or in scoping: the `lightcone` skill
      offers its literature pass while scoping, before any result exists.

Is our result consistent with that found in the original paper? We check it by
quoting them — and the quote is verified against the paper itself.

```text
The paper that published this catalogue is Suzuki et al. 2012, arXiv
1105.3470. Cache the paper, verify their own Omega_Lambda from these
supernovae, and show me the output. Then record it in astra.yaml as a prior
insight with the quote as evidence, and tell me how our constraint compares to
theirs.
```

The text is searched in the cached PDF; running `astra paper verify-quote`
gives:

```text
✓ Verified   Quote verified on page(s) [17]
```

```yaml
prior_insights:
  suzuki_2012_result:
    label: "Union2.1's own dark-energy constraint"
    claim: >
      Suzuki et al. (2012) constrain the dark energy density from these
      supernovae alone, in a flat universe, to Omega_Lambda = 0.705
      (+0.040, -0.043) including systematic errors.
    created_at: "2026-07-27T00:00:00Z"
    tags: [physics, priors]
    evidence:
      - id: suzuki_2012_sne_alone_flat_lcdm
        doi: "10.48550/arXiv.1105.3470"
        quote:
          exact: "In a flat Universe, SNe Ia alone constrain the dark-energy density, ΩΛ, to be ΩΛ = 0.705+0.040−0.043 including systematics"
        location:
          value: "page=17"
```

A prior insight is what the literature already says, written into the
analysis file with the evidence for it. See [evidence](../concepts/evidence.md).

If you want to run the check yourself:

```bash
uvx astra-tools validate astra.yaml --verify-evidence
```

```text
✓ Schema validation passed
✓ Semantic validation passed

Verifying evidence...
✓ Evidence (prior_insights): 1/1 verified
```

Our central value agrees with theirs, but look at the error bars!

## You now have

- The paper's own Ω_Λ in `astra.yaml` as a prior insight, its quote verified
  against the PDF.
- A central value that agrees with theirs, and error bars that do not.
