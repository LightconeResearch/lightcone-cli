# Parsl pilot model for SLURM targets

`lc run --target <slurm-target>` acquires a single SLURM allocation
(the **pilot**) once at the start, then dispatches every recipe in the
analysis tree into that pool. The queue wait is paid once per `lc run`,
not once per recipe.

## Target schema

A SLURM target declares one or more pilots. Each pilot becomes a
`SlurmProvider` + `WorkQueueExecutor` in the underlying Parsl config.

```yaml
backend: slurm
site: perlmutter
container_runtime: podman-hpc
pilots:
  cpu:
    nodes: 4
    walltime: 2h
    qos: debug
    account: m1234
    worker_init: |
      module load python
      source $HOME/.lightcone/envs/perlmutter/bin/activate
  gpu:
    nodes: 2
    walltime: 1h
    qos: debug
    constraint: gpu
    account: m1234_g
    worker_init: |
      module load python cudatoolkit
      source $HOME/.lightcone/envs/perlmutter-gpu/bin/activate
```

Recognized pilot keys: `nodes`, `walltime`, `qos`, `account`,
`partition`, `constraint`, `worker_init`, `scheduler_options`,
`exclusive` (default `True`).

## Routing

A recipe is dispatched to a pilot based on its `resources`:

1. `resources.nodes > 1` and `mpi` pilot exists → `mpi`
2. `resources.gpus > 0` and `gpu` pilot exists → `gpu`
3. otherwise → `cpu`

A GPU recipe with no `gpu` pilot configured raises immediately at
dispatch time — better to fail fast than dispatch to a CPU allocation
that can't satisfy the request.

## `worker_init` essentials

Workers run on compute nodes, not the login node. They need the
project's Python environment available before tasks can run. Typical
`worker_init`:

```yaml
worker_init: |
  module load python
  source $HOME/.lightcone/envs/perlmutter/bin/activate
```

Anything in `worker_init` runs once per pilot, before tasks dispatch.

## Installing the WorkQueue dependency

WorkQueue's Python bindings come from the `ndcctools` conda package:

```bash
conda install -c conda-forge ndcctools
```

Without it, `lc run --target <slurm-target>` raises a clear error.

## Migrating from the old per-recipe SLURM backend

Old target shape (pre-2026-04):

```yaml
backend: slurm
scheduler:
  account: m1234
  qos: debug
options:
  qos: {choices: [debug, regular], default: debug}
```

New shape:

```yaml
backend: slurm
pilots:
  cpu:
    nodes: 4
    walltime: 2h
    account: m1234
    qos: debug
options:
  qos: {choices: [debug, regular], default: debug}
```

`scheduler:` is gone. The `nodes` and `walltime` that used to live
on individual recipes now describe the pilot's compute budget for
the whole `lc run`. Recipe-level `resources.cpus`/`memory`/`gpus`
still control per-task bin-packing inside the pilot.
