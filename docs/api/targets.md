# lightcone.engine.targets (removed)

The target configuration module is gone. The remaining global config is
`~/.lightcone/config.yaml`:

```yaml
container:
  runtime: auto   # auto | docker | podman | podman-hpc | none
slurm:
  account: null
  time_padding: 1.5
```

It is read by [`lightcone.engine.container.load_runtime`](container.md) and
[`lightcone.engine.async_jobs`](async_jobs.md).
