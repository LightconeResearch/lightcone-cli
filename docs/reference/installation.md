# Installation

Lightcone has three parts. The **agent plugin** teaches your coding
agent to scope and drive an analysis. The **`lc` CLI** runs it and
records what produced every result. **Lightcone Lab** is an optional
JupyterLab extension for browsing a project. Install the plugin first,
then `lc`; add Lab if you want it.

!!! note "Supported platforms"
    Linux (glibc 2.34+, x86_64 or aarch64) and macOS (14+ on Apple
    silicon, 15+ on Intel). On Windows, use WSL.

## Before you start: uv and git

The plugin needs [uv](https://docs.astral.sh/uv/); `lc` needs uv and
git. `lc` uses uv as its only environment substrate — projects are
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

## 1. The agent plugin

This is the recommended way to work: your agent scopes the analysis
with you, writes `astra.yaml` and the scripts, and drives `lc`. The
plugin is called **`lightcone`**, and it works in Claude Code and
Codex. It bundles everything the agent needs, so it is the only plugin
to install.

=== "Claude Code"

    ```bash
    claude plugin marketplace add LightconeResearch/agent-skills
    claude plugin install lightcone@lightcone-research
    ```

    Then invoke `/lightcone:lightcone` in a session.

=== "Claude App"

    Go to **Customize → Plugins**, click **Add**, then choose
    **Add marketplace → Add from repo**. Paste
    `https://github.com/LightconeResearch/agent-skills`, pick `lightcone`,
    and call it with `/lightcone:lightcone`.

=== "Codex CLI"

    ```bash
    codex plugin marketplace add LightconeResearch/agent-skills
    codex plugin add lightcone@lightcone-research
    ```

    Then invoke the skill in Codex, for example `$lightcone:lightcone`.

=== "Codex App"

    Click the arrow beside **Create** and open **Plugins**. Install the
    `LightconeResearch/agent-skills` marketplace, search for `lightcone`,
    and install it. Then call `/lightcone:lightcone`.

You rarely need to type the invocation: the agent loads the skill on
its own when you ask to start, run or resume an analysis, or when the
directory holds an `astra.yaml`.

**The plugin does not install `lc`.** At the start of every session it
checks that `lc` is on your `PATH` and recent enough. If it isn't, the
agent tells you and asks whether to install or upgrade it, and waits
for your answer; only in a headless session, with no one to ask, does
it install `lc` on its own and say so. You can let the agent do it, or
install `lc` yourself with the next section.
[Agent plugin](plugin.md) in the Reference describes
everything the plugin ships.

## 2. The `lc` CLI

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

    lc --version                # → lc, version ...

> **Note** Some people may have already set a personal shell alias
> `lc='ls --color'`. If that's you, installing lightcone-cli will shadow
> the alias — make sure to rebind it (e.g. `alias l='ls --color'`).
> [Troubleshooting](troubleshooting.md) has more on a
> shadowed `lc`.

### Tell git who you are

Every output `lc` makes is committed, so git needs an identity before
the first build — `lc materialize` checks up front rather than failing
after your recipes have run:

```bash
git config --global user.name "Ada Lovelace"
git config --global user.email "ada@example.org"
```

If you already commit from this machine, you're done.

### (Optional) Podman or Docker

Only *containerized* projects need a container runtime — a project opts
in by declaring `[tool.lightcone.image]` in its `pyproject.toml`, and
until it does, recipes run directly on your machine in the project's
own locked environment.

- Local machine: install [Podman](https://podman.io/) (rootless, no
  daemon) or [Docker](https://docs.docker.com/get-docker/).
- HPC login node: see [Run on a cluster](../guides/cluster.md).

There is nothing to configure: `lc` detects whichever runtime is
available (`podman-hpc`, then `podman`, then `docker` — skipping docker
if its daemon isn't running).

### Sanity check

    lc --help
    lc init --help

Both should print help text. If `lc` is shadowed by an `ls` alias,
unset it (`unalias lc`) or use the full path (`$(which lc) --version`).
The [`lc` CLI reference](cli/index.md) covers every verb.

### Updating

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

### Uninstalling

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

## 3. Lightcone Lab

Lightcone Lab is a JupyterLab extension for browsing a project: its
outputs, decisions, inputs and cited papers, with each output's state
and provenance. It needs Python 3.11+ and JupyterLab 4.5.10 or newer
(below 5), and uv 0.12 or newer: an older uv resolves an old release of
the extension without telling you.

=== "New to JupyterLab"

    Install JupyterLab with the extension as one uv tool. This also puts
    `lc` on your `PATH`:

    ```bash
    uv tool install jupyterlab --with jupyterlab-lightcone --with-executables-from jupyter-core,lightcone-cli
    jupyter lab
    ```

    If you have already installed `lc` on its own (section 2), leave
    `lightcone-cli` out of `--with-executables-from`: uv refuses to
    replace an executable another tool installed.

=== "Your own JupyterLab"

    Install the extension into the environment JupyterLab runs from,
    then restart JupyterLab:

    ```bash
    pip install jupyterlab-lightcone
    ```

The extension brings `lightcone-cli` with it as a dependency and calls
the engine directly, so `lc` does not have to be on the Jupyter server's
`PATH`; creating a project from Lab does need `uv` and `git` there. To
confirm it loaded:

```bash
jupyter labextension list
```

[Browse a project in Lightcone Lab](../guides/lab.md) shows what to do
with it.

## Next

[Quickstart](../get-started/quickstart.md): set up Lightcone and start a
project.
