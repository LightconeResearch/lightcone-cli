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

=== "NERSC Perlmutter"
    NERSC doesn't ship `uv`, but it installs into your home dir with a
    single curl:

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    uv python install 3.12
    ```

    Both `uv` and an isolated Python 3.12 land under `~/.local/`.
    Make sure `~/.local/bin` is on your `PATH`.

    ??? note "Alternative: NERSC's `python` module"
        `module load python` gives you a ready-to-use distribution with
        `conda`, `pip`, and many scientific packages already installed:

        ```bash
        module load python      # NERSC Python (3.11+)
        ```

        Convenient, but the module is shared and read-only. For custom
        packages, build a conda env on top:

        ```bash
        conda create -n your-env-name python=3.11 -y
        conda activate your-env-name
        ```

        This is NERSC's [recommended path for `pip install`](https://docs.nersc.gov/development/languages/python/nersc-python/)
        when you need custom packages.

    !!! warning "Storage: 40 GB home quota"
        Conda envs land under `~/.conda/envs/` by default. The
        Perlmutter home quota is **40 GB**, which gets eaten quickly.
        NERSC recommends `/global/common/software/<project>/` for
        larger envs. If you want them on `$SCRATCH` (note: 12-week
        purge), move and symlink:

        ```bash
        conda deactivate
        mv ~/.conda/envs/your-env-name $SCRATCH/conda-envs/
        ln -s $SCRATCH/conda-envs/your-env-name ~/.conda/envs/your-env-name
        ```

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

=== "NERSC Perlmutter"
    With `uv` (recommended — isolates `lc` under `~/.local/share/uv/tools/`):

    ```bash
    uv tool install lightcone-cli
    ```

    With pip, the exact command depends on which Python you're using:

    ```bash
    # NERSC python module
    module load python
    python -m pip install --user lightcone-cli   # lands in ~/.local/bin/

    # Conda env
    conda activate your-env-name
    python -m pip install lightcone-cli
    ```

    `astra-tools` is a transitive dependency — pulled in automatically.

    ??? note "From source (contributors only)"
        ```bash
        git clone https://github.com/LightconeResearch/lightcone-cli.git
        uv pip install -e ./lightcone-cli
        ```

        To also hack on `astra-tools`:

        ```bash
        git clone https://github.com/LightconeResearch/ASTRA.git
        uv pip install -e ./ASTRA
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

Most of your interactions with a lightcone project happen *through* an
agent-based CLI. Any agent that can drive a project shell works — the
choice is yours.

=== "Claude Code"
    ```bash
    curl -fsSL https://claude.ai/install.sh | bash
    ```

    Make sure `~/.local/bin` is on your `PATH`, then verify and
    authenticate:

    ```bash
    claude --version
    claude        # first run prompts for login (claude.ai or API key)
    ```

    Other install routes (npm, native package managers) are documented
    in the [Claude Code installation docs](https://docs.claude.com/en/docs/claude-code/setup).

=== "OpenAI Codex"
    See the [openai/codex](https://github.com/openai/codex) repo README
    for install options.

=== "opencode"
    ```bash
    curl -fsSL https://opencode.ai/install | bash
    ```

Open a project in your terminal or editor (see [Getting Started](getting-started.md)) and run your agent CLI from inside it. Inside Claude Code you'll type slash commands like `/lc-new`,
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

## Updating

=== "uv tool"
    ```bash
    uv tool upgrade lightcone-cli
    ```

=== "pip"
    ```bash
    pip install -U lightcone-cli astra-tools
    ```

=== "Source"
    ```bash
    cd path/to/lightcone-cli
    git pull
    uv pip install -e .        # only needed if pyproject.toml changed
    ```

    Editable installs auto-follow source edits — switching branches or
    pulling new commits is reflected immediately in `lc`. Re-install
    only when `pyproject.toml` adds a new dependency.

## Uninstalling

=== "uv tool"
    ```bash
    uv tool uninstall lightcone-cli
    ```

=== "pip"
    ```bash
    pip uninstall lightcone-cli
    ```

!!! note "Keep your config?"
    `~/.lightcone/config.yaml` survives the uninstall. Delete it too
    if you want a clean slate.
