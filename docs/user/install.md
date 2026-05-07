# Install

You need three things on your machine: Python 3.11+, the `lc` CLI, and
Claude Code. A container runtime is optional but recommended.

## 1. Python 3.11+

If you don't already have a recent Python:

- macOS: `brew install python@3.12`
- Linux: your package manager (`apt install python3.12`, etc.) or
  [pyenv](https://github.com/pyenv/pyenv).
- Windows: [python.org](https://www.python.org/downloads/) or WSL.

Confirm:

```bash
python3 --version           # → Python 3.11.x or newer
```

## 2. lightcone-cli

The published name on PyPI is `lightcone-cli`; the command it provides
is `lc`.

```bash
pip install lightcone-cli
```

If you use [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv pip install lightcone-cli
# or, project-local:
uv tool install lightcone-cli
```

Confirm:

```bash
lc --version                # → lightcone-cli, version ...
```

> **Heads-up about the `lc` name.** `lc` is not a standard Unix tool,
> but a few people have a personal shell alias `lc='ls --color'`. If
> that's you, installing lightcone-cli will shadow the alias —
> rebind it (e.g. `alias l='ls --color'`).

## 3. One-time setup

```bash
lc setup
```

This creates `~/.lightcone/config.yaml` with:

```yaml
container:
  runtime: auto
```

`auto` detects whichever of `podman`, `docker`, or `podman-hpc` is on
your PATH (and skips docker if its daemon isn't running). You can pin
the runtime later by editing this file.

## 4. Claude Code

Most of your interactions with lightcone-cli happen *through* Claude
Code, the CLI that drives Claude.

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Open a project (in the next page we make one) with:

```bash
claude
```

Inside Claude Code you'll type slash commands like `/lc-new` and
`/lc-build` — see [The Claude Code Workflow](claude-workflow.md).

## 5. (Optional) Docker or Podman

If your analysis declares a `container:` (which it usually should — it
makes the result reproducible across machines), you need a container
runtime:

- Local laptop: install [Podman](https://podman.io/) (rootless, no
  daemon) or [Docker](https://docs.docker.com/get-docker/).
- HPC login node: see [Running on a Cluster](cluster.md).

`lc setup`'s `auto` mode picks whichever you have. If you don't have
either, you can still use `lc` — set `runtime: none` in
`~/.lightcone/config.yaml` and recipes will run on the host without
isolation.

## Sanity check

```bash
lc --help
lc init --help
```

Both should print help text. If `lc` is shadowed by an `ls` alias,
unset it (`unalias lc`) or use the full path
(`$(which lc) --version`).
