# Container Builds for HPC

HPC nodes generally cannot reach a docker daemon, so lightcone-cli ships
support for `podman-hpc` (NERSC Perlmutter and friends). The build/migrate
workflow is owned by `lightcone.engine.container`.

## podman-hpc workflow

`podman-hpc` is rootless and HPC-aware. After a build, the image must be
*migrated* into the per-node container cache so compute nodes can read it
without a registry.

```bash
# On a login node, with podman-hpc on PATH:
lc setup                              # writes ~/.lightcone/config.yaml
$EDITOR ~/.lightcone/config.yaml      # set container.runtime: podman-hpc
lc build                              # builds + migrates each image
```

`lc build` checks for cached tags and skips rebuilds. Use `--force` to
rebuild everything.

## Tag computation

Tags are content-addressed:

```
lc-<sanitized-project-name>-<sha256[:12]>
```

The hash covers the Containerfile contents plus any of these dependency
files found at the project root:
`requirements.txt`, `requirements-dev.txt`, `requirements-test.txt`,
`pyproject.toml`, `setup.py`, `setup.cfg`, `poetry.lock`, `Pipfile.lock`.

## At run time

`lc run` does **not** re-shell into Snakemake's `container:` directive or
`--sdm apptainer`. The Snakefile generator wraps each rule's recipe in:

```bash
podman-hpc run --rm --pull=never -v "$PWD":"$PWD" -w "$PWD" \
  <image> bash -c '<recipe>'
```

`--pull=never` is critical: short-name resolution would otherwise try
`unqualified-search-registries` for tags like `lc-myproject-abc123` and
fail. Pre-pulling registry images via `lc build` (or pre-staging
Containerfile images via `lc build`) is therefore mandatory.

See also: [api/container](../api/container.md) for the implementation,
and [`lc build`](../cli/build.md) for the user-facing command.
