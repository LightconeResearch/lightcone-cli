# lightcone.engine.targets (removed)

The target configuration module is gone. The only remaining global config
is `~/.lightcone/config.yaml`, which today carries one key:

```yaml
container:
  runtime: auto   # auto | docker | podman | podman-hpc | none
```

It is read by [`lightcone.engine.container.load_runtime`](container.md).
