# HPC & SLURM

lightcone-cli executes ASTRA recipes on SLURM clusters via **persistent
pilots**: a single `sbatch` allocation hosts a Dask scheduler and pool
of workers that all subsequent `lc run` invocations dispatch to. One
queue wait per session — not per recipe.

## Supported sites

| Site | Key | Container runtime |
|------|-----|------------------|
| NERSC Perlmutter | `perlmutter` | `podman-hpc` |
| Other SLURM clusters | (add to `site_registry.SITE_DEFAULTS`) | `singularity`, `docker`, `podman` |

A SLURM cluster is in scope if it offers (a) a shared filesystem visible
to login + compute nodes, (b) routable TCP from orchestrator host to
compute nodes, (c) a container runtime on compute nodes, and
(d) standard SLURM CLI tools.

## Quick start

```bash
lc pilot add perlmutter         # interactive: site detect → site defaults → $EDITOR
lc pilot start perlmutter       # one queue wait, ~minutes
lc run                          # ~1s round-trip, no queueing
lc run                          # again — instant
lc pilot stop perlmutter        # done
```

For an in-depth walkthrough of pilot configuration and lifecycle, see
[Pilots](pilots.md).

## Interactive iteration without a pilot

When a project has no pilot configured (or you pass `--local`),
`lc run` executes recipes locally with the auto-detected container
runtime. This is appropriate when:

- You're already inside an `salloc`'d compute shell — recipes execute on
  the node you're sitting on without going through the queue.
- You're developing on a workstation — recipes use Docker / Podman
  directly.

```bash
salloc --nodes=1 --qos=interactive --constraint=gpu --time=01:00:00 --account=m4031_g
# now in a shell on a compute node
lc run --local           # bypass any project-default pilot
```

## See also

- [Pilots](pilots.md) — pilot YAML schema, sbatch rendering, lifecycle
- [Site Registry](site-registry.md) — site defaults and how to add new sites
- [Container Builds](containers.md) — `podman-hpc` build and migrate workflow
