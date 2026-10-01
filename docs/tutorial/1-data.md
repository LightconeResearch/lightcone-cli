# 1. The data

A classic cosmology problem, a public catalogue, and a directory to work in.

!!! abstract "Planned changes"
    - Re-run the prompt with the `lightcone` plugin installed (the source ran with
      `astra` only).
    - Decide whether `lc init` runs here, before the download, rather than in
      [chapter 3](3-reproduce.md): the `lightcone` skill's scoping starts from an
      `lc init` scaffold.

We work through a classic cosmology problem: using the relation between the
brightness and redshifts of supernovae to estimate the dark energy content of
the universe. We will fit the standard ΛCDM model of cosmology to 580 Type Ia
supernovae from [Suzuki et al. 2012](https://arxiv.org/abs/1105.3470).
Mechanically, this is a straightforward least-squares fit with a single free
parameter, Ω_Λ.

## Start your agent

Make a directory to work in:

```bash
mkdir sn-cosmology && cd sn-cosmology
```

Then start your agent in it, with the `lightcone` plugin installed (see
[install](../reference/installation.md)).

## Get the data

```text
Download the Union2.1 supernova compilation from the Supernova Cosmology
Project into a data/ directory:

  https://supernova.lbl.gov/Union/figures/SCPUnion2.1_mu_vs_z.txt

Then show me the first few rows and tell me what's in the file.
```

## You now have

- A working directory, `sn-cosmology`, with the Union2.1 compilation in `data/`.
- An agent that has read the file and can tell you what is in it.
