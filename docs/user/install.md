# Install

To get started on a lightcone project, you need three things on your machine: Python 3.11+, the lightcone command line tool `lc`, and
an agent-based CLI (currently supporting Claude Code).  
A container runtime is optional but recommended.

## 1. Python

If you don't already have a recent Python

=== "macOS"
    ```bash
    brew install python@3.12
    ```

=== "Linux"
    Your package manager (`apt install python3.12`, etc.) or
    [pyenv](https://github.com/pyenv/pyenv)

=== "Windows"
    [python.org](https://www.python.org/downloads/) or WSL

!!! tip "Recommendation"
    We highly recommend the use of [uv](https://docs.astral.sh/uv/) to manage Python installation and virtual environments.

    `uv` can be installed in a single commandline

        curl -LsSf https://astral.sh/uv/install.sh | sh

    and a subsequent version of Python

        uv python install 3.12

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

Get a confirmation of the proper installation by running

    lc --version                # → lightcone-cli, version ...

> **Note** Some people may have already set a personal shell alias `lc='ls --color'`. If that's you, installing lightcone-cli will shadow the alias — make sure to rebind it (e.g. `alias l='ls --color'`).

## 3. Global configuration

`~/.lightcone/config.yaml` is created automatically the first time you
run any `lc` command. No manual setup step is needed. The file starts
as:

```yaml
container:
  runtime: auto
```

`auto` detects whichever of `podman`, `docker`, or `podman-hpc` is on
your PATH (and skips docker if its daemon isn't running). Feel free to pin the runtime later by editing this file directly.

## 4. Agentic CLI

Most of your interactions with a lightcone project happen *through* an agent-based CLI, for now we are supporting Claude Code.

Install Claude Code

    curl -fsSL https://claude.ai/install.sh | bash

Open a project in your terminal or editor (see [Getting Started](getting-started.md)) and run

    claude

Inside Claude Code you'll type slash commands like `/lc-new`,
`/lc-from-code`, and `/lc-from-paper` — see
[The Agentic Workflow](agent-workflow.md).

## 5. (Optional) Docker or Podman

If your analysis declares a `container:` (which it usually should — it
makes the result reproducible across machines), you need a container
runtime:

- Local laptop: install [Podman](https://podman.io/) (rootless, no
  daemon) or [Docker](https://docs.docker.com/get-docker/).
- HPC login node: see [Running on a Cluster](cluster.md).

The `auto` mode picks whichever container runtime you have. If you don't
have either, you can still use `lc` — set `runtime: none` in
`~/.lightcone/config.yaml` and recipes will run on the host without
isolation.

## Sanity check

    lc --help
    lc init --help

Both should print help text. If `lc` is shadowed by an `ls` alias,
unset it (`unalias lc`) or use the full path
(`$(which lc) --version`).
