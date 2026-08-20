# Install

To work on a lightcone project you need two things on your machine:
[uv](https://docs.astral.sh/uv/) and git. Everything else — Python
itself, git-annex, the `astra` spec tooling — is installed by uv or
ships with `lc`.

!!! note "Supported platforms"
    Linux (glibc 2.34+, x86_64 or aarch64) and macOS (14+ on Apple
    silicon, 15+ on Intel). git-annex ships as a binary wheel inside
    the install, and its platforms are the floor: below it, the install
    fails cleanly rather than half-working. On Windows, use WSL.

## 1. uv and git

`lc` uses uv as its only environment substrate — projects are
`pyproject.toml` + `uv.lock`, and uv manages the Python interpreters
too, so there is no separate Python install step.

=== "macOS / Linux"
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    git is preinstalled on macOS; on Linux use your package manager
    (`apt install git`, `dnf install git`, …).

=== "NERSC Perlmutter"
    NERSC doesn't ship `uv`, but it installs into your home directory
    with a single curl:

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    `uv` lands under `~/.local/bin` — make sure it's on your `PATH`.
    git is already on the system.

## 2. lightcone-cli

The published name on PyPI is `lightcone-cli`; the command it provides
is `lc`.

=== "uv"
    ```bash
    uv tool install lightcone-cli
    ```

=== "pip"
    ```bash
    python -m pip install lightcone-cli
    ```

The install also puts `git-annex` on your `PATH` — `lc` versions every
result in the project's own git repository, and git-annex is what
carries the data bytes. You never run it by hand, but git dispatches to
it, so it has to be installed; the wheel takes care of that.

`astra-tools` is a dependency, so the `astra` CLI (spec validation,
universe management) arrives with it.

Get a confirmation of the proper installation by running

    lc --version                # → lc, version ...
    git annex version           # → git-annex version: ...

> **Note** Some people may have already set a personal shell alias
> `lc='ls --color'`. If that's you, installing lightcone-cli will shadow
> the alias — make sure to rebind it (e.g. `alias l='ls --color'`).

## 3. Tell git who you are

Every output `lc` makes is committed, so git needs an identity before
the first build — `lc materialize` checks up front rather than failing
after your recipes have run:

```bash
git config --global user.name "Ada Lovelace"
git config --global user.email "ada@example.org"
```

If you already commit from this machine, you're done.

## 4. (Optional) Podman or Docker

Only *containerized* projects need a container runtime — a project opts
in by declaring `[tool.lightcone.image]` in its `pyproject.toml`, and
until it does, recipes run directly on your machine in the project's
own locked environment.

- Local machine: install [Podman](https://podman.io/) (rootless, no
  daemon) or [Docker](https://docs.docker.com/get-docker/).
- HPC login node: see [Running on a Cluster](cluster.md).

There is nothing to configure: `lc` detects whichever runtime is
available (`podman-hpc`, then `podman`, then `docker` — skipping docker
if its daemon isn't running).

## Sanity check

    lc --help
    lc init --help

Both should print help text. If `lc` is shadowed by an `ls` alias,
unset it (`unalias lc`) or use the full path (`$(which lc) --version`).

## Updating

=== "uv tool"
    ```bash
    uv tool upgrade lightcone-cli
    ```

=== "pip"
    ```bash
    pip install -U lightcone-cli
    ```

An upgrade never invalidates your results: the engine's version is
recorded in every output's manifest, but it is not part of any output's
identity, so nothing gets rebuilt just because `lc` moved.

## Uninstalling

=== "uv tool"
    ```bash
    uv tool uninstall lightcone-cli
    ```

=== "pip"
    ```bash
    pip uninstall lightcone-cli
    ```

Your projects are untouched — everything `lc` knows about an analysis
lives in the project's own repository, not in any global state.
